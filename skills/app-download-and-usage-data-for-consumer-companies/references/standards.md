# Standards for Consumer App Data Signals

## Engagement Thresholds

| Metric | Definition | Threshold (defaults) |
|---|---|---|
| **Stickiness Ratio** | DAU / MAU | `>= 50%` world-class; `< 20%` low engagement. Configurable via `EngineConfig`. |
| **Leaky Bucket Syndrome** | High Downloads + Low Stickiness | `downloads >= 10% of MAU` AND `stickiness < 20%` => `churn_risk_warning`. |
| **High Acquisition** | Daily downloads as a fraction of MAU | `downloads / MAU >= 10%` (inclusive). |
| **DAU > MAU** | Impossible panel state | Clamp DAU to MAU locally (input not mutated); log anomaly; escalate if recurring. |
| **Cumulative Downloads** | "Vanity" metric | Weak standalone correlation with enterprise value; never use alone. |

All thresholds are configurable through `EngineConfig` and validated at construction
(`world_class_threshold` in `(0, 1]`; `low_stickiness_threshold` in
`[0, world_class_threshold)`; `high_acquisition_fraction` in `[0, 1]`). Boundary
comparisons are inclusive on the world-class and high-acquisition sides and
exclusive on the low-stickiness side, so exact-boundary behavior is deterministic.

## Vendor Landscape (mobile app alt-data)

| Vendor | Strengths | Limitations / diligence focus |
|---|---|---|
| **Sensor Tower** | Downloads, MAU/DAU estimates, SDK install panel | Panel extrapolation; confirm SDK coverage by geography; license permits investment use. |
| **data.ai** (formerly App Annie) | Broad global coverage, deep history | Subject of SEC Release No. 34-92975 (2021) for misrepresenting estimate derivation and MNPI controls. Require written attestation on methodology and MNPI posture. |
| **Apptopia** | Mid-market pricing, download/DAU estimates | Smaller panel; higher variance for niche apps; validate against reported earnings. |
| **Similarweb** | Web + app cross-traffic, engagement | App panel is secondary to web; cross-check app-side coverage. |

Treat all vendor estimates as model outputs, not ground truth. Cross-validate
two independent vendors before relying on a signal for live capital.

## MNPI & Compliance

App-usage estimates can rise to the level of material nonpublic information
depending on derivation method and source data. Minimum controls before
consuming signals in live trading:

1. **Vendor diligence**: documented panel methodology, anonymization guarantees,
   and a written license permitting investment use
   (`alternative-data-vendor-due-diligence-checklist`).
2. **MNPI assessment**: confirm the vendor does not ingest non-public company
   data to produce estimates, and that internal controls exist to prevent MNPI
   leakage (the App Annie 34-92975 failure mode).
3. **Information barrier**: maintain separation between research consuming
   alt-data and any group with access to MNPI from the issuer
   (`insider-trading-controls-for-alternative-data-usage`).
4. **MAR surveillance** (EU): ensure alt-data signals are within the firm's
   market-abuse surveillance scope and insider-list controls.

## Point-in-Time Correctness

- Always shift the event date by the vendor's publication lag (typically 1-7
  days) before ingestion. This engine assumes PIT alignment is already done
  upstream.
- Record the `as_of` (availability) date alongside the event date in the
  calling pipeline; never backtest on the event date alone.

## Freshness / Availability Monitoring

- Alert when the most recent event date per ticker falls behind the vendor's
  expected cadence by more than 2x the publication lag.
- Alert on a spike in `DAU > MAU` anomalies per vendor (panel degradation
  indicator).

## Category

`quant-research-alt-data`
