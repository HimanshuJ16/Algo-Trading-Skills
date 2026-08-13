---
name: app-download-and-usage-data-for-consumer-companies
description: Quantitative alternative data engine for analyzing app engagement metrics
  (DAU, MAU, Downloads) to generate predictive signals for consumer companies.
domain: quant-research-alt-data
subdomain: digital-footprint
tags:
- alt-data
- dau-mau
- consumer-tech
- signal-generation
- churn-prediction
brokers_frameworks:
- generic
version: "1.2.0"
author: System
license: MIT
---

## When to Use

Use this skill when processing mobile application alternative data (sourced from vendors such as Sensor Tower, data.ai, Apptopia, or Similarweb) to build fundamental engagement signals for consumer-facing public companies. While raw app downloads are often reported as "vanity metrics" by companies, the actual Daily Active Users (DAU) and Monthly Active Users (MAU) dictate long-term revenue viability. This engine calculates the "Stickiness Ratio" (DAU/MAU) and flags dangerous divergence between high marketing-driven downloads and failing user retention (the "Leaky Bucket" syndrome).

Applicable scenarios:
- Forecasting revenue durability for consumer-tech, gaming, ride-share, food-delivery, and streaming issuers ahead of earnings.
- Constructing long/short alt-data baskets (overweight `is_world_class`, underweight `churn_risk_warning`).
- Validating management commentary on "record downloads" against underlying engagement.

## When NOT to Use

Do **not** use this skill when:
- You have not yet performed vendor diligence and MNPI/MAR compliance review on the data source. App Annie Inc. was the subject of the SEC's first securities-fraud enforcement against an alternative data provider (Release No. 34-92975, Sept. 14, 2021) for misrepresenting how its estimates were derived and falsely claiming MNPI controls. See `insider-trading-controls-for-alternative-data-usage`.
- The issuer's revenue is not materially driven by app engagement (e.g., pure B2B, hardware-only, or pre-product companies with negligible MAU).
- You need raw point-in-time alignment. This engine consumes already-PIT-aligned `AppUsageDataPoint`s; perform the publication-lag shift upstream via `alternative-data-feature-integration` first.
- You need daily panel spend / transaction signals — use `credit-card-transaction-data-signal-construction` instead.

## Prerequisites

- Python 3.9+.
- A vendor feed providing per-ticker, per-date `downloads`, `dau`, and `mau` estimates.
- Completed vendor diligence: panel methodology documentation, data-licensing terms permitting investment use, and a documented MNPI/MAR compliance sign-off (see `alternative-data-vendor-due-diligence-checklist`).
- Point-in-time alignment of the feed (event date shifted by the vendor's publication lag, typically 1-7 days).

## Workflow

1. **Vendor Ingestion & Diligence**: Acquire daily panel data (Downloads, DAU, MAU) per ticker. Confirm the vendor's panel composition, extrapolation model, and licensing terms permit investment use. Document the publication lag.
2. **Point-In-Time Alignment**: Shift event dates forward by the vendor's publication lag via `alternative-data-feature-integration` so signals are only usable on the date the data became available (eliminates look-ahead bias).
3. **Ingest Metrics**: Load PIT-aligned data into `AppUsageDataPoint` objects (frozen; `ticker`, `date`, `downloads`, `dau`, `mau`).
4. **Calculate Stickiness**: `AppUsageSignalEngine.process()` derives `stickiness_ratio = DAU / MAU`. If `DAU > MAU` (impossible in a genuine panel), DAU is clamped to MAU *without mutating the input* and the event is logged as a data anomaly.
5. **Analyze Divergence**: The engine compares download velocity against stickiness. If `downloads >= 10% of MAU` while `stickiness < 20%`, the engine emits `churn_risk_warning=True`.
6. **Signal Generation**: Output an `AppUsageSignal` classifying the issuer's user base as world-class engagement, leaky-bucket churn risk, or average.
7. **Decision Points**:
   - Overweight equities flagged `is_world_class`.
   - Underweight/short equities flagged `churn_risk_warning`.
   - Hold equities with average engagement; treat as noise unless combined with other alt-data signals.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Downloads with Growth**: Equating a spike in app downloads (often driven by expensive ad campaigns) with revenue growth. If DAU/MAU is below 20%, newly acquired users are churning out immediately.
- **Ignoring Point-in-Time (PIT)**: Backtesting on the event date rather than the vendor publication date introduces look-ahead bias. Always shift by the publication lag upstream (see `alternative-data-feature-integration`).
- **Skipping MNPI / Vendor Diligence**: Consuming app-usage estimates without confirming the vendor's derivation methodology and MNPI controls risks repeating the App Annie (SEC 34-92975) failure mode — trading on data whose provenance and compliance posture were misrepresented.
- **Trusting DAU > MAU**: DAU can never exceed MAU. A panel reporting otherwise is a data-quality defect; the engine clamps and logs it, but recurring occurrences indicate a vendor panel problem requiring escalation.
- **Overreading Cumulative Downloads**: Cumulative downloads have weak correlation with long-term enterprise value on their own; always pair with engagement (stickiness) and acquisition cost context.
- **Stale Data**: App-usage panels update with a lag. Monitoring freshness (last received event date per ticker) is required; a stale feed silently degrades signal quality.

## Verification

- Run `python -m unittest discover -s skills/app-download-and-usage-data-for-consumer-companies/scripts` and confirm all tests pass (covers world-class stickiness, leaky-bucket churn, threshold boundaries, DAU>MAU clamping without input mutation, invalid inputs, custom config, and batch processing).
- Manual check: construct an `AppUsageDataPoint` with `dau=6000000, mau=10000000` (stickiness 60%) and confirm `is_world_class=True`; construct one with `downloads=200000, dau=150000, mau=1000000` (stickiness 15%, high acquisition) and confirm `churn_risk_warning=True` and `"LEAKY BUCKET"` in the summary.
- Confirm `process()` does **not** mutate its input when `DAU > MAU` (regression test `test_dau_exceeds_mau_anomaly`).
- Confirm a documented MNPI/vendor-diligence sign-off exists before promoting any signal to live trading.

## Related Skills

- `alternative-data-feature-integration`
- `insider-trading-controls-for-alternative-data-usage`
- `alternative-data-vendor-due-diligence-checklist`
- `backtesting-alt-data-strategies-with-realistic-availability-lag`
- `web-scraped-sentiment-data-pipeline`
