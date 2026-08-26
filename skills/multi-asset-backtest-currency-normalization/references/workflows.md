# Deep Workflow Reference — multi-asset-backtest-currency-normalization

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Pin the rate convention.**
   - Store $E(C_{\text{from}} \rightarrow C_{\text{to}}, T)$ as **units of `to` per one
     unit of `from`**, so conversion is always a multiplication. `EUR/USD = 1.10` is
     `register_fx_rate("EUR", "USD", d, 1.10)`; `USD/JPY = 150` is
     `register_fx_rate("USD", "JPY", d, 150.0)`.
   - Check the vendor's direction before loading (see `references/standards.md`). ECB
     reference rates quote the euro as base, so an ECB `USD` row is `EUR -> USD`.
   - Register one direction only. The inverse is derived at lookup as $1/E$; an
     explicitly registered quote takes precedence over a derived inverse, so a bid and
     an offer for the same pair coexist without either overwriting the other.
   - Re-registering the same direction and date with a different rate raises rather
     than overwriting: two sources disagreeing is a data defect, not a correction. Pass
     `allow_overwrite=True` when the replacement really is deliberate.

2. **Initialize the multi-currency ledger.**
   - `MultiCurrencyPortfolioNormalizer(reporting_currency="USD", max_staleness_days=0)`.
   - One cash balance per currency, held in that currency. Negative balances are
     legitimate: IBKR opens a margin loan in the traded currency rather than
     auto-converting the base currency.
   - Currency codes are normalized (`strip`, `upper`) and validated as three-letter
     ISO 4217 alphabetic codes.

3. **Register point-in-time FX rates.**
   - Rates key on `datetime.date`. `datetime.datetime` is rejected: it is a `date`
     subclass, so it passes every type check while hashing to a different key, and
     `.date()` on a tz-aware timestamp is timezone-dependent. Convert explicitly in the
     caller's valuation timezone.
   - Rates must be finite and strictly positive. Validate with `math.isfinite` —
     `float('nan') <= 0` is `False`, so a comparison-only guard admits NaN.

4. **Decide the missing-rate policy.**
   - Default (`max_staleness_days=0`): a rate must exist for exactly the valuation date,
     or the valuation raises.
   - Global books span mismatched calendars (the ECB publishes only on TARGET working
     days), so a bounded fallback is often required. With `max_staleness_days=n` the
     lookup accepts the most recent rate **on or before** the valuation date, within
     $n$ calendar days.
   - The search is strictly backwards. A rate dated after the valuation date is never
     used — forward-filling from the future would inject tomorrow's information into
     today's NAV, which is look-ahead bias in its purest form.
   - Every fallback is logged and recorded in `fx_rate_dates_used`; `stale_fx_currencies`
     reports each stale currency and the rate's age in days.

5. **Convert position valuations and cash balances.**
   $$\text{Value}_{\text{reporting}} = Q \cdot P_{\text{local}} \cdot E(C_{\text{local}} \rightarrow C_{\text{reporting}}, T)$$
   - Positions may be short ($Q < 0$) and prices may be negative (spreads;
     physically-settled crude in April 2020). Non-finite values are rejected.

6. **Compute total NAV.**
   $$\text{NAV}_{\text{reporting}} = \sum_{c} \text{Cash}_c \cdot E(c \rightarrow \text{base}) + \sum_{i} \text{Position}_i \cdot E(c_i \rightarrow \text{base})$$
   - A held currency with no usable rate aborts the valuation. Skipping it would return
     a plausible-looking partial NAV.
   - The snapshot exposes local and reporting breakdowns under separately tagged names
     (`cash_local_by_currency` vs `cash_reporting_by_currency`,
     `positions_local_by_currency` vs `positions_reporting_by_currency`) plus
     `positions_reporting_by_symbol`, which **accumulates** repeated symbols rather than
     letting the last lot overwrite the earlier ones. Only reporting-currency figures
     may be summed together.

7. **Attribute NAV change between two snapshots.**
   - For each currency, with local value $V$ and rate $E$:
     $$V_1E_1 - V_0E_0 = (V_1-V_0)E_0 + V_0(E_1-E_0) + (V_1-V_0)(E_1-E_0)$$
     i.e. local (trading) effect + FX translation effect + interaction effect. The
     identity is exact; all three are reported so the components always reconstruct the
     total.
   - `attribute_nav_change()` rejects reversed or equal dates and mismatched reporting
     currencies. A currency present in only one snapshot has its missing-side rate
     looked up from the rate table; a missing lookup raises rather than assuming an
     unchanged rate, which would book a real FX move as a trading gain.
   - With no cash-flow journal, deposits, withdrawals and dividends land in the local
     effect, and an inter-currency transfer inside the period spreads across all three
     buckets (two snapshots cannot reveal the rate at which the conversion executed).
     Net external flows and conversions out before reading these components as P&L.

## Failure Modes Observed in Production

- **Unconverted P&L summation:** local-currency P&L added straight into a base-currency
  balance. Prevented structurally by tagging every field with its unit.
- **Inverted rate application:** $1/E$ used where $E$ belongs. Silent, plausible, and
  wrong by the square of the deviation from parity.
- **NaN admitted by a positivity check:** `if rate <= 0` does not catch `nan`; one NaN
  rate turns an entire NAV series into NaN.
- **`datetime` used as a rate key:** stores fine, never matches a `date` lookup, and
  surfaces as "missing FX rate" for a date whose rate was definitely loaded.
- **Ghost ledgers from unnormalized codes:** `"USD "` and `"USD"` as separate balances.
- **Reciprocal auto-registration:** writing $1/E$ at registration time lets a later
  opposite-direction quote silently rewrite the original rate.
- **Fixed FX rate assumptions:** one constant rate across a multi-year backtest.
- **Forward-filled holiday rates:** a gap filled from the *next* available quote leaks
  future information into the valuation.
- **Symbol collision in reporting:** two lots of the same symbol overwriting each other
  in a per-symbol breakdown, so the total is right while the breakdown silently drops a
  leg.

## Production Implementation Reference

- Reference code: `scripts/currency_normalizer.py`
  (`MultiCurrencyPortfolioNormalizer`, `PositionValuation`, `MultiCurrencyNAV`,
  `CurrencyAttribution`, `NAVChangeAttribution`, `normalize_currency_code`).
- Automated unit tests: `scripts/test_currency_normalizer.py`.
- Standards, quoting conventions, IAS 21 paragraph references and broker behavior:
  `references/standards.md`.
