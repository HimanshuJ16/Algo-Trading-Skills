# Pre-Flight Checklist

## Data alignment
- [ ] Is `actual_returns[i]` the return realised *after* `predictions[i]` was observable (including any execution delay), rather than the return of the bar the prediction was made on?
- [ ] Have NaN/Inf values been dropped or imputed before the backtest, rather than being silently treated as "flat"?
- [ ] Does the prediction series come from a walk-forward / out-of-sample process, not a model fit on the whole history?

## Cost configuration
- [ ] Has `bps_cost_half_turn` been calibrated to your own realistic live execution (spread crossed, fees, commission, estimated impact) rather than a borrowed constant?
- [ ] Is `signal_threshold` at least the round-trip breakeven, `2 * bps_cost_half_turn / 10_000`, in the same units as the predictions?
- [ ] Is `liquidate_at_end` left enabled, so a position still open on the final bar pays its exit half-turn?
- [ ] If the model churns around the threshold, has an `exit_threshold` (buy/hold spread) been evaluated?

## Results review
- [ ] Was the threshold sweep validated out-of-sample, rather than picking the best in-sample net result?
- [ ] Is `Total Net Return` positive — and is `Total Turnover (Units)` low enough to be plausible at your intended capital?
- [ ] Does the net edge survive re-running at 2–3× the assumed cost?
- [ ] Has `Cost Drag (%)` been compared against gross return to confirm the strategy is not simply a cost-transfer machine?
- [ ] Are the model's limitations acknowledged — flat cost per unit turnover, no size-dependent market impact, no partial fills or rejections?
