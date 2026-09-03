# Checklist for Arrival Price / IS Execution Algos

## Prerequisites

- [ ] Python 3.9+ runtime available.
- [ ] Parent order size (integer shares), horizon as a number of time bins, and `UrgencyLevel` defined.
- [ ] Arrival price captured at decision time (median 1-second mid-quote) and stored immutably as the IS benchmark.
- [ ] Order-placement infrastructure (`order-placement-idempotency`, `multi-broker-rate-limit-handling`) in place — every child order needs the same idempotency/rate-limit discipline as a single order.
- [ ] Catch-up / give-up policy for rejected or partially-filled child orders decided in advance.
- [ ] Instrument liquidity tier confirmed; urgency matched to it (see `algo-parameter-defaults-by-instrument-liquidity-tier`).

## Trajectory Validation

- [ ] `UrgencyLevel.HIGH` heavily front-loads child orders (first bin is the max; first-half sum > 2x second-half sum).
- [ ] `UrgencyLevel.HIGH` and `MEDIUM` schedules are monotonically non-increasing across bins.
- [ ] `UrgencyLevel.LOW` generates an exact uniform (flat) TWAP schedule.
- [ ] The sum of all child orders in every trajectory exactly equals the parent order size.
- [ ] No child-order size is ever negative, including boundary inputs (`num_bins=1`, `total_size=1`, `total_size < num_bins`, long horizons).
- [ ] Front-loading strictly increases LOW < MEDIUM < HIGH for the first bin.
- [ ] Same inputs produce identical output across runs (determinism).
- [ ] Long horizons stay on the exact curve: `HIGH` urgency puts ~63.2% in the first bin at 10 bins *and* at 10,000 bins, with no overflow, `NaN`, or collapse to 100% in bin 0.
- [ ] `forecast_shortfall` reproduces the Almgren-Chriss limiting cases (uniform schedule vs Eqs. 10/11; single-bin dump vs Eq. 13) and returns a strictly positive `expected_cost`.

## Deployment

- [ ] Run per-skill tests: `python -m unittest discover -s skills/arrival-price-benchmark-execution-algo/scripts`.
- [ ] Run full suite (guards cross-skill regressions): `python tools/run_all_tests.py`.
- [ ] Validate skill structure: `python tools/validate_skills.py`.
- [ ] After any `version` bump in SKILL.md, run `python tools/build_index.py` and confirm `index.json` is in sync.
- [ ] Confirm `kappa` calibration is appropriate for the instrument's current volatility and impact regime; recalibrate via `execution-cost-model-recalibration-cadence` if stale.

## Rollback

- [ ] Previous SKILL.md version and scripts are recoverable via git (the trajectory generator is stateless — no persisted state migration needed).
- [ ] If a live execution is mid-flight on a bad schedule, re-invoke `generate_schedule` with remaining shares and remaining bins rather than reverting code mid-order.
- [ ] Kill-switch path (`execution-algorithm-kill-switch-integration`) tested and reachable before going live.

## Monitoring

- [ ] Per-child fill events logged with bin index, intended vs filled size, and latency.
- [ ] Live shortfall vs frozen arrival price tracked in real time and alerted when it exceeds `forecast.expected_cost` by more than the configured tolerance, measured in units of `forecast.stdev` (**not** `variance`, which is in currency squared).
- [ ] Shortfall computed on the **filled** quantity, with the unfilled remainder carried as opportunity cost against the horizon-end price, and signed so that positive means underperformance.
- [ ] Schedule drift (cumulative filled vs intended) monitored; catch-up/give-up policy triggered automatically on threshold breach.
- [ ] Impact parameters (`sigma`, `eta`, `gamma`, `epsilon`) sourced from a current calibration, with `eta_tilde = eta - gamma*tau/2 > 0` (the model is non-convex otherwise and `ImpactParameters` will reject it).

## Post-Deployment Verification

- [ ] After a live/paper execution completes, a shortfall report comparing achieved average price to the frozen arrival price is produced and reviewed.
- [ ] Achieved shortfall reconciled against the Almgren-Chriss expected shortfall; persistent excess triggers `kappa` recalibration.
- [ ] Child-order timing/sizing inspected for meaningful within-bin randomization (not perfectly predictable).
- [ ] No rejected/partially-filled child order was silently dropped or blindly resubmitted at the same size.

## Sign-off
- Execution Quant: ___________________________
- Risk Officer: ___________________________
- Date: ___________________________
