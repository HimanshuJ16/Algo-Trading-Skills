---
name: alternative-data-feature-integration
description: Integrates alternative data sources (e.g., satellite, credit card logs,
  sentiment) into quantitative features while enforcing strict Point-in-Time (PIT)
  lag mapping to prevent look-ahead bias.
domain: financial-ml
subdomain: data-engineering
tags:
- machine-learning
- alternative-data
- look-ahead-bias
- point-in-time
- feature-engineering
brokers_frameworks:
- generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill when integrating any alternative data source into a trading model. Alternative data is notoriously prone to **look-ahead bias** because the date an event happened (Event Date) is rarely the date the quantitative fund actually received the data (Knowledge Date or As-Of Date). This engine strictly enforces publication lags and aligns irregular alternative data frequencies (e.g., weekly satellite updates) to the trading strategy's frequency (e.g., daily market close) using safe, PIT-compliant forward-filling.

## Prerequisites

- Python 3.9+
- Raw alternative data events containing an exact `event_timestamp`.
- A known `publication_lag` (how long after the event the data vendor actually publishes the dataset).

## Workflow

1. **Ingest Raw Events**: Load raw alternative data points into the `AltDataIntegrator`.
2. **Apply Publication Lag**: The integrator adds the publication lag to the event time to compute the strict `knowledge_timestamp`.
3. **Align to Trading Timeline**: Pass a list of target trading times (e.g., daily market close times). The integrator will map the most recently known alternative data value to each trading time. 
4. **Safe Forward-Filling**: If no new alternative data has been published by the trading time, the integrator safely forward-fills the last known value, guaranteeing zero future leakage.

## Common Pitfalls

- **Using Event Date for Backtesting**: The most critical error in quantitative finance. If satellite imagery of a retailer's parking lot is taken on Sunday (Event Date) but not published by the vendor until Tuesday morning (Knowledge Date), backtesting as if you knew the data on Monday morning introduces massive look-ahead bias.
- **Naive Forward Filling**: Forward filling a pandas dataframe without first shifting the index by the publication lag.

## Verification

Run `python scripts/test_alt_data_integrator.py` to assert that the PIT mapping correctly shifts knowledge times and successfully aligns irregular data to a target trading schedule without leaking future values.

## Related Skills

- `feature-engineering-without-leakage`
- `point-in-time-database-for-ml-training-data`
