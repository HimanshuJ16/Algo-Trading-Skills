---
name: satellite-imagery-based-signal-research
description: >-
  Production-grade satellite imagery alternative data research engine processing retail parking lot car counts, floating-roof crude oil tank shadow fill levels, and agricultural NDVI vegetation indices to generate quantitative trading signals.
domain: Alternative Data & Quantitative Research
subdomain: Satellite Imagery & Computer Vision Signals
tags: ["satellite-imagery", "alternative-data", "car-counts", "oil-tank-shadows", "ndvi", "quant-signals"]
brokers_frameworks: ["Earth Observation Alternative Data", "Pandas DataFrames", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when researching, constructing, or backtesting quantitative trading signals derived from satellite imagery alternative data. Satellite data provides objective "ground truth" physical economic activity (retail store traffic, global crude oil inventory fill levels, agricultural crop yields) days or weeks before official corporate earnings reports or government surveys (EIA, USDA) are published. This engine calculates Z-score normalized signals ($Z = \frac{X - \mu}{\sigma}$) and assigns directional trading biases (-1.0 to +1.0).

## Prerequisites

- Satellite observation payload (`SatelliteObservation`: `timestamp_iso`, `asset_id`, `signal_type`, `observed_metric`, `baseline_historical_mean`, `baseline_historical_std`, `availability_lag_days`).
- Signal type (`RETAIL_PARKING_OCCUPANCY`, `FLOATING_ROOF_OIL_STORAGE`, `AGRICULTURAL_NDVI`).

## Workflow

1. **Imagery Metric Ingestion**:
   - Ingest processed computer vision metric (vehicle counts, tank shadow roof fill %, NDVI index).
2. **Z-Score Normalization**:
   - Compute Z-score relative to rolling historical baseline ($\mu, \sigma$).
3. **Directional Mapping & Lag Verification**:
   - Map Z-score to directional bias (-1.0 to +1.0) based on domain logic:
     - Retail parking: High car count ($Z \ge 1.5$) $\implies$ Bullish equity (+1.0).
     - Oil tank fill: High crude inventory ($Z \ge 1.5$) $\implies$ Bearish crude oil (-1.0).
     - Agricultural NDVI: High crop vigor ($Z \ge 1.5$) $\implies$ Bearish crop price (-1.0).
4. **Signal Output**: Output structured `QuantitativeSatelliteSignal`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Image Processing Lag**: Backtesting satellite signals as if they were available at image capture time without adding 1–3 days processing/pipeline latency.
- **Unadjusted Cloud Cover / Seasonal Shadow Changes**: Failing to subtract seasonal solar angle shadow length changes when measuring floating-roof oil tanks.
- **Overestimating Signal Coverage**: Assuming satellite imagery covers 100% of global retail stores or oil storage facilities.

## Verification

- Instantiate `SatelliteImageryBasedSignalResearchEngine`. Process retail car count observation ($Z = +2.0$) $\implies$ verify `trading_signal_direction = +1.0` (Bullish). Process oil storage fill observation ($Z = +2.5$) $\implies$ verify `trading_signal_direction = -1.0` (Bearish). Process neutral observation ($Z = +0.2$) $\implies$ verify `trading_signal_direction = 0.0` (Neutral).
- Run `python scripts/test_satellite_imagery_based_signal_research.py`.

## Related Skills

- `credit-card-transaction-data-signal-construction`
- `web-scraped-sentiment-data-pipeline`
---
