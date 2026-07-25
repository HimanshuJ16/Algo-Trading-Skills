---
name: model-monitoring-dashboard-for-non-technical-stakeholders
description: >-
  Use when deploying ML trading models to aggregate complex model health metrics (accuracy, staleness, concept drift, feature drift) into a non-technical traffic light (GREEN, AMBER, RED) health dashboard.
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "monitoring-dashboard", "model-health", "non-technical", "traffic-light", "risk-reporting"]
brokers_frameworks: ["Model Monitoring Dashboard Engine", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating live ML trading systems. Risk managers, compliance officers, and portfolio managers require clear, actionable visibility into model health without parsing complex ML statistical outputs (PSI, Kolmogorov-Smirnov stats, loss functions). This skill translates complex ML model telemetry into simple business-oriented Traffic Light (GREEN/AMBER/RED) statuses and plain-language action recommendations.

## Prerequisites

- Model telemetry inputs: current accuracy/loss, days since last retrain, feature drift PSI scores, prediction latency.
- Threshold configurations for AMBER warning and RED breach.

## Workflow

1. **Ingest Raw Model Telemetry**: Read accuracy, staleness days, feature drift score, and inference latency.
2. **Evaluate Sub-Component Health Statuses**:
   - Accuracy: GREEN if $\text{Accuracy} \ge 55\%$, AMBER if $50–54\%$, RED if $<50\%$.
   - Staleness: GREEN if $\le 14$ days, AMBER if $15–30$ days, RED if $>30$ days.
   - Feature Drift: GREEN if $\text{PSI} \le 0.10$, AMBER if $0.10–0.25$, RED if $>0.25$.
3. **Aggregate Overall Traffic Light Status**: Assign overall status equal to worst sub-component status.
4. **Generate Plain-Language Action Plan**: Output clear recommendation (e.g. "SCHEDULE_RETRAIN", "HALT_TRADING_IMMEDIATELY").

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Exposing Raw Statistical Jargon**: Displaying raw $p$-values to risk managers without translating into business risk actions.
- **Concealing Critical Breaches**: Averaging sub-component scores so a severe accuracy crash is masked by low latency.

## Verification

- Ingest healthy metrics, verify overall `GREEN` status and `NONE` action.
- Ingest high feature drift (PSI=0.30), verify `RED` status and `HALT_TRADING` recommendation.
- Run `python scripts/test_monitoring_dashboard.py` and confirm 100% pass rate.

## Related Skills

- `model-staleness-detection`
- `feature-importance-drift-monitoring`
- `concept-drift-vs-staleness-differentiation`
---
