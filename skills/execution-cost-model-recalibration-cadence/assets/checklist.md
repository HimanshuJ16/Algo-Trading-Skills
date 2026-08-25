# Pre-Flight Checklist — Execution Cost Model Recalibration

## Sample

- [ ] Sample contains **completed** parent orders only — no working or partially-filled orders.
- [ ] All trades measure `realized_is_bps` against the **same benchmark** the model predicts.
- [ ] Sample window is fixed, recorded, and deduplicated.
- [ ] `volatility_daily_pct` is in **percent** ($1.5$ = $1.5\%$/day), not a decimal fraction and not annualized.
- [ ] Non-finite values are rejected before any metric is computed — a `NaN` makes RMSE and bias `NaN`, and every `nan > limit` test is False, so corrupt data would report the model as stable.
- [ ] `order_qty > 0` and `adv_shares > 0` on every record.

## Audit

- [ ] RMSE tracking error and mean prediction bias computed against the **active** parameters.
- [ ] Bias sign convention understood: **positive means the model under-predicts cost**.
- [ ] Thresholds compared on **unrounded** metrics (rounding first creates a dead band at the limit).
- [ ] Thresholds are documented as **configuration**, with the calibration rationale recorded — they are not industry standards.

## Refit gates

- [ ] Sample size meets `min_recalibration_sample_size`; if not, the breach is reported and the refit **deferred**, not forced.
- [ ] Refit is a genuine two-regressor least-squares solve, not a single-ratio rescale of both coefficients.
- [ ] Design conditioning $\det/(S_{11}S_{22})$ is above the floor — the sample spans multiple order sizes and spread regimes.
- [ ] Fitted $\eta^*$ and $\gamma^*$ are both non-negative.
- [ ] Fitted magnitudes sanity-checked against the literature ($\eta \approx 0.5$ for half-spread; square-root prefactor of order $0.5\text{–}1.0$).

## Before promoting to production

- [ ] Refitted parameters scored on a **held-out or subsequent** sample — in-sample RMSE is guaranteed no worse and is not evidence.
- [ ] Drift confirmed as a regime change, not a one-off event (venue outage, fee-schedule change, outlier parents) that the refit would bake in.
- [ ] Parameter change versioned with a rollback path.
- [ ] Sample window, trigger metrics, conditioning, and approver recorded for audit.

## Cadence

- [ ] Trigger-based audit runs on a defined schedule, not ad hoc.
- [ ] Calendar-arm review is scheduled separately — including the annual validation floor where RTS 6 Art. 9 applies.
