# Pre-Flight Checklist

## Inputs

- [ ] Is `pay_receive_type` set per swap (`PAY_FIXED` vs `RECEIVE_FIXED`), with `notional_usd` non-negative?
- [ ] Is `tenor_years` the **remaining** tenor, not the original tenor?
- [ ] Does `payment_frequency_per_year` match the contract (1 for USD SOFR fixed-vs-float, 2 for a legacy semi-annual fixed leg)?
- [ ] Does `floating_rate_index` match `currency`, and is every position USD?
- [ ] Is `bonds_dv01_usd` signed as P&L per +1 bps **rise** — negative for a long bond book?

## Calculation

- [ ] Is swap DV01 computed from the fixed-leg annuity, not a `tenor / 2` duration heuristic?
- [ ] Do Pay-Fixed positions come out positive and Receive-Fixed negative?
- [ ] Is DV01 expressed in USD per basis point, aggregated within a single curve only?

## Hedge

- [ ] Was `IrsHedgeSpec` supplied at the live par rate for the hedge tenor (`hedge_rate_is_default == False`)?
- [ ] Are you acting on `required_hedge_side` + `required_hedge_notional_abs_usd`, not the sign of the notional?
- [ ] Does booking the recommended hedge return net DV01 to zero on a re-run?

## Before trusting the number

- [ ] Is the shock small enough that ignoring convexity is acceptable (well under ~100 bps)?
- [ ] Have you accounted for what DV01 neutrality does not cover: curve twists, gross notional, and counterparty/CSA exposure?
