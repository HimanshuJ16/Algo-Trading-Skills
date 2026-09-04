# Checklist for App Usage Data Signals

## Prerequisites
- [ ] Python 3.10+ available.
- [ ] Vendor feed contracted: per-ticker daily `downloads`, `dau`, `mau`.
- [ ] Vendor diligence complete: panel methodology documented, license permits investment use, MNPI/MAR sign-off recorded (`alternative-data-vendor-due-diligence-checklist`).
- [ ] Vendor publication lag documented and applied upstream via `alternative-data-feature-integration` (PIT alignment).
- [ ] Cross-validation vendor is **independent in ownership and panel**, not just a different brand (Sensor Tower acquired data.ai / App Annie in March 2024 and merged its panel — those two are one source).
- [ ] Downloads aggregation window fixed and documented, and it matches the window `high_acquisition_fraction` was calibrated against (default assumes a trailing 30-day sum, matching MAU).
- [ ] Thresholds calibrated against a category peer cohort — the `50% / 20% / 10%` defaults are consumer-social rules of thumb, not validated constants, and misfire on weekly-cadence categories (food delivery, airline, insurance, e-commerce).
- [ ] Calibrated threshold values and the peer cohort recorded alongside the signal for reproducibility.
- [ ] `insider-trading-controls-for-alternative-data-usage` controls reviewed and in place.

## Validation
- [ ] `AppUsageSignalEngine` instantiates (default or custom `EngineConfig`).
- [ ] DAU/MAU stickiness calculated cleanly; `DAU > MAU` clamped to MAU **without mutating the input**.
- [ ] World-class boundary (>= 50%) and leaky-bucket condition (`downloads >= 10% of MAU` AND `stickiness < 20%`) behave correctly at exact boundaries.
- [ ] Invalid inputs (empty ticker, negative counts, `mau <= 0`, wrong type) raise `ValueError`/`TypeError` fail-fast.
- [ ] Non-finite counts (NaN/inf) raise `ValueError` and non-numeric counts (`None`, string, `bool`) raise `TypeError` — confirm no NaN stickiness ever reaches portfolio construction.
- [ ] Fractional vendor estimates (extrapolated panel figures) are accepted unchanged.
- [ ] `process_many` preserves order and fails fast on the first invalid point.
- [ ] `process()` is deterministic: repeated calls on the same input yield identical `AppUsageSignal`.
- [ ] Run test suite: `python -m unittest discover -s skills/app-download-and-usage-data-for-consumer-companies/scripts`.

## Deployment
- [ ] Engine wired into the signal pipeline downstream of PIT alignment.
- [ ] Freshness monitor armed: alert when per-ticker event date falls behind 2x the publication lag.
- [ ] Anomaly monitor armed: alert on recurring `DAU > MAU` occurrences per vendor.
- [ ] Signals routed to portfolio construction with decision-point mapping (overweight world-class, underweight leaky-bucket).

## Rollback
- [ ] Engine is stateless and input-immutable; rolling back is a code/config redeploy with no in-flight state to drain.
- [ ] Prior signal snapshot retained for comparison after redeploy.
- [ ] Vendor feed can be suspended independently of the engine if panel degradation is detected.

## Monitoring (post-deployment)
- [ ] Per-ticker signal log retained for audit (ticker, date, stickiness, flags, summary).
- [ ] Data-quality dashboard tracks: stale tickers, `DAU > MAU` anomaly rate, rejected-point rate (non-finite / non-numeric counts), vendor feed gaps.
- [ ] Periodic cross-vendor reconciliation (e.g., monthly) for tickers in live book, against an independently owned vendor.
- [ ] Threshold calibration re-run at least annually, or when the peer cohort's stickiness distribution shifts materially.
- [ ] Alert if `churn_risk_warning` never fires across the whole universe — the usual cause is daily downloads compared against a 30-day-calibrated `high_acquisition_fraction` (a silent false negative).

## Post-Deployment Verification
- [ ] First live signals match backtest expectations for the same PIT-aligned inputs.
- [ ] No unhandled exceptions in the rolling 7-day production window.
- [ ] Compliance attestation refreshed quarterly (MNPI/MAR controls still effective).

## Sign-off
- Quantitative Researcher: ___________________________
- Compliance / MNPI reviewer: ___________________________
- Date: ___________________________
