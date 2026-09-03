---
name: app-download-and-usage-data-for-consumer-companies
description: >-
  Use when turning mobile app engagement panels such as downloads and active users into
  a fundamental signal for consumer companies, with the panel normalisation and lag that
  makes the estimate point-in-time defensible.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: alt-data, dau-mau, consumer-tech, signal-generation, churn-prediction
  brokers_frameworks: generic
  version: "1.3.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when processing mobile application alternative data (sourced from vendors such as Sensor Tower, data.ai, Apptopia, or Similarweb) to build fundamental engagement signals for consumer-facing public companies. While raw app downloads are often reported as "vanity metrics" by companies, the actual Daily Active Users (DAU) and Monthly Active Users (MAU) dictate long-term revenue viability. This engine calculates the "Stickiness Ratio" (DAU/MAU) and flags dangerous divergence between high marketing-driven downloads and failing user retention (the "Leaky Bucket" syndrome).

Applicable scenarios:
- Forecasting revenue durability for consumer-tech, gaming, ride-share, food-delivery, and streaming issuers ahead of earnings.
- Constructing long/short alt-data baskets (overweight `is_world_class`, underweight `churn_risk_warning`).
- Validating management commentary on "record downloads" against underlying engagement.

## When NOT to Use

Do **not** use this skill when:
- You have not yet performed vendor diligence and MNPI/MAR compliance review on the data source. App Annie Inc. was the subject of the SEC's first securities-fraud enforcement against an alternative data provider (Release No. 34-92975, Sept. 14, 2021): it promised the app companies supplying its data that the data would be used only in aggregated, anonymised form, then used it in non-aggregated form to adjust its model estimates while assuring subscribers those estimates were generated consistently with the consents obtained. It is a provenance case, not a case about public data being per se MNPI. See `insider-trading-controls-for-alternative-data-usage`.
- The issuer's revenue is not materially driven by app engagement (e.g., pure B2B, hardware-only, or pre-product companies with negligible MAU).
- You need raw point-in-time alignment. This engine consumes already-PIT-aligned `AppUsageDataPoint`s; perform the publication-lag shift upstream via `alternative-data-feature-integration` first.
- You need daily panel spend / transaction signals — use `credit-card-transaction-data-signal-construction` instead.
- You have not calibrated the stickiness thresholds to the issuer's app category. The `50% / 20%` defaults are consumer-social rules of thumb; published 2025 benchmarks put healthy e-commerce (~20-23%) and insurance (~16-27%) apps at or below the 20% "low engagement" line. Running the defaults over a weekly-cadence category produces sector-wide false churn warnings.

## Prerequisites

- Python 3.9+.
- A vendor feed providing per-ticker, per-date `downloads`, `dau`, and `mau` estimates.
- Completed vendor diligence: panel methodology documentation, data-licensing terms permitting investment use, and a documented MNPI/MAR compliance sign-off (see `alternative-data-vendor-due-diligence-checklist`).
- Point-in-time alignment of the feed (event date shifted by the vendor's publication lag, typically 1-7 days).

## Workflow

1. **Vendor Ingestion & Diligence**: Acquire panel data (Downloads, DAU, MAU) per ticker. Confirm the vendor's panel composition, extrapolation model, and licensing terms permit investment use. Document the publication lag. Decision point: before nominating a second vendor for cross-validation, verify it is independent *in ownership and panel* — Sensor Tower acquired data.ai (formerly App Annie) in March 2024 and merged its panel, so reconciling those two is not corroboration.
2. **Point-In-Time Alignment**: Shift event dates forward by the vendor's publication lag via `alternative-data-feature-integration` so signals are only usable on the date the data became available (eliminates look-ahead bias).
3. **Window Alignment**: Aggregate `downloads` over the window `high_acquisition_fraction` is calibrated against — the `0.10` default assumes a trailing 30-day sum, matching MAU's window. Decision point: if you pass raw single-day downloads against the default, `churn_risk_warning` will never fire; that is a silent false negative, not an error. Aggregate to 30 days or recalibrate the fraction to a daily scale.
4. **Threshold Calibration**: Set `world_class_threshold` and `low_stickiness_threshold` from a category peer cohort's observed distribution rather than the defaults, and record the values and cohort with the signal. See `references/standards.md` -> "Threshold provenance and calibration".
5. **Ingest Metrics**: Load PIT-aligned, window-aligned data into `AppUsageDataPoint` objects (frozen; `ticker`, `date`, `downloads`, `dau`, `mau`). Counts must be finite real numbers; NaN/inf raise `ValueError` and non-numeric values (`None`, strings, `bool`) raise `TypeError`, so a panel gap can never be scored as engagement.
6. **Calculate Stickiness**: `AppUsageSignalEngine.process()` derives `stickiness_ratio = DAU / MAU`. If `DAU > MAU` (impossible in a genuine panel), DAU is clamped to MAU *without mutating the input* and the event is logged as a data anomaly.
7. **Analyze Divergence**: The engine compares download velocity against stickiness. With the defaults, `downloads >= 10% of MAU` while `stickiness < 20%` emits `churn_risk_warning=True`.
8. **Signal Generation**: Output an `AppUsageSignal` classifying the issuer's user base as world-class engagement, leaky-bucket churn risk, or average.
9. **Decision Points**:
   - Overweight equities flagged `is_world_class`.
   - Underweight/short equities flagged `churn_risk_warning` — but first confirm the thresholds were category-calibrated; an uncalibrated warning on a weekly-cadence app is most likely a threshold artefact, not a churn finding.
   - Hold equities with average engagement; treat as noise unless combined with other alt-data signals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Downloads with Growth**: Equating a spike in app downloads (often driven by expensive ad campaigns) with revenue growth. If DAU/MAU is well below the issuer's category norm, newly acquired users are churning out immediately.
- **Applying Social-App Thresholds Across Categories**: The `50%` world-class and `20%` low-engagement defaults describe habitual consumer-social apps. Published 2025 benchmarks put whole healthy verticals at or under 20% (e-commerce ~20-23%, insurance ~16-27%). A food-delivery or airline app is *designed* for weekly use — scoring it against a messaging-app bar produces a sector-wide short basket built on a calibration error, not on churn.
- **Mismatching the Downloads Window**: `downloads` is a flow, `mau` a 30-day stock. Comparing single-day downloads against the 30-day-calibrated `high_acquisition_fraction=0.10` makes the leaky-bucket condition effectively unreachable — a real app rarely acquires 10% of its monthly actives in one day. The failure is silent: no warning ever fires, and nothing raises.
- **Assuming Two Vendor Brands Are Two Vendors**: Sensor Tower acquired data.ai (formerly App Annie) in March 2024 and folded its panel in. Reconciling a Sensor Tower estimate against a data.ai estimate will not surface a shared panel bias or a common extrapolation error, however independent the two dashboards look.
- **Ignoring Point-in-Time (PIT)**: Backtesting on the event date rather than the vendor publication date introduces look-ahead bias. Always shift by the publication lag upstream (see `alternative-data-feature-integration`).
- **Skipping MNPI / Vendor Diligence**: Consuming app-usage estimates without confirming the vendor's derivation methodology and MNPI controls risks repeating the App Annie (SEC 34-92975) failure mode — trading on data whose provenance and compliance posture were misrepresented.
- **Trusting DAU > MAU**: DAU can never exceed MAU. A panel reporting otherwise is a data-quality defect; the engine clamps and logs it, but recurring occurrences indicate a vendor panel problem requiring escalation.
- **Overreading Cumulative Downloads**: Cumulative downloads are a poor standalone proxy for enterprise value; always pair with engagement (stickiness) and acquisition-cost context. Treat the strength of that relationship as something to measure on your own panel — no quantified correlation is asserted here.
- **Silent NaN From Panel Gaps**: Vendor exports encode gaps as NaN. Every threshold comparison against NaN is `False`, so without validation a missing observation emits a well-formed "Average engagement" signal with `stickiness_ratio=nan` and poisons any downstream aggregate. The engine now rejects non-finite counts outright — never paper over the rejection by substituting `0`, which is a real reading meaning "no users".
- **Stale Data**: App-usage panels update with a lag. Monitoring freshness (last received event date per ticker) is required; a stale feed silently degrades signal quality.

## Verification

- Run `python -m unittest discover -s skills/app-download-and-usage-data-for-consumer-companies/scripts` and confirm all 30 tests pass (covers world-class stickiness, leaky-bucket churn, threshold boundaries, DAU>MAU clamping without input mutation, non-finite and non-numeric counts, fractional vendor estimates, invalid inputs, custom config, and batch processing).
- Manual check: construct an `AppUsageDataPoint` with `dau=6000000, mau=10000000` (stickiness 60%) and confirm `is_world_class=True`; construct one with `downloads=200000, dau=150000, mau=1000000` (stickiness 15%, high acquisition) and confirm `churn_risk_warning=True` and `"LEAKY BUCKET"` in the summary.
- Confirm `process()` does **not** mutate its input when `DAU > MAU` (regression test `test_dau_exceeds_mau_anomaly`).
- Confirm a NaN count raises rather than producing a signal: `AppUsageDataPoint(ticker="X", date=..., downloads=1000, dau=float("nan"), mau=1000)` must raise `ValueError` (regression test `test_nan_dau_raises_rather_than_emitting_nan_signal`).
- Confirm the thresholds in use were calibrated to the issuer's app category, and that the recorded downloads window matches the window `high_acquisition_fraction` was calibrated against.
- Confirm a documented MNPI/vendor-diligence sign-off exists before promoting any signal to live trading.

## Related Skills

- `alternative-data-feature-integration`
- `insider-trading-controls-for-alternative-data-usage`
- `alternative-data-vendor-due-diligence-checklist`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `web-scraped-sentiment-data-pipeline`
