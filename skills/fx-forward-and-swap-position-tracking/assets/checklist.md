# Pre-Flight Checklist

## Contract data
- [ ] Is `days_to_maturity` the **remaining** calendar days at today's valuation date, re-derived each run, rather than the original tenor?
- [ ] Is `notional_base_currency` positive and in the **base** currency, with direction carried only by `position_side`?
- [ ] Is `agreed_forward_rate` the all-in contracted outright (not forward points, not spot)?
- [ ] Does `currency_pair` equal `base_currency/quote_currency` for every row, so no rate is silently inverted?

## FX swaps
- [ ] Is every swap booked as **two rows** sharing one `contract_id`, one `NEAR` and one `FAR`?
- [ ] Do the two legs run in **opposite** directions, with the far leg maturing strictly after the near leg?
- [ ] Has the near leg's spot-window warning been reviewed rather than filtered out of the log?

## Day-count conventions
- [ ] Is the basis resolved **per currency**, with the pair's two legs allowed to differ (GBP/USD is Act/365 against Act/360)?
- [ ] Has JPY been confirmed as **Actual/365**, not the retired JPY LIBOR Actual/360?
- [ ] Have all `WARNING`-logged fallback currencies been given a verified basis via `day_count_basis`, sourced from the benchmark administrator?

## Pricing and marking
- [ ] Where an outright is quotable, is `market_forward_rate` supplied so the mark is not a CIRP theoretical rate?
- [ ] Is the spread between `cirp_forward_rate` and `valuation_forward_rate` monitored as cross-currency basis rather than treated as a reconciliation break?
- [ ] Is `pip_factor` correct for each pair (100 for yen-quoted, 10,000 for four-decimal, explicit override for anything else)?
- [ ] Does each currency pair's market data actually correspond to the tenors of the positions being marked against it?

## Valuation output
- [ ] Is `mtm_pv_quote` — the **discounted** figure — what gets published, with `undiscounted_mtm_quote` kept only for carry attribution?
- [ ] Is P&L reported per quote currency, with a consolidated total produced only when `reporting_fx_rates` covers every quote currency in the book?
- [ ] Are conversion rates for consolidation from the same snapshot as the valuation spot rates?

## Exposure and risk
- [ ] Is `net_exposure_by_currency` read for **both** legs of every pair, not just the base side?
- [ ] Is `net_exposure_by_maturity_bucket` reviewed alongside it, so a book-level net of zero does not hide two offsetting buckets of gap risk?
- [ ] Are the bucket bounds aligned with the firm's own gap-limit framework rather than left at the library default?

## Audit
- [ ] Are `valuation_details` persisted per run, including the day-count bases, pip factor, discount factor, and `mtm_basis` actually applied?
- [ ] Are `report.warnings` routed to an alert, not just logged?
- [ ] Is `mtm_pv_quote` understood as an **economic** mark, with hedge-accounting designation and effectiveness testing handled elsewhere?
