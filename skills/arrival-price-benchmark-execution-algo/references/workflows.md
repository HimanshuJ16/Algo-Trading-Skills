# Workflows for Arrival Price / IS Execution

## End-to-End Execution Workflow

1. **Portfolio Manager Decision**: The PM decides to buy 10,000 shares of AAPL. The median 1-second mid-quote at this instant is $150.00. This is recorded as the immutable **Arrival Price**.
2. **Urgency Assessment** — map the alpha-decay horizon to urgency:
   - Alpha decays in 5 minutes -> `UrgencyLevel.HIGH` (`kappa = 1.0`).
   - Alpha decays over the session (hours) -> `UrgencyLevel.MEDIUM` (`kappa = 0.5`).
   - Trade is a multi-day rebalance with no short-term alpha -> `UrgencyLevel.LOW` (uniform TWAP).
3. **Trajectory Generation**: `ArrivalPriceTrajectoryGenerator.generate_schedule(total_size, num_bins, urgency)` produces the child-order array. Example for 10,000 shares, 10 bins:
   - LOW: `[1000] * 10`
   - MEDIUM: front-loaded, ~40% in bin 0, smoothly decreasing.
   - HIGH: front-loaded, ~63% in bin 0, smoothly decreasing.
4. **Execution Routing**: An execution bot loops through the time bins, sending Limit or Market orders sized according to the array. Timing/sizing are slightly randomized within each bin so the pattern is not predictable.
5. **Deviation Handling** (catch-up / give-up policy, decided in advance):
   - **Catch-up**: redistribute an unfilled child quantity across remaining bins, accepting more impact to hit the arrival-price benchmark.
   - **Give-up**: accept incomplete execution by the window's end, eating opportunity cost but avoiding an impact spike. Appropriate when price has already moved favorably and there is no urgency to complete.
   - Never blindly resubmit the exact same rejected child order — re-evaluate against live liquidity first.
6. **Implementation Shortfall Calculation**: At completion, with `s = +1` for a buy and `s = -1` for a sell:
   ```
   IS = s * [ Q_filled * (Avg Exec Price - Arrival Price)
            + (Q_parent - Q_filled) * (End Price - Arrival Price) ] + fees
   ```
   Positive means underperformance. The execution term takes the **filled** quantity; the unfilled remainder carries an opportunity cost against the horizon-end price. A give-up decision in step 5 is precisely the case where `Q_filled < Q_parent`, so collapsing this to `(Arrival - Avg Exec) * Q_parent` both flips the sign and misprices the give-up.

   Then compare against the model:
   ```python
   from arrival_price_benchmark_execution_algo import ImpactParameters, forecast_shortfall

   params = ImpactParameters(sigma=..., eta=..., gamma=..., epsilon=..., tau=1.0)
   forecast = forecast_shortfall(trajectory.child_order_sizes, params, risk_aversion=lam)
   # forecast.expected_cost, forecast.stdev, forecast.objective
   ```
   Compare realised `IS` against `forecast.expected_cost` in units of `forecast.stdev` — `variance` is in currency squared. A persistent excess means `kappa` calibration is off.

## Decision Points

| Situation | Recommended Action |
|---|---|
| Child order rejected due to thin book | Apply catch-up across remaining bins if still within impact budget; else give-up. |
| Price gaps sharply against the order mid-execution | Re-evaluate: if alpha still live, raise urgency (re-generate with HIGH); if alpha gone, give-up. |
| Live volatility diverges >2x from the sigma used to set kappa | Pause, recalibrate kappa (see `execution-cost-model-recalibration-cadence`), re-generate remaining schedule. |
| `total_size` not divisible by `num_bins` | Largest-remainder apportionment handles this automatically; residual skews to front bins. |
| Schedule looks too front-loaded; horizon lengthened but bin 0 did not shrink | Expected: the half-life `1/kappa` is independent of `T`. Lower the urgency; adding bins only appends near-empty tail bins. |
| Horizon spans days rather than bins within a session | Static kappa is brittle over that span regardless of the schedule's numerics; use `multi-day-execution-schedules-for-very-large-orders` with adaptive re-planning. |

## Edge Cases & Failure Modes

- **`num_bins = 1`**: single bin receives the entire parent — valid degenerate schedule.
- **`total_size = 1` across many bins**: apportionment places the single share in one bin, rest zero — no negatives.
- **`total_size < num_bins`**: only `total_size` bins are non-zero; curve shape is preserved as well as integer allocation allows.
- **Extreme `kappa * T`**: the sinh ratio is evaluated in a scaled form (`exp(a-b)*(1-exp(-2a))/(1-exp(-2b))`) that never materialises `sinh(kappa*T)`, so the trajectory stays exact for arbitrarily long horizons. A long horizon is *not* an immediate-execution regime — the leading bins are unchanged, since the half-life does not depend on `T`.
- **Non-integer / boolean inputs**: rejected by explicit `TypeError` guards (`bool` is a subclass of `int` and is rejected explicitly to avoid silent `True == 1` coercion).

## Recovery

- If the generator raises `ValueError`/`TypeError`, the caller must fix the inputs before retrying — there is no partial state to roll back (the function is pure and stateless).
- If a live execution diverges from the schedule, re-invoke `generate_schedule` with the **remaining** unexecuted shares and the **remaining** bins to produce a fresh continuation schedule; do not reuse the original array past the deviation point.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| Trajectory first bin ~100% of parent | `kappa` genuinely too high (`kappa*tau >> 1`, i.e. bins long relative to the half-life). | Lower the urgency level, or use finer bins so one bin is short relative to `1/kappa`. |
| Trajectory first bin ~63% (HIGH) or ~39% (MEDIUM) regardless of `num_bins` | Not a bug: the half-life `1/kappa` is independent of `T` (A&C 2000 Sec. 2.3). | Lower urgency to trade more patiently. |
| Forecast expected cost is negative | The closed-form Eq. (20) was evaluated with `T` substituted for `tau` inside `tanh`/`sinh`. | Use `forecast_shortfall`, or re-check Eq. (20) against `standards.md`. |
| Negative child size | Should be impossible after the fix; if seen, check for monkey-patched `_apportion`. | Re-run unit tests; restore canonical module. |
| Sum of child sizes != parent | Floating-point drift past the apportionment guard. | Report; the defensive `drift` correction in `_trajectory` should already cover it. |
| Non-deterministic output across runs | External mutation of the `_KAPPA` map or input mutation. | Do not mutate `UrgencyLevel`/`_KAPPA`; treat `ExecutionTrajectory` as immutable. |
