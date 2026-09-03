# Standards for Consumer App Data Signals

## Engagement Thresholds

| Metric | Definition | Threshold (defaults) | Source |
|---|---|---|---|
| **Stickiness Ratio** | DAU / MAU | `>= 50%` world-class; `< 20%` low engagement. Configurable via `EngineConfig`. | Industry rule of thumb (no external standard exists) — calibrate per category, see below |
| **Leaky Bucket Syndrome** | High Downloads + Low Stickiness | `downloads >= 10% of MAU` AND `stickiness < 20%` => `churn_risk_warning`. | Engineering default (no external standard exists) |
| **High Acquisition** | Downloads as a fraction of MAU, both measured over the **same window** | `downloads / MAU >= 10%` (inclusive). Default assumes downloads summed over a trailing 30 days, matching MAU. | Engineering default (no external standard exists) |
| **DAU > MAU** | Impossible panel state | Clamp DAU to MAU locally (input not mutated); log anomaly; escalate if recurring. | Arithmetic identity |
| **Non-finite counts** | NaN / inf in `downloads`, `dau` or `mau` | Rejected with `ValueError` before any arithmetic. Non-numeric (incl. `bool`, `None`) rejected with `TypeError`. | Engine invariant |
| **Cumulative Downloads** | "Vanity" metric | Directionally weak on its own; pair with engagement and acquisition-cost context. Not a quantified correlation claim — measure it on your own panel before relying on it. | Unquantified heuristic |

All thresholds are configurable through `EngineConfig` and validated at construction
(`world_class_threshold` in `(0, 1]`; `low_stickiness_threshold` in
`[0, world_class_threshold)`; `high_acquisition_fraction` in `[0, 1]`). Boundary
comparisons are inclusive on the world-class and high-acquisition sides and
exclusive on the low-stickiness side, so exact-boundary behavior is deterministic.

## Threshold provenance and calibration

The `50% / 20% / 10%` defaults are **industry rules of thumb, not validated
constants**. No regulator, standards body, or peer-reviewed source defines them;
they circulate as consumer-social benchmarks (the 50% figure is commonly traced
to Facebook-era "sticky consumer product" commentary). Treat them as a starting
point, never as ground truth.

**They do not transfer across app categories.** Published 2025 category
benchmarks put healthy verticals at or below the 20% "low engagement" default —
e-commerce ~20-23%, fintech insurance ~16-27%, AI products ~21-23%, and B2B SaaS
~31-33%, varying by region ([Mixpanel, "Monthly active users (MAU): definition,
formula, and benchmarks"](https://mixpanel.com/blog/mau/)). A grocery, airline,
insurance, or food-delivery app is *designed* for weekly or monthly use; scoring
it against a messaging-app bar manufactures false `churn_risk_warning`s across
an entire sector. This matters directly here because the skill explicitly
targets ride-share, food-delivery, and streaming issuers.

Required before trading the output:

1. Build a **category peer cohort** (5+ comparable apps, same usage cadence).
2. Set `low_stickiness_threshold` and `world_class_threshold` from that cohort's
   observed distribution (e.g. cohort 20th / 80th percentile), not from the
   defaults.
3. Fix the **downloads aggregation window** and calibrate
   `high_acquisition_fraction` against it (below).
4. Record the calibrated values and cohort with the signal, so a later review
   can reproduce the classification.

## Downloads window (units)

`downloads` is a **flow** and `mau` a **30-day stock**. The ratio is only
meaningful once the caller fixes the window over which downloads are summed, and
the engine cannot infer it from the data.

- The `high_acquisition_fraction=0.10` default assumes downloads summed over the
  **same trailing 30-day window** as MAU.
- Feeding **single-day** downloads against that default is a dimensional
  mismatch that makes the condition effectively unreachable — a real app rarely
  acquires 10% of its monthly actives in one day — so `churn_risk_warning` never
  fires. That failure is silent: a missing warning, not an error.
- If you must run on daily downloads, recalibrate `high_acquisition_fraction`
  to a daily-scale value measured on your own panel, and document it.

## Vendor Landscape (mobile app alt-data)

| Vendor | Strengths | Limitations / diligence focus |
|---|---|---|
| **Sensor Tower** | Downloads, MAU/DAU estimates, SDK install panel | Panel extrapolation; confirm SDK coverage by geography; license permits investment use. **Acquired data.ai in March 2024** and has since folded data.ai's panel apps into its own first-party panel. |
| **data.ai** (formerly App Annie) | Broad global coverage, deep history | **Now owned by Sensor Tower** (acquisition announced March 2024) — see the independence warning below. Subject of SEC Release No. 34-92975 (2021) for misrepresenting how its estimates were derived. Require written attestation on methodology and MNPI posture. |
| **Apptopia** | Mid-market pricing, download/DAU estimates | Independent as of 2025 (acquired its own US/EU consumer panel, Feb 2025). Smaller panel; higher variance for niche apps; validate against reported earnings. |
| **Similarweb** | Web + app cross-traffic, engagement | App panel is secondary to web; cross-check app-side coverage. |

Treat all vendor estimates as model outputs, not ground truth. Cross-validate
against a second vendor before relying on a signal for live capital — but verify
the two are **independent in ownership and panel**, not merely different brands:

> **Sensor Tower and data.ai are the same company.** Sensor Tower announced its
> acquisition of data.ai (formerly App Annie) in March 2024 and has integrated
> data.ai's panel into its own
> ([Sensor Tower announcement](https://sensortower.com/blog/data-ai-joins-sensor-tower);
> [PR Newswire, 18 Mar 2024](https://www.prnewswire.com/news-releases/sensor-tower-acquires-market-intelligence-platform-dataai-302090753.html)).
> Reconciling a Sensor Tower estimate against a data.ai estimate is **not**
> independent corroboration and will not surface a shared panel bias or a common
> extrapolation error. Pair either one with a structurally separate source
> (e.g. Apptopia, Similarweb, or first-party disclosure) instead.

Record the ownership check as part of vendor diligence; vendor consolidation in
this sector is ongoing, so re-verify at each diligence refresh.

## MNPI & Compliance

App-usage estimates can rise to the level of material nonpublic information
depending on derivation method and source data. Minimum controls before
consuming signals in live trading:

1. **Vendor diligence**: documented panel methodology, anonymization guarantees,
   and a written license permitting investment use
   (`alternative-data-vendor-due-diligence-checklist`).
2. **MNPI assessment**: confirm the vendor does not use non-public, non-
   aggregated company data to derive or adjust its estimates, and that internal
   controls exist to prevent MNPI leakage. This is the precise App Annie
   34-92975 failure mode: App Annie promised the app companies supplying its
   data that the data would be used only in aggregated, anonymised form, then
   used it in non-aggregated form to alter model estimates, while assuring
   subscribers the estimates were generated consistently with the consents it
   had obtained. It is a **provenance** case, not a case about predictive power
   or about public/aggregate data being per se MNPI — see
   `alternative-data-vendor-due-diligence-checklist`.
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
- Alert on rejected points (non-finite or non-numeric counts) per vendor: these
  are now hard failures rather than silent NaN signals, so their rate is a
  direct feed-quality indicator.
- Re-run threshold calibration on the category cohort at least annually, or
  whenever the cohort's stickiness distribution shifts materially — a stale
  threshold silently reclassifies issuers.
