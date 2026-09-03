# Pre-Flight / Sign-off Checklist — multi-horizon-forecasting-architecture

Use this before considering the skill's implementation complete.

- [ ] **One Forecast Per Horizon:** Confirm competing models for the same horizon are aggregated upstream and duplicate `horizon_steps` are rejected, not silently double-weighted.
- [ ] **Consistent Horizon Units:** Confirm every prediction in a call expresses `horizon_steps` in the same base unit, and that `predicted_return` is the return over the *whole* horizon, not a per-step rate.
- [ ] **Finite, In-Range Inputs:** Confirm non-finite values, $|IC| > 1$, confidence outside $[0, 1]$, and non-positive horizons are rejected at the boundary.
- [ ] **Horizon-at-IC Match:** Confirm each `ic_score` was measured out-of-sample *at its own horizon*, not reused across horizons.
- [ ] **Scale Normalization Applied:** Confirm forecasts are rescaled onto one target horizon before weighting. Spot-check one: a $90$ bps forecast at $\tau = 45$ must enter a $\tau_\star = 5$ blend as $30$ bps under `SQRT_TIME`.
- [ ] **Scaling Mode Justified:** Confirm `SQRT_TIME`'s iid assumption is acceptable for the instrument, or that measured per-horizon volatilities and `EXPLICIT_VOL` are used instead. `NONE` requires documented upstream normalization.
- [ ] **Target Horizon Documented:** Confirm $\tau_\star$ matches the horizon actually traded, and that downstream consumers read `composite_alpha` in those units.
- [ ] **Weight Normalization:** Confirm $\sum_k \bar{w}_k = 1.0$ and that the composite equals the weighted mean of the rescaled forecasts before arbitration.
- [ ] **No Silent Scheme Fallback:** Confirm an unrecognized weighting scheme, scaling mode, or conflict policy raises rather than degrading to equal weighting.
- [ ] **Degenerate Weights Fail Safe:** Confirm an all-non-positive-IC (or all-zero-confidence) set returns `composite_alpha == 0.0` with status `NO_VALID_HORIZON_WEIGHTS` and a WARNING, rather than an equal-weighted signal.
- [ ] **Consensus Reported Honestly:** Confirm both head-count and weight-weighted consensus are reported, and that an all-flat forecast set reports $0\%$, not $100\%$.
- [ ] **Conflict Threshold In Target Units:** Confirm `conflict_threshold` is set to the smallest move worth acting on at $\tau_\star$ and is compared against the *rescaled* forecasts.
- [ ] **Arbitration Policy Chosen And Recorded:** Confirm the `ConflictPolicy` is a deliberate choice, that any damping factor is derived from conflict-conditional performance rather than left at the placeholder default, and that the report status distinguishes a damped signal from a clean one.
- [ ] **Turnover Netted:** Confirm short-horizon weight is justified after execution cost and turnover, not on gross IC alone.
- [ ] **Risk Controls Independent:** Confirm exposure limits and kill switches sit outside this signal path — conflict arbitration is not a risk control.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/multi-horizon-forecasting-architecture/scripts` and confirm 100% test pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Reviewed by: ___________________________
