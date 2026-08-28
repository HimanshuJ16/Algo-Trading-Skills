# Pre-Flight Checklist

## Input data
- [ ] Is every contract's index the same label type, with exactly one bar per contract per session?
- [ ] Do the frames carry the column the roll method needs (`volume` or `open_interest`), and `open`/`high`/`low` if the backtest touches intrabar levels?
- [ ] Are contracts ordered by expiration — decoded month code with a **two-digit** year, or an explicit `contract_expiries` map — rather than alphabetically?
- [ ] Does the contract set cover the whole backtest window without a missing expiration in the middle?

## Roll configuration
- [ ] Is the roll trigger a deliberate choice, and recorded with the series? (There is no standard; vendors disagree.)
- [ ] Is the switch effective the session *after* the crossover is observed, so the trigger session's bar is not priced off information from its own close?
- [ ] For `DAYS_BEFORE_EXPIRY`: is `contract_expiries` supplied, and is the threshold understood as **calendar** days?
- [ ] Has `min_confirmation_sessions` been considered for products where a single anomalous volume print could roll the series permanently?

## Adjustment
- [ ] Does the adjustment method match the question being asked — additive for point-based signals and dollar P&L, proportional for percentage returns and multi-decade series?
- [ ] Is the **newest** segment left at real market prices (final bar `adjusted_close == raw_close`, offset 0.0, factor 1.0)?
- [ ] Have `open`, `high` and `low` received the same adjustment as `close`, leaving bar geometry intact?
- [ ] For long backwardated histories under additive adjustment: has the adjusted series been checked for prices crossing zero?
- [ ] For proportional adjustment: are all closes at every roll strictly positive?

## Verification before backtesting
- [ ] Does `total_roll_events` match the number of expirations the period should have crossed?
- [ ] Is `sessions_without_active_bar` zero, or explained?
- [ ] Is `unevaluable_trigger_sessions` zero, or explained?
- [ ] Does the adjusted series have no session-over-session move that a single contract's own price action cannot account for?
- [ ] Is every bar tagged with its `active_contract` and `segment_id`, and are roll sessions flagged?

## Reproducibility
- [ ] Are `roll_events`, the roll method and the adjustment method stored with the series, given that the next roll restates the whole history?
- [ ] Is any downstream consumer that reads absolute price levels off this series aware they are not historical market prices?
