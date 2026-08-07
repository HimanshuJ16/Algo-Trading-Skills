---
name: automated-rollback-triggers-on-anomaly-detection
description: Deployment ops engine that monitors post-deployment health metrics and
  triggers automated rollbacks if trading or technical anomalies are detected.
domain: deployment-ops
subdomain: deployment
tags:
- deployment
- ci-cd
- rollback
- anomaly-detection
- self-healing
brokers_frameworks:
- generic
version: "1.1.0"
author: System
license: MIT
---

## When to Use

Use this skill within your CI/CD pipeline immediately following a deployment of a trading algorithm (e.g., during a Canary Release or Blue/Green deployment). In algorithmic trading, milliseconds of malfunction can cause catastrophic financial damage. 

This "self-healing" engine continuously monitors post-deployment telemetry (technical metrics like latency/errors, and trading metrics like order reject rates). If any metric breaches the predefined safety threshold, the engine instantly fires an automated rollback trigger, minimizing Mean Time to Recovery (MTTR).

## Prerequisites

- Python 3.9+
- A telemetry aggregator (e.g., Prometheus, Datadog) capable of supplying real-time `DeploymentHealthMetrics`.
- Integration with a CI/CD controller (e.g., Kubernetes, GitHub Actions, Jenkins) to execute the rollback script.

## Workflow

1. **Deploy**: The new algorithm version `v2.1` is deployed.
2. **Monitor**: The `AutomatedRollbackEngine` receives health metrics every second for the first 5 minutes post-deployment.
3. **Analyze**: The engine compares metrics (e.g., `order_reject_rate`, `latency_ms`) against the `RollbackThresholdConfig`.
4. **Trigger**: If the order reject rate spikes to 10% (exceeding the 1% threshold), the engine flags an anomaly and sets `should_rollback = True`.
5. **Action**: The CI/CD pipeline intercepts the flag and reverts the production state to `v2.0` automatically.

## Common Pitfalls

- **Ignoring Trading Metrics**: Monitoring only CPU and Memory, while failing to monitor financial metrics like runaway order rates or exchange reject rates.
- **Flaky Thresholds**: Setting thresholds too tight, causing false-positive rollbacks during normal market volatility.

## Verification

Run `python scripts/test_anomaly_rollback_trigger.py` to confirm that the engine successfully detects anomalies and triggers the rollback state.

## Related Skills

- `canary-releases-for-strategy-code-changes`
- `blue-green-deployment-for-live-strategy-updates`
