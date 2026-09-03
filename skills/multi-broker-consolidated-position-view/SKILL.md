---
name: multi-broker-consolidated-position-view
description: >-
  Use when positions live across several brokers and exchanges and risk limits need one
  netted base-currency view. Normalises symbol formats, converts currencies, and audits
  breaks against the strategy's own target ledger.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, multi-broker, consolidated-view, position-reconciliation, risk-accounting, netting
  brokers_frameworks: "Multi-Broker Ledger; Python Risk Engine; IBKR API; Alpaca Trading API; Binance USD-M Futures API"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill whenever a strategy distributes execution across multiple brokerage accounts or exchanges (e.g. US equities on IBKR and Alpaca, crypto on Binance). Isolated per-broker position views create blind spots in gross exposure and risk limits. This skill normalizes broker-specific symbol formats, converts multi-currency market values into a single base currency, nets long/short holdings across brokers, and audits position discrepancies against the strategy's own target ledger.

It is the **normalization layer**: `cross-account-aggregate-risk-view` explicitly delegates FX conversion, broker-symbol normalization, and multi-currency valuation to this skill and consumes canonical symbols and base-currency values from it. Numbers produced here therefore end up inside firm-wide gross-market-value caps, so this module fails closed rather than emitting a plausible-looking wrong figure.

## When NOT to Use

- **As a substitute for the broker's own books.** This is a derived view of snapshots you supply. The broker's statement is authoritative for settlement, margin and tax; a break found here means *investigate*, never *overwrite the broker*.
- **For lot-level cost basis, tax lots, or realized P&L.** `weighted_avg_cost_base` is an indicative net basis blended from brokers that compute cost basis under *different* rules, and it is undefined for a flat net position. Use `fifo-vs-specific-lot-tax-accounting-methods` and `cross-strategy-tax-lot-optimization` for anything an accountant or a tax authority will read.
- **For firm-wide limit enforcement, cash, or margin.** No cash balances, no margin utilization, no GMV cap. Feed this skill's output into `cross-account-aggregate-risk-view` (firm-wide caps) or `margin-utilization-circuit-breaker`.
- **To net opposing orders before routing.** This flags a position-level symptom (`is_internally_offset`); the execution-side fix belongs to `multi-order-netting-before-routing`.
- **As a live event-driven position tracker.** It consolidates point-in-time snapshots. It does not poll brokers, does not consume fill streams, and holds no position state between calls.
- **For options risk.** It values option positions with a supplied multiplier; it computes no Greeks and no exposure-equivalent delta — see `options-greeks-real-time-portfolio-aggregation`.

## Prerequisites

- Position snapshots from each broker adapter (`broker_name`, `broker_symbol`, signed `quantity`, `average_cost`, `current_price`, ISO 4217 `currency`, and `contract_multiplier` for anything that is not cash equity or spot).
- A symbol translation map (broker symbol → canonical symbol) covering **every** symbol traded, not just the ones that currently collide.
- An FX rate table expressed as **units of base currency per one unit of the quoted currency**, containing the base currency itself at exactly 1.0. There is no built-in default table — a hardcoded rate is stale the moment it is written.
- For each broker adapter, a determination of whether its reported cost field already embeds the contract multiplier (IBKR's `avgCost` does for derivatives; its `avgPrice` does not).

## Workflow

1. **Ingest Raw Broker Positions**:
   - Build a `RawBrokerPosition` per leg. Quantities are **signed** — negative is short, matching IBKR, Alpaca `qty`, and Binance `positionAmt`. Validation happens on construction, so a NaN price or a malformed currency code raises here rather than poisoning an aggregate.
   - **Decision point — is this instrument a derivative?** If yes, `contract_multiplier` is mandatory and cannot be inferred from the symbol. A standard OCC equity option covers 100 shares; corporate actions can leave an adjusted contract delivering something other than 100 shares while keeping a 100 premium multiplier. Read the multiplier from the contract definition, never from the ticker.
   - **Decision point — does this broker's cost field already include the multiplier?** Set `average_cost_includes_multiplier=True` if so. Getting this backwards on a 50× futures contract inflates cost basis 50-fold and reports a large loss on a winning position.

2. **Normalize Symbols**:
   - Resolve `broker_symbol` → canonical symbol.
   - **Decision point — what should an unmapped symbol do?** In production set `strict_symbol_mapping=True` so it raises. The default fallback (upper-case the raw ticker) is what silently turns `AAPL` and `AAPL.US` into two canonical symbols, halving apparent netting and double-counting the asset. If you must run non-strict, check `unmapped_broker_symbols()` before trusting the output.

3. **Convert to Base Currency**:
   - Every leg is multiplied by `fx_rates[currency]`. An unknown currency raises `MissingFxRateError` — it is never assumed to be 1:1.
   - **Decision point — how old are these snapshots?** Set `max_snapshot_age` and pass `valuation_time`. Legs and the FX table are then age-checked and a stale one raises. Consolidating a 40-minute-old crypto snapshot with a live equity snapshot produces a view that was never simultaneously true at any broker. `snapshot_skew_seconds` reports the spread across a symbol's legs when all of them are timestamped.

4. **Aggregate & Net Consolidated Position**:
   - Per canonical symbol: $Q_{\text{net}} = \sum_b Q_b$, $Q_{\text{gross}} = \sum_b |Q_b|$, signed net market value, gross market value $\sum_b |MV_b|$, cost basis, and unrealized P&L.
   - **Decision point — which value does the downstream limit consume?** A gross-market-value cap must read `gross_market_value_base`. The signed `total_market_value_base` collapses a long-100/short-100 book to roughly zero while both legs still consume margin and carry borrow cost.
   - `weighted_avg_cost_base` is `None` — not `0.0` — when the net position is flat within tolerance, because the quotient diverges as net quantity approaches zero.

5. **Reconciliation Audit**:
   - Compare the strategy's internal target ledger against consolidated broker holdings.
   - **Decision point — classify before reacting.** `QUANTITY_MISMATCH` (both sides hold it, sizes differ) usually means a partial or missed fill. `MISSING_AT_BROKER` means an expected position does not exist — an unfilled order, or a broker-side forced close-out. `UNEXPECTED_AT_BROKER` means a broker holds something the strategy never intended, which is *also* the signature of a symbol-mapping failure creating a phantom symbol. Rule out the mapping before treating it as a rogue fill.
   - **Decision point — is the tolerance right for this instrument?** The 1e-5 default suits share quantities and is far too coarse for an 8-decimal crypto quantity, where it silently accepts a 50% position error on a small holding. Set `symbol_tolerances` per instrument.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Defaulting an unknown currency to 1:1**: a currency missing from the rate table must fail, not pass through unconverted. A ¥10,000,000 position valued at 1:1 enters the firm-wide view as $10,000,000 instead of roughly $67,000 — a plausible-looking number that is wrong by more than two orders of magnitude, with no error anywhere.
- **Shipping a hardcoded default FX table**: a rate literal is stale the moment it is committed, and a caller who forgets to supply rates gets a confidently wrong exposure rather than a failure. Rates must be injected and timestamped.
- **Dropping the contract multiplier**: valuing an equity option at `quantity × price` understates it 100×. Twenty contracts at a $7.50 premium are $15,000 of exposure, not $150 — the difference between passing and breaching a gross-exposure cap.
- **Double-applying the multiplier**: IBKR's `avgCost` already reflects it for derivatives. Multiplying again turns a $840,000 cost basis into $42,000,000 and flips a $15,000 gain into a multi-million-dollar phantom loss.
- **Unmatched symbol mismatches**: failing to translate exchange-specific suffixes (`AAPL.US` vs `AAPL`) yields two canonical entries for one asset. The offsetting legs never meet, so netting is understated and the same economic exposure is counted twice.
- **Reporting net market value as exposure**: long 100 AAPL at Broker A and short 100 at Broker B nets to roughly zero market value while consuming margin, borrow, and commission on both sides. `gross_market_value_base` and `is_internally_offset` are what surface it.
- **Dividing cost basis by a near-zero net quantity**: a book that is long 100 and short 99.999999 has a net of 1e-6, and a naive average cost per contract in the millions. Treat a flat net position as having no defined average cost.
- **Consolidating snapshots taken minutes apart**: brokers are polled independently. Without an `as_of` per leg and a maximum age, a stale leg is indistinguishable from a live one, and the "point-in-time" view never existed at any point in time.
- **Setting a reconciliation tolerance at the exact expected increment**: a tolerance written as a round decimal is not round in binary floating point — `100.01 - 100.0` evaluates to `0.010000000000005`, which exceeds a `0.01` tolerance and alerts a purely representational residue as a position break every cycle. Leave headroom.
- **Formatting break alerts to two decimals**: a 5e-08 BTC discrepancy printed as `+0.00` reads as a non-event in the very alert raised to report it.
- **Treating the blended average cost as an accounting figure**: it mixes brokers computing cost basis under different rules — Alpaca alone uses weighted average intraday and compressed FIFO end-of-day. It is indicative only.

## Verification

- Consolidate two AAPL legs (IBKR long 500 @ $150 cost / $160 mark, Alpaca short 200 @ $155 / $160): verify `net_quantity` 300, `gross_quantity` 700, `total_market_value_base` $48,000, and `gross_market_value_base` $112,000 — the netted figure understates exposure by $64,000, and `is_internally_offset` must be true.
- Value 20 equity-option contracts at a $7.50 mark with `contract_multiplier=100`: verify $15,000, not $150. Value 3 futures at 5,700 index with `contract_multiplier=50` and `average_cost_includes_multiplier=True` against a 280,000 `avgCost`: verify $855,000 market value, $840,000 cost, $15,000 unrealized — re-applying the 50× would report $42,000,000 of cost.
- Convert a 40-share EUR position marked at €640 with a 1.25 USD-per-EUR rate: verify $32,000 (an inverted rate gives $20,480, a 1:1 fallback €25,600-as-dollars).
- Negative checks, each of which must raise: constructing the ledger with no `fx_rates`; an FX table missing the base currency or holding it at anything other than 1.0; a position in a currency absent from the table; a NaN or infinite quantity/cost/price; a negative price; a malformed currency code; a non-positive `contract_multiplier`; a naive (non-timezone-aware) `as_of`; a leg older than `max_snapshot_age`; a leg stamped ahead of `valuation_time`; an unmapped symbol under `strict_symbol_mapping`; duplicate target-ledger keys differing only in case.
- Verify a long 100 / short 99.999999 book reports `weighted_avg_cost_base is None` rather than a cost per share in the hundreds of thousands.
- Verify the three `DiscrepancyKind` values are produced by the three distinct situations, and that a 5e-06 BTC break is caught under `symbol_tolerances={"BTC": 1e-8}` while the 1e-5 default silently passes it.
- Run `python -m unittest discover -s skills/multi-broker-consolidated-position-view/scripts` and confirm 100% pass rate.

## Related Skills

- `cross-account-aggregate-risk-view`
- `multi-currency-pnl-and-fx-conversion`
- `multi-asset-backtest-currency-normalization`
- `reference-data-symbol-mapping-across-vendors`
- `multi-order-netting-before-routing`
- `broker-failover-secondary-account-routing`
- `correlation-aware-exposure-limits`
