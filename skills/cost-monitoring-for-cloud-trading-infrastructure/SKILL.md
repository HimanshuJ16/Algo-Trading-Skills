---
name: cost-monitoring-for-cloud-trading-infrastructure
description: Quantitative FinOps module for tracking cloud infrastructure expenditure
  (AWS/GCP/Azure compute, egress bandwidth, storage), detecting cost spikes via rolling
  Z-score baselines, and auditing unit economics.
domain: Infrastructure
subdomain: FinOps & Cloud Management
tags:
- finops
- cloud-cost
- anomaly-detection
- aws
- gcp
- z-score
- egress-cost
- unit-economics
brokers_frameworks:
- Generic Cloud
- NumPy
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in cloud-hosted quantitative trading architectures (AWS, GCP, Azure) to monitor infrastructure costs (Compute, Data Egress, Market Data Feeds, Databases) and detect unexpected spend anomalies. Runaway backtesting jobs, un-throttled market data streaming across Availability Zones (cross-AZ egress tax), or idle GPU instances can cause cloud bills to surge tenfold. This module calculates rolling baseline statistics and uses $Z$-score thresholds to flag cost anomalies.

## Prerequisites

- Daily or hourly cloud cost telemetry records categorized by `service`, `category` (Compute, Storage, NetworkEgress), and `environment`.
- Baseline historical period (e.g. 14 days of spend history).

## Workflow

1. **Telemetry Ingestion**: Ingest cost records ($C_{t, \text{service}}$) and trading volume metrics ($V_t$).
2. **Rolling Baseline Calculation**:
   - Compute rolling mean $\mu$ and standard deviation $\sigma$ over baseline window $W$.
3. **Anomaly & Spike Audit**:
   - Compute $Z$-score: $Z_t = \frac{C_t - \mu}{\sigma + \epsilon}$.
   - Flag `CRITICAL` if $Z_t \ge 3.0$ and percentage increase $> 50\%$.
   - Flag `WARNING` if $Z_t \ge 2.0$.
4. **Unit Economics Tracking**:
   - Compute unit cost: $\text{Unit Cost} = \frac{C_{\text{total}}}{\text{Total Trades Executed}}$.
5. **FinOps Alert Generation**: Produce remediation recommendations (e.g. terminate idle instances, pin microservices to single AZ).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Evaluating Total Spend Without Volume Context**: Flagging a cost spike during an extreme volatility market day when high trading volume naturally increases AWS Lambda / API gateway fees. Unit cost ($\frac{\text{Spend}}{\text{Trades}}$) must be evaluated.
- **Ignoring Cross-AZ Network Egress**: Failing to tag cross-Availability-Zone traffic, missing the $0.01/GB "hidden tax" of inter-AZ data transfers.
- **24-Hour Billing Delay**: Relying exclusively on end-of-day cloud billing exports instead of hourly telemetry metrics.

## Verification

- Instantiate `CloudCostAnomalyDetector`. Feed 14 days of baseline compute spend around $100/day ($\sigma \approx 5.0$). Submit a 15th day spend record of $500 (Z \approx 80$). Verify the detector flags a `CRITICAL` cost anomaly for `Compute`. Test normal day spend ($102) and verify status is `NORMAL`.
- Run `python scripts/test_cloud_cost_anomaly_detector.py`.

## Related Skills

- `cross-region-data-replication-lag-monitoring`
- `cross-strategy-shared-infrastructure-resource-contention`
---
