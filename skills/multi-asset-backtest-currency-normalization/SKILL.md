---
name: multi-asset-backtest-currency-normalization
description: Use when backtesting global multi-asset portfolios to convert multi-currency
  cash flows, position valuations, and FX conversion rates into a single unified reporting
  currency without currency mixing errors
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- multi-currency
- fx-conversion
- portfolio-accounting
- currency-normalization
- nav-attribution
brokers_frameworks:
- Interactive Brokers Multi-Currency
- Backtrader Multi-Asset
- VectorBT FX
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever backtesting or reporting on a portfolio holding assets denominated in more than one currency (e.g. US stocks in `USD`, European stocks in `EUR`, Japanese stocks in `JPY`, Indian stocks in `INR`). Directly summing unconverted P&L across currencies produces catastrophic accounting distortions — treating 10,000 JPY as equal to 10,000 USD — and the result still *looks* like a valid number. Point-in-time FX translation, per-currency cash ledger isolation, and a single reporting currency are mandatory before any aggregate figure (NAV, exposure, drawdown, Sharpe) means anything.

## When NOT to Use

- **As an FX execution model.** Everything here is *valuation* at mid rates: translating a balance does not move money. Actually converting cash crosses a bid/ask spread and usually pays a broker fee. Applying a spread to a pure valuation would understate NAV on every bar — model conversion cost in the execution layer, on the trades that actually convert.
- **As a books-and-records ledger.** Balances are IEEE-754 doubles with no per-currency minor-unit rounding (ISO 4217 defines a minor unit per currency) and no transaction journal. Fine for a backtest NAV series; not fine for a general ledger or a tax filing.
- **For crypto/stablecoin ledgers.** Currency codes are validated as three-letter ISO 4217 alphabetic codes. `USDT`, `USDC` and wrapped tokens are outside that standard and outside the fiat-translation semantics used here.
- **For intraday FX marking.** Rates key on a calendar date. If a strategy needs the 4pm London fix distinct from the Tokyo close, key on the fix, not the date.
- **When P&L must be split by trade rather than by currency.** This decomposes NAV *change* by currency, not per-position attribution — see `multi-currency-pnl-and-fx-conversion`.

## Prerequisites

- Multi-currency price data plus a point-in-time FX rate series covering **every currency held on every valuation date**.
- A deliberately chosen reporting currency, decided once and recorded — not inherited from whichever currency happens to dominate the codebase.
- Per-currency cash balances (a negative balance is a legitimate foreign-currency margin loan, not an error).
- An explicit decision on the rate quoting direction (below), applied uniformly across every rate source.

## Workflow

1. **Fix the rate convention before loading a single quote.**
   - This library stores `rate` = units of `to_currency` per **one** unit of `from_currency`, so conversion is always a multiplication: `register_fx_rate("EUR", "USD", d, 1.10)` means 1 EUR = 1.10 USD, and `register_fx_rate("USD", "JPY", d, 150.0)` means 1 USD = 150 JPY. This matches the market BASE/QUOTE reading of `EUR/USD` and `USD/JPY`.
   - **Decision point — check which direction your vendor publishes.** ECB euro reference rates are quoted against the euro as base (units of foreign currency per 1 EUR), so an ECB `USD` row is `EUR -> USD`, *not* `USD -> EUR`. Inverting this is silent: 10,000 EUR booked at $1/1.10$ instead of $1.10$ gives 9,091 USD — a plausible number that is wrong by 18%.
   - Register only the direction you actually have. The inverse is derived as $1/E$ at lookup time, and an explicitly registered quote always beats a derived inverse, so a real bid and a real offer can coexist without one silently overwriting the other.

2. **Initialize the multi-currency ledger.**
   - Set the reporting currency: `MultiCurrencyPortfolioNormalizer(reporting_currency="USD")`.
   - Keep one cash balance per currency (`{"USD": 50000, "EUR": 30000, "JPY": 1000000}`). This mirrors how brokers actually hold cash: an IBKR Universal Account keeps a balance per currency and translates only for statement presentation.

3. **Register point-in-time FX rates $E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$.**
   - **Decision point — decide the missing-rate policy explicitly.** Global books span mismatched calendars: the ECB publishes euro reference rates only on TARGET working days, so a portfolio valued on Good Friday has no same-day euro reference rate. Either register a rate for every valuation date, or set `max_staleness_days` to permit falling back to the most recent rate **on or before** the valuation date. The search is strictly backwards — a rate dated after the valuation date is never used, so the fallback cannot leak look-ahead into a backtest.
   - Every stale fallback is recorded in `fx_rate_dates_used` / `stale_fx_currencies` and logged, so a NAV computed on an old rate is auditable rather than invisible.

4. **Convert local valuations to the reporting currency.**
   $$\text{Value}_{\text{reporting}} = Q \cdot P_{\text{local}} \cdot E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$$

5. **Compute total NAV.**
   $$\text{NAV}_{\text{reporting}} = \sum_{c} \text{Cash}_c \cdot E(c \rightarrow \text{base}) + \sum_{i} \text{Position}_i \cdot E(c_i \rightarrow \text{base})$$
   - **Decision point — a missing rate must abort the valuation, not skip the currency.** A NAV that silently omits one currency is more dangerous than no NAV: it is still a plausible number, and it will be charted, compounded and reported as if it were complete.
   - Never sum a `*_local_by_currency` figure with a `*_reporting_*` figure. Local values are in each currency's own units and are not comparable across keys; only reporting-currency values may be added.

6. **Attribute the NAV change between two valuation dates.**
   - With local value $V$ and rate $E$, the change decomposes **exactly**:
     $$V_1E_1 - V_0E_0 = \underbrace{(V_1-V_0)E_0}_{\text{local / trading}} + \underbrace{V_0(E_1-E_0)}_{\text{FX translation}} + \underbrace{(V_1-V_0)(E_1-E_0)}_{\text{interaction}}$$
   - **Decision point — report the interaction term, don't absorb it.** A two-way local/FX split does not sum to the total; folding the residual into either bucket silently misattributes it. `attribute_nav_change()` returns all three.
   - **Decision point — attribution is not a substitute for a trade journal.** With snapshot-only inputs, deposits and withdrawals land in the local effect, and an intra-period currency conversion spreads across all three buckets, because two snapshots cannot reveal the rate at which the conversion executed. Net external flows and conversions out first.
   - This is the same separation IAS 21 para 28 requires (exchange differences on monetary items recognised in profit or loss) and that IBKR reports on its own "Cash FX Translation Gain/Loss" statement line.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-converted P&L addition**: adding JPY or EUR P&L straight into a USD cash balance. Guard structurally, not by discipline: tag every aggregate field with its unit so `cash_local_by_currency` can never be summed with `positions_reporting_by_currency` by accident.
- **Inverted rates**: applying $1/E$ where $E$ belongs. It never raises, never produces a NaN, and yields a number that survives every sanity check — verify the direction against a known pair (1 USD is ~150 JPY, not ~0.0067 JPY) before trusting a rate file.
- **A NaN rate that passes a positivity check**: `float('nan') <= 0` is `False`, so a naive `if rate <= 0: raise` guard admits NaN, and one NaN rate turns the entire NAV series into NaN. Validate with `math.isfinite`, not with a comparison.
- **`datetime` where a `date` is expected**: `datetime.datetime` is a subclass of `datetime.date`, so it satisfies every type hint and `isinstance` check while hashing to a different dictionary key — a rate stored under a timestamp is invisible to a date lookup, and the failure surfaces as "missing FX rate" on a date whose rate you know you loaded.
- **Unnormalized currency codes**: `"USD "` and `"usd"` become distinct ledger keys, quietly splitting one balance into two that never reconcile.
- **Auto-registering the reciprocal**: writing $1/E$ into the table at registration time means a later explicitly quoted opposite direction overwrites the original — register EUR→USD at 1.10, then USD→EUR at 0.90, and EUR→USD silently becomes 1.1111. Derive inverses at lookup instead.
- **Static FX rate assumption**: one constant rate across a multi-year backtest. Using today's rate to translate a historical position is the multi-currency instance of look-ahead bias.
- **Forward-filling a missing rate from the future**: filling a holiday gap with the *next* available rate injects tomorrow's information into today's NAV. Fill backwards only, bound the staleness, and record which date was actually used.
- **Ignoring FX conversion costs where they genuinely apply**: translation is at mid, but the moment a cash balance is actually converted a spread and a broker fee are paid. Charging the spread on translation understates NAV every bar; ignoring it on conversion overstates the strategy's return.
- **Silently dropping a currency with no rate**: a partial NAV is worse than a failed one.

## Verification

- Hold 50,000 USD, 30,000 EUR (EUR/USD = 1.10) and 1,000,000 JPY (USD/JPY = 150.0); confirm NAV = $50{,}000 + 33{,}000 + 6{,}666.67 = \$89{,}666.67$ and that the JPY leg is divided by 150, not multiplied.
- Confirm `register_fx_rate` rejects `nan`, `inf`, `0` and negative rates, and that `compute_total_nav` raises when a held currency has no usable rate for the valuation date.
- Confirm registering `USD -> EUR` at 0.90 after `EUR -> USD` at 1.10 leaves `EUR -> USD` at exactly 1.10.
- Confirm the staleness fallback accepts a Friday rate for a Monday valuation at `max_staleness_days=3`, records `stale_fx_currencies == {"EUR": 3}`, and **raises** when only a *later* rate exists (the look-ahead guard).
- Confirm attribution is exact: hold 10,000 EUR at 1.10, then 12,000 EUR at 1.20 — local 2,200, FX 1,000, interaction 200, total 3,400 — and that the three components reconstruct the total to floating-point precision on a book with several currencies, including a negative (loan) balance.
- Run `python scripts/test_currency_normalizer.py` and confirm 100% pass rate.

## Related Skills

- `multi-currency-pnl-and-fx-conversion`
- `currency-pair-quoting-convention-normalization`
- `multi-currency-var-aggregation`
- `lookahead-bias-elimination`
- `corporate-action-adjusted-backtesting`
