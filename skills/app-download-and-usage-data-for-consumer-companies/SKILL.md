---
name: app-download-and-usage-data-for-consumer-companies
description: Quantitative alternative data engine for analyzing app engagement metrics (DAU, MAU, Downloads) to generate predictive signals for consumer companies.
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
version: 1.1.0
author: System
license: MIT
---

## When to Use

Use this skill when processing mobile application alternative data (often sourced from vendors like Apptopia or SensorTower). While raw app downloads are often reported as "vanity metrics" by companies, the actual Daily Active Users (DAU) and Monthly Active Users (MAU) dictate long-term revenue viability. This engine calculates the "Stickiness Ratio" (DAU/MAU) and flags dangerous divergence between high marketing-driven downloads and failing user retention (the "Leaky Bucket" syndrome).

## Prerequisites

- Python 3.9+
- Time-series data containing `downloads`, `dau` (Daily Active Users), and `mau` (Monthly Active Users).

## Workflow

1. **Ingest Metrics**: Load the daily/monthly active user data into `AppUsageDataPoint` objects.
2. **Calculate Stickiness**: The engine derives the DAU/MAU ratio, measuring how habitual the app usage is.
3. **Analyze Divergence**: The engine compares download velocity against stickiness. If downloads are surging but the stickiness ratio is collapsing, the engine triggers a `Churn Risk Warning`.
4. **Signal Generation**: Output a clean `AppUsageSignal` indicating whether the consumer company's user base is growing sustainably or bleeding capital on ineffective marketing.

## Common Pitfalls

- **Confusing Downloads with Growth**: Equating a spike in app downloads (often driven by expensive ad campaigns) with revenue growth. If DAU/MAU is below 20%, those downloaded users are churning out immediately.
- **Ignoring Point-in-Time (PIT)**: Ensuring that the alternative data vendor's publication lag is accounted for (see the `alternative-data-feature-integration` skill).

## Verification

Run `python scripts/test_app_download_and_usage_data_for_consumer_companies.py` to confirm that the engine accurately identifies "world-class" stickiness vs "leaky bucket" churn risk.

## Related Skills

- `alternative-data-feature-integration`
- `web-scraped-sentiment-data-pipeline`
