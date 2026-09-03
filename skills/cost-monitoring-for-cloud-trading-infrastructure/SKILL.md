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
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in cloud-hosted quantitative trading architectures (AWS, GCP, Azure) to monitor infrastructure costs (Compute, Data Egress, Market Data Feeds, Databases) and detect unexpected spend anomalies. Runaway backtesting jobs, un-throttled market data streaming across Availability Zones (cross-AZ egress tax), or idle GPU instances can cause cloud bills to surge tenfold. This module calculates rolling baseline statistics and uses $Z$-score thresholds to flag cost anomalies.

## When NOT to Use

- **You need real-time enforcement (budget kill switches, instance termination).** This module classifies and reports on telemetry it is given; it does not connect to cloud APIs or actuate anything.
- **You need vendor-native anomaly detection.** AWS Cost Anomaly Detection / GCP budget alerts already cover single-account baselines — use this when you need cross-cloud, trading-volume-normalized, environment-scoped analysis in your own pipeline.
- **You need to detect spend *collapsing*.** The gates are one-sided: only positive deviations escalate. A market-data subscription that lapses or a feed handler that dies — spend dropping to $0 — reports `NORMAL`. Detect that with liveness monitoring, not this module.
- **You are forecasting or optimizing spend.** Baseline statistics here are for spike detection, not capacity planning or rightsizing analysis.

## Prerequisites

- Daily or hourly cloud cost telemetry records categorized by `service`, `category` (Compute, Storage, NetworkEgress), and `environment`.
- Baseline historical period (e.g. 14 days of spend history) for the same service AND environment as the record being analyzed.
- **The caller windows the history.** `analyze_service_cost` filters by (service, environment) but does not sort by timestamp, deduplicate, or truncate — pass exactly the records that belong in the rolling window. Handing a year of history to a "14-day rolling baseline" silently widens it.

## Workflow

1. **Telemetry Ingestion**: Ingest cost records ($C_{t, \text{service}}$) and trading volume metrics ($V_t$). Costs may be negative (credits/refunds) but non-finite telemetry values are rejected — a NaN would otherwise make every threshold comparison False and silently classify the day as NORMAL.
2. **Rolling Baseline Calculation** (scoped to service + environment — never mix PROD and DEV spend for the same service):
   - Compute rolling mean $\mu$ and population standard deviation $\sigma$ over baseline window $W$.
   - If $\sigma \approx 0$ (flat spend, e.g. reserved capacity), the Z-score degenerates to the absolute dollar deviation from the mean.
3. **Anomaly & Spike Audit**:
   - Compute $Z$-score: $Z_t = \frac{C_t - \mu}{\sigma}$ (severity decisions use the unrounded value).
   - Flag `CRITICAL` if $Z_t \ge 3.0$ AND percentage increase vs mean $> 30\%$ — the dual gate keeps a $2 deviation on a large flat baseline from paging anyone.
   - Flag `WARNING` if $Z_t \ge 2.0$ **and** the deviation is material. On a flat baseline the "Z" is a dollar deviation, so it is scale-dependent: +\$3 on a \$100,000/day reserved-capacity baseline reaches $Z = 3.0$ at +0.003%. A `flat_baseline_min_pct_change` floor (default 1%, constructor-tunable) gates WARNING **in the flat case only** — a small percentage move on a genuinely varying baseline can still be a real outlier and is never suppressed.
   - If no baseline history exists for the service+environment, status is reported as baseline-UNKNOWN (treated as NORMAL with an explicit recommendation) — a brand-new runaway service cannot be z-scored until a baseline accrues; watch it through direct budget alarms meanwhile.
   - A \$0-mean baseline with positive spend reports an unbounded percentage increase (so the CRITICAL gate cannot be bypassed by a zero-cost baseline).
4. **Unit Economics Tracking**:
   - Compute unit cost: $\text{Unit Cost} = \frac{C_{\text{total}}}{\text{Total Trades Executed}}$. Always pass the real trade volume — with the default volume of 1.0, unit cost silently equals raw spend. Zero trades with positive spend reports an **infinite** unit cost (the worst unit economics there is — a halted strategy still burning compute), never \$0.00.
5. **FinOps Alert Generation**: Produce remediation recommendations (e.g. terminate idle instances, pin microservices to single AZ).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Evaluating Total Spend Without Volume Context**: Flagging a cost spike during an extreme volatility market day when high trading volume naturally increases AWS Lambda / API gateway fees. Unit cost ($\frac{\text{Spend}}{\text{Trades}}$) must be evaluated.
- **Mixing Environments in One Baseline**: baselining PROD spend against DEV/STAGING history for the same service distorts $\mu$ and $\sigma$ in both directions. Scope every baseline to (service, environment).
- **Ignoring Cross-AZ Network Egress**: Failing to tag cross-Availability-Zone traffic. AWS charges inter-AZ data transfer at $0.01/GB **per direction** (source cited in `references/standards.md`) — a GB moved across AZs effectively bills ~$0.02, which is why market-data fan-out across zones doubles the "hidden tax".
- **24-Hour Billing Delay**: Relying exclusively on end-of-day cloud billing exports instead of hourly telemetry metrics.
- **Flat Baselines Paging On-Call**: with $\sigma \approx 0$, any absolute deviation produces a huge Z, and that Z is denominated in dollars — so the same \$3 blip scores 3.0 on a \$100/day baseline and 3.0 on a \$100,000/day one. Two separate relative gates exist for this: the CRITICAL >30%-mean-increase gate and the WARNING `flat_baseline_min_pct_change` floor. Don't remove either, and don't assume the CRITICAL gate alone silences flat-baseline noise — it does not gate WARNING.
- **Reading \$0.00/Trade as Efficiency**: a unit cost of zero means zero *spend*, not zero waste. Spend with no trades is reported as infinite unit cost; treat `inf` as an alert, not as missing data.

## Verification

- Instantiate `CloudCostAnomalyDetector`. Feed 14 days of baseline compute spend around $100/day ($\sigma \approx 5.0$). Submit a 15th day spend record of $500 (Z \approx 80$). Verify the detector flags a `CRITICAL` cost anomaly for `Compute`. Test normal day spend ($102) and verify status is `NORMAL`.
- Add DEV-environment history records for the same service and verify the PROD baseline mean is unchanged (environment scoping).
- Submit a spend record with no matching history and verify the recommendation reports baseline-UNKNOWN rather than a clean bill of health.
- Feed 14 days of a perfectly flat \$100,000/day baseline and submit \$100,003. Verify the status is `NORMAL` (+0.003% is below the materiality floor) and the recommendation names the floor rather than reporting a clean bill of health.
- Submit a record with `trading_volume=0` and positive spend; verify `unit_cost_usd` is `inf`, not `0.0`.
- Run `python -m unittest discover -s skills/cost-monitoring-for-cloud-trading-infrastructure/scripts`.

## Related Skills

- `cross-region-data-replication-lag-monitoring`
- `cross-strategy-shared-infrastructure-resource-contention`
---
