# Pre-Flight / Sign-off Checklist — multi-currency-pnl-and-fx-conversion

Use this before considering the skill's implementation complete.

## Tagging and reporting currency

- [ ] **Currency Tagging:** Every trade, position and P&L record carries an explicit currency, verified against the **schema** — a single-currency deployment passes a behavioural check trivially.
- [ ] **Base Currency:** The reporting currency is a deliberate, documented choice, not inherited from a default. (GIPS 2020 for Firms, 4.C.9, if a performance claim is made.)
- [ ] **Native Records Preserved:** Position-level P&L remains in its native currency for broker-statement reconciliation; nothing is overwritten at trade entry.

## Rate provider

- [ ] **Rate Direction:** `get_rate("USD", "JPY")` returns ≈150, not ≈0.0067. Tested on a pair quoted each way round.
- [ ] **No Parity Fallback:** Asking the provider for a pair it does not carry **raises**. A returned `1.0` is a defect.
- [ ] **No Hard-Coded Rates:** No undated constants anywhere in the provider path.
- [ ] **Rate Validity:** Zero, negative and NaN rates are rejected before use.
- [ ] **Triangulation:** Direct, inverse and pivot paths tested; pivot currency matches the book's actual crossing currency.

## Point-in-time discipline

- [ ] **As-Of Resolution:** Historical conversion uses the newest rate at or **before** each event's timestamp.
- [ ] **No Lookahead:** A request preceding the series raises rather than borrowing the first known rate.
- [ ] **Staleness Bound:** `max_staleness` is set to something the book's session structure justifies, and a stale rate raises.
- [ ] **`require_timestamp=True`** on every backtest resolver, so an untimestamped lookup fails instead of silently meaning "latest".
- [ ] **Timezone Consistency:** The rate series and all lookup timestamps are uniformly naive or uniformly aware (UTC recommended).
- [ ] **Backtest Differs:** A run against the historical series produces different results from the same run at one current rate, over a period with real FX movement. If they agree, point-in-time resolution is not wired in.

## Aggregation and precision

- [ ] **Convert Before Aggregating:** Every cross-currency exposure and risk check aggregates base-currency values, never raw notionals.
- [ ] **Round Once:** Aggregates sum at full precision and quantise once. A 1,000-leg aggregate in a 0-decimal base is checked explicitly.
- [ ] **No Dropped Legs:** A leg whose rate is unavailable fails the aggregate rather than being skipped.
- [ ] **ISO 4217 Minor Units:** KRW and JPY round to 0 decimals; KWD, BHD, OMR, JOD, TND, IQD, LYD to 3. Precision is not seeded from a payment-processor table.
- [ ] **Crypto Precision Registered** from venue instrument metadata, not from the module default.
- [ ] **Half-Up Rounding:** `round_amount(2.675, "USD")` is `2.68`, not `2.67`.

## Decomposition

- [ ] **Price vs FX Split:** `calculate_decomposed_pnl` separates the price effect from the FX translation effect.
- [ ] **Reconciliation:** `native_price_pnl + fx_translation_pnl == total_base_pnl` exactly, verified in a 0-decimal base currency.
- [ ] **Interaction Term Understood:** The team knows the cross term sits inside the FX leg under this convention, and `price_fx_interaction` has been compared against any external attribution system before the two are reported side by side.
- [ ] **Hedging Decision Informed:** FX translation P&L is reviewed separately when evaluating whether the strategy has an edge or is long a rising currency.

## Validation

- [ ] **Non-Exchangeable Currencies:** For any currency under capital controls in the traded universe, the team has confirmed the provider's rate is realisable (see IAS 21 *Lack of Exchangeability*, effective 1 Jan 2025) — the module cannot detect this.
- [ ] **Broker Reconciliation:** A sample of converted base-currency figures ties out against the broker's native-currency statement at each trade's own point-in-time rate.
- [ ] **Automated Testing:** `python -m unittest discover -s skills/multi-currency-pnl-and-fx-conversion/scripts` passes.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
