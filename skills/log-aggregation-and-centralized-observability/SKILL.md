---
name: log-aggregation-and-centralized-observability
description: >-
  Centralized logging and observability pipeline for distributed trading microservices, formatting structured JSON logs, redacting sensitive API keys/secrets, and monitoring error rate spikes for Grafana Loki / OpenTelemetry.
domain: System Architecture & Infrastructure
subdomain: Observability & Distributed Logging
tags: ["log-aggregation", "observability", "opentelemetry", "grafana-loki", "elk-stack", "structured-json", "pii-sanitization", "error-spike-alert"]
brokers_frameworks: ["OpenTelemetry Collector", "Grafana Loki API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying distributed algorithmic trading microservices (order routers, market data gateways, risk managers, execution engines) that emit logs across co-location servers. In low-latency trading, unformatted text logs scatter across servers and obscure critical system failures. This module formats standardized **Structured JSON Logs** with trace correlation IDs, redacts sensitive API keys and hot wallet private keys, downsamples high-frequency DEBUG logs, and monitors error log velocity to trigger real-time **Observability Error Spike Alerts**.

## Prerequisites

- Log record payload (`subsystem`: `ORDER_ROUTER`/`RISK_GATEWAY`, `level`: `DEBUG`/`INFO`/`WARN`/`ERROR`/`CRITICAL`, `message`, `correlation_id`, `metadata`).
- OpenTelemetry / Grafana Loki ingestion endpoint specification.

## Workflow

1. **Sensitive Key Redaction & Masking**:
   - Audit metadata dictionary keys (`api_key`, `secret`, `private_key`, `auth_header`, `password`).
   - Replace sensitive values with `'[REDACTED]'`.
2. **Structured JSON Log Construction**:
   - Format OpenTelemetry-compliant JSON payload containing `timestamp_iso`, `correlation_id`, `subsystem`, `level`, `message`, and sanitized `metadata`.
3. **Adaptive Log Rate Limiting & Sampling**:
   - Downsample `DEBUG` messages during high-volume spikes while preserving $100\%$ of `INFO`, `WARN`, `ERROR`, and `CRITICAL` records.
4. **Error Velocity Audit & Spike Alerting**:
   - Audit error count in active window. If errors $> 10\text{ errors/window} \implies$ Trigger `OBSERVABILITY_ERROR_SPIKE_ALERT`.
5. **Audit Report Generation**: Output structured `ObservabilityReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Leaking API Secrets in Log Aggregators**: Accidentally printing API secret keys or hot wallet private keys in plain text JSON logs sent to central storage.
- **Unstructured Text Logging**: Logging free-form strings (`print("order failed")`) missing correlation IDs, breaking automated Grafana querying and trace correlation.
- **Flooding Storage with High-Frequency DEBUG Ticks**: Logging every tick at `DEBUG` level without adaptive sampling, causing disk exhaustion on log servers.

## Verification

- Instantiate `CentralizedLogAggregatorEngine`. Ingest 100 log records including 15 ERROR logs and metadata containing `"api_key": "secret123"` $\implies$ verify `"api_key"` is masked to `'[REDACTED]'`, formats OpenTelemetry JSON payload, and triggers `OBSERVABILITY_ERROR_SPIKE_ALERT`.
- Run `python scripts/test_centralized_log_aggregator.py`.

## Related Skills

- `data-lineage-tracking-for-audit-and-debugging`
- `cross-vendor-timestamp-precision-reconciliation`
---
