# Workflows for App Usage Alternative Data

## Quantitative Pipeline

1. **Vendor Ingestion & Diligence**
   - Acquire panel data (Downloads, DAU, MAU) per ticker from a vetted vendor
     (Sensor Tower, Apptopia, Similarweb, data.ai).
   - Confirm documented panel methodology, anonymization, and a license
     permitting investment use. Record the vendor's publication lag.
   - Cross-validate a second vendor for any ticker entering live trading, and
     **check ownership independence first**: Sensor Tower acquired data.ai
     (formerly App Annie) in March 2024 and merged its panel, so those two are
     one source, not two. See `references/standards.md`.
2. **Point-In-Time Alignment**
   - Pass the data through `alternative-data-feature-integration` to shift
     event dates forward by the publication lag, ensuring zero look-ahead bias.
3. **Window Alignment**
   - Decide the window over which `downloads` is summed and aggregate to it.
     `high_acquisition_fraction` compares a downloads *flow* to a 30-day MAU
     *stock*, so the two must share a window. The default (`0.10`) assumes a
     trailing 30-day download sum.
   - Running raw single-day downloads against the default silently disables
     `churn_risk_warning` rather than raising — it is a false negative, not an
     error. Either aggregate to 30 days or recalibrate the fraction.
4. **Threshold Calibration**
   - The `50% / 20% / 10%` defaults are consumer-social rules of thumb, not
     validated constants. Build a category peer cohort and set
     `world_class_threshold` / `low_stickiness_threshold` from its observed
     distribution before trading the output. Applying social-app thresholds to
     a weekly-cadence category (food delivery, airline, insurance, e-commerce)
     manufactures sector-wide false churn warnings.
   - Record the calibrated values and the cohort alongside the signal.
5. **Signal Generation**
   - Process PIT-aligned, window-aligned data through
     `AppUsageSignalEngine.process()` (single point) or `process_many()`
     (ordered batch, fail-fast on invalid input).
   - The engine validates inputs (non-empty ticker, finite real-number counts,
     non-negative counts, positive MAU), clamps `DAU > MAU` without mutating the
     input, and emits an `AppUsageSignal`.
6. **Portfolio Construction**
   - Overweight equities flagged `is_world_class`.
   - Underweight/short equities flagged `churn_risk_warning`.
   - Hold equities with average engagement; combine with corroborating
     alt-data before acting.
7. **Freshness & Quality Monitoring**
   - Alert when per-ticker event dates fall behind the expected cadence.
   - Alert on repeated `DAU > MAU` anomalies (vendor panel degradation).
   - Alert on the rate of points rejected for non-finite / non-numeric counts;
     these are hard failures, so a rising rate is a direct feed-quality signal.

## Decision Points

| Engine output | Suggested action | Confidence guardrail |
|---|---|---|
| `is_world_class=True` | Overweight / long tilt | Corroborate with revenue / ARPU data. Confirm the threshold was calibrated to the issuer's app category. |
| `churn_risk_warning=True` | Underweight / short tilt | Confirm download spike is paid-acquisition driven, not organic, and that `downloads` and `high_acquisition_fraction` share a window. A warning on an uncalibrated weekly-cadence app is most likely a threshold artefact. |
| Average engagement | Hold / no standalone trade | Require a second alt-data signal before acting. |
| `DAU > MAU` (anomaly) | Drop the point; escalate to vendor | Do not trade on clamped values until root-caused. |

## Configuration

Thresholds are tunable via `EngineConfig` without code changes to the engine:

```python
from app_download_and_usage_data_for_consumer_companies import (
    AppUsageSignalEngine, EngineConfig, AppUsageDataPoint,
)
engine = AppUsageSignalEngine(EngineConfig(world_class_threshold=0.45))
```

Defaults: `world_class_threshold=0.50`, `low_stickiness_threshold=0.20`,
`high_acquisition_fraction=0.10`. These are unsourced consumer-social rules of
thumb; `high_acquisition_fraction` additionally assumes a trailing 30-day
download sum. Calibrate both against a category peer cohort — see
`references/standards.md` -> "Threshold provenance and calibration".

## Edge Cases & Failure Modes

- **Empty / whitespace-only ticker**: raises `ValueError` (fail-fast).
- **NaN or inf in any count**: raises `ValueError`. Vendor exports encode panel
  gaps as NaN; without this check `nan / nan` yields `nan`, every threshold
  comparison against `nan` is `False`, and a missing observation would be
  emitted as a well-formed "Average engagement" signal.
- **Non-numeric count** (`None` from a JSON null, a string from an uncast CSV
  column, or a `bool`): raises `TypeError`.
- **Negative `downloads` or `dau`**: raises `ValueError`.
- **`mau <= 0`**: raises `ValueError` (stickiness undefined).
- **`DAU > MAU`**: clamped to MAU locally; input not mutated; logged as anomaly.
- **Batch with one invalid point**: `process_many` raises `ValueError`
  immediately rather than silently dropping the point.

## Recovery

- On a `ValueError` from the engine, quarantine the offending data point and
  re-run the upstream PIT-alignment / validation stage.
- On a recurring `DAU > MAU` anomaly, suspend the vendor feed and require a
  methodology explanation before resuming.
- On a freshness alert, fall back to the prior valid snapshot and flag the
  signal as stale until the feed catches up.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `ValueError: mau must be strictly positive` | Panel gap for ticker on that date | Forward-fill from prior valid observation or drop the point. |
| `ValueError: ... must be finite` | Vendor encoded a panel gap as NaN, or an overflow artefact as inf | Quarantine the point; do not substitute 0 — that is a real reading of "no users". |
| `TypeError: ... must be a real number` | Column not cast on ingest (string), JSON null, or a boolean flag written into a count field | Fix the ingest cast; re-run the point. |
| `TypeError: data must be an AppUsageDataPoint` | Wrong type passed to `process` | Construct via the dataclass; do not pass dicts. |
| Sudden spike in churn warnings across many tickers | Vendor panel composition shift, or uncalibrated thresholds applied to a non-social category | Pause live use; re-check category calibration, then cross-validate against an independently owned vendor. |
| `churn_risk_warning` never fires on any ticker | Daily downloads compared against the 30-day-calibrated `high_acquisition_fraction` | Aggregate downloads to a trailing 30-day sum, or recalibrate the fraction to a daily scale. |
| Signals look shifted in backtest | PIT alignment skipped | Re-run `alternative-data-feature-integration` with the correct lag. |
