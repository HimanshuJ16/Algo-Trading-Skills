# Workflows for App Usage Alternative Data

## Quantitative Pipeline

1. **Vendor Ingestion & Diligence**
   - Acquire daily panel data (Downloads, DAU, MAU) per ticker from a vetted
     vendor (Sensor Tower, data.ai, Apptopia, Similarweb).
   - Confirm documented panel methodology, anonymization, and a license
     permitting investment use. Record the vendor's publication lag.
   - Cross-validate two vendors for any ticker entering live trading.
2. **Point-In-Time Alignment**
   - Pass the data through `alternative-data-feature-integration` to shift
     event dates forward by the publication lag, ensuring zero look-ahead bias.
3. **Signal Generation**
   - Process PIT-aligned data through `AppUsageSignalEngine.process()` (single
     point) or `process_many()` (ordered batch, fail-fast on invalid input).
   - The engine validates inputs (non-empty ticker, non-negative counts,
     positive MAU), clamps `DAU > MAU` without mutating the input, and emits
     an `AppUsageSignal`.
4. **Portfolio Construction**
   - Overweight equities flagged `is_world_class`.
   - Underweight/short equities flagged `churn_risk_warning`.
   - Hold equities with average engagement; combine with corroborating
     alt-data before acting.
5. **Freshness & Quality Monitoring**
   - Alert when per-ticker event dates fall behind the expected cadence.
   - Alert on repeated `DAU > MAU` anomalies (vendor panel degradation).

## Decision Points

| Engine output | Suggested action | Confidence guardrail |
|---|---|---|
| `is_world_class=True` | Overweight / long tilt | Corroborate with revenue / ARPU data. |
| `churn_risk_warning=True` | Underweight / short tilt | Confirm download spike is paid-acquisition driven, not organic. |
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
`high_acquisition_fraction=0.10`.

## Edge Cases & Failure Modes

- **Empty / whitespace-only ticker**: raises `ValueError` (fail-fast).
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
| `TypeError: data must be an AppUsageDataPoint` | Wrong type passed to `process` | Construct via the dataclass; do not pass dicts. |
| Sudden spike in churn warnings across many tickers | Vendor panel composition shift | Pause live use; cross-validate against a second vendor. |
| Signals look shifted in backtest | PIT alignment skipped | Re-run `alternative-data-feature-integration` with the correct lag. |
