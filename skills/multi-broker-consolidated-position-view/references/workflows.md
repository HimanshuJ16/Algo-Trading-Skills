# Deep Workflow Reference — multi-broker-consolidated-position-view

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Raw position ingestion

Build one `RawBrokerPosition` per leg from every broker adapter:
`broker_name`, `broker_symbol`, signed `quantity`, `average_cost`, `current_price`,
`currency`, `contract_multiplier`, `average_cost_includes_multiplier`, `as_of`.

Validation runs on construction. A NaN or infinite number, a negative price, a blank
identifier, a non-positive multiplier, a malformed currency code, or a naive
(timezone-less) timestamp raises immediately, so a malformed feed row cannot reach an
aggregate. A zero price is accepted — a worthless expiring option legitimately marks
at zero.

Two per-adapter determinations must be made once and encoded, not guessed per call:

- **The contract multiplier.** Mandatory for derivatives, unavailable from the ticker.
  Standard OCC equity options cover 100 shares, but adjusted contracts can deliver
  another amount while keeping the 100 premium multiplier, so read it from the contract
  definition. `1.0` is correct only for cash equities, spot FX, and spot crypto.
- **Whether the broker's cost field already embeds the multiplier.** IBKR's `avgCost`
  does for derivatives; its `avgPrice` does not. Set
  `average_cost_includes_multiplier` accordingly. The failure is silent in both
  directions: applying the multiplier twice on a 50× contract turns an $840,000 cost
  basis into $42,000,000; omitting it when required understates cost by the same factor.

Note that one broker can legitimately contribute two legs for one symbol — Binance
USD-M in hedge mode returns separate LONG and SHORT rows for a single account. Ingest
both; they net.

### 2. Symbol normalization

Resolve `broker_symbol` to a canonical symbol via the registered map.

Run with `strict_symbol_mapping=True` in production. The non-strict fallback
upper-cases the raw ticker, which quietly produces two canonical entries for one
asset (`AAPL` from IBKR, `AAPL.US` from an unmapped Alpaca feed): offsetting legs
never meet, netting is understated, and the same exposure is counted twice. In
non-strict mode, call `unmapped_broker_symbols(positions)` and treat a non-empty
result as a blocking condition before consuming the view.

### 3. Currency conversion to the base currency

`fx_rates[c]` is defined as **units of base currency per one unit of `c`**, and must
contain the base currency itself at exactly 1.0. There is no default table: a rate
literal is stale the moment it is committed, and a caller who forgets to supply rates
would otherwise receive a confidently wrong exposure figure instead of an error.

A currency absent from the table raises `MissingFxRateError`. It is never assumed to
be 1:1 — that assumption misstates the position by the full size of the exchange rate,
and for a currency like JPY that is more than two orders of magnitude.

Rates are validated on entry: NaN, infinite, zero, and negative rates are rejected,
since each would silently zero out or sign-flip a position's market value.

### 4. Snapshot age control

Brokers are polled independently, so their snapshots are not simultaneous. Set
`max_snapshot_age` and pass `valuation_time` to `consolidate_positions()`:

- every leg must carry an `as_of`, or consolidation raises;
- a leg older than the limit raises;
- a leg stamped *ahead* of `valuation_time` raises, because clock skew between the
  adapter host and this process invalidates the age check itself;
- the FX table is age-checked too when `fx_rates_as_of` is set.

`snapshot_skew_seconds` reports the spread between the oldest and newest leg for a
symbol, and is withheld (`None`) unless every leg is timestamped — a skew computed
over a subset understates how far apart the snapshots really are.

### 5. Consolidation and netting

Per canonical symbol, with sums accumulated via `math.fsum` to limit floating-point
drift across many legs:

- $Q_{\text{net}} = \sum_b Q_b$ and $Q_{\text{gross}} = \sum_b |Q_b|$;
- signed net market value, and **gross market value** $\sum_b |MV_b|$;
- cost basis and unrealized P&L;
- `broker_breakdown` (net quantity per broker), `currencies`, `leg_count`;
- `is_internally_offset`, true when both a long and a short leg exist for the symbol.

Two output rules matter downstream:

- **Gross, not net, is the exposure figure.** A long-100 / short-100 book nets to
  roughly zero market value while consuming margin, borrow, and commission on both
  sides. A gross-market-value cap must read `gross_market_value_base`.
- **A flat net position has no average cost.** `weighted_avg_cost_base` is `None`
  rather than `0.0` when $|Q_{\text{net}}|$ is within tolerance, because the quotient
  diverges as the net approaches zero — long 100 against short 99.999999 would
  otherwise report a per-contract basis roughly $10^5$ times the real one. `None`
  says "not defined here"; `0.0` reads as "acquired for free".

Output is ordered by canonical symbol so consecutive risk snapshots diff cleanly and
audit records are reproducible.

### 6. Reconciliation audit

`reconcile_against_target()` compares the consolidated view against the strategy's
target ledger. Target keys are upper-cased before matching, so a lower-cased key does
not manufacture a phantom pair of breaks; duplicate keys differing only in case raise,
because the intended quantity is ambiguous.

Each break is classified, and the classes call for different responses:

| Kind | Meaning | Typical cause |
|---|---|---|
| `QUANTITY_MISMATCH` | Both sides hold the symbol, sizes differ | Partial fill, missed fill, unreported amendment |
| `MISSING_AT_BROKER` | Target expects a position no broker reports | Unfilled order, or a broker-side forced close-out |
| `UNEXPECTED_AT_BROKER` | A broker holds something the target never intended | Rogue/manual fill — **or a symbol-mapping failure creating a phantom symbol.** Rule out the mapping first |

Tolerance is absolute and defaults to `1e-5`. That suits share quantities and is far
too coarse for 8-decimal crypto: on a 0.00001 BTC target it silently accepts a 50%
position error. Use `symbol_tolerances` per instrument.

Two tolerance subtleties:

- The comparison is **inclusive** at the boundary (`> tolerance` is a break).
- A tolerance written as a round decimal is not round in binary floating point.
  `100.01 - 100.0` evaluates to `0.010000000000005`, which exceeds a `0.01`
  tolerance — set tolerances with headroom rather than at the exact quantity
  increment you expect, or a purely representational residue alerts as a break every
  reconciliation cycle.

Break messages format quantities to 12 significant digits. Two decimals renders a
5e-08 BTC discrepancy as `+0.00`, and 8 significant digits renders both sides of it
identically as `1` — either way the alert reads as a non-event.

## Fail-closed summary

| Condition | Behavior |
|---|---|
| `fx_rates` not supplied | `ValueError` at construction |
| FX table missing the base currency, or holding it at ≠ 1.0 | `ValueError` at construction |
| FX rate NaN / infinite / ≤ 0 | `ValueError` |
| Position currency absent from the table | `MissingFxRateError` |
| Unmapped symbol, strict mode | `UnmappedSymbolError` |
| Leg or FX table older than `max_snapshot_age` | `StaleSnapshotError` |
| Leg missing `as_of` while `max_snapshot_age` is set | `StaleSnapshotError` |
| Leg stamped ahead of `valuation_time` | `StaleSnapshotError` |
| NaN/infinite number, negative price, non-positive multiplier, blank identifier, malformed currency, naive timestamp | `ValueError` at construction |
| Duplicate or non-finite target-ledger entries | `ValueError` |

## Production Implementation Reference

- Reference code: `scripts/consolidated_ledger.py`
  (`MultiBrokerConsolidatedLedger`, `RawBrokerPosition`, `ConsolidatedPosition`,
  `ReconciliationDiscrepancy`, `DiscrepancyKind`, `MissingFxRateError`,
  `StaleSnapshotError`, `UnmappedSymbolError`).
- Automated unit tests: `scripts/test_consolidated_ledger.py`.
