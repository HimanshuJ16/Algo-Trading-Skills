---
name: market-data-latency-monitoring-per-vendor
description: >-
  Real-time microsecond market data latency decomposition engine, calculating P50, P90, P95, P99, P99.9 percentiles and jitter per vendor stream to audit exchange-to-app SLA thresholds.
domain: System Architecture & Infrastructure
subdomain: Latency Measurement & Vendor SLA Monitoring
tags: ["market-data", "latency-monitoring", "p99-percentile", "vendor-sla", "microsecond-timestamps", "jitter-monitoring", "hdrhistogram"]
brokers_frameworks: ["OpenTelemetry Histograms", "Prometheus Latency Metrics", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when monitoring low-latency market data streams across multiple commercial vendors (Bloomberg B-PIPE, Refinitiv ELEKTRON, Direct Exchange Feeds). Average latency metrics obscure tail-end latency spikes that ruin algorithmic execution. This module decomposes total latency into **Vendor Transport**, **Network Wire**, and **Local App Queue** components in microseconds, computing exact $P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$ percentiles and jitter ($\sigma$) per vendor to trigger real-time **Vendor Latency SLA Breach Alerts**.

## Prerequisites

- Microsecond tick latency samples (`t_exchange_us`, `t_vendor_us`, `t_local_nic_us`, `t_app_us`).
- Vendor SLA threshold specification (`max_p99_latency_us`: e.g., $500\ \mu\text{s}$).

## Workflow

1. **Microsecond Latency Decomposition**:
   - Calculate latency components:
     $$\Delta t_{\text{vendor\_us}} = T_{\text{vendor}} - T_{\text{exchange}}$$
     $$\Delta t_{\text{wire\_us}} = T_{\text{local\_nic}} - T_{\text{vendor}}$$
     $$\Delta t_{\text{proc\_us}} = T_{\text{app}} - T_{\text{local\_nic}}$$
     $$\Delta t_{\text{total\_us}} = T_{\text{app}} - T_{\text{exchange}}$$
2. **Percentile & Jitter Calculation**:
   - Compute statistical percentiles ($P_{50}, P_{90}, P_{95}, P_{99}, P_{99.9}$) and jitter ($\sigma_{\text{latency}}$) directly from sorted sample distributions.
3. **Vendor SLA Audit & Alerting**:
   - Audit $P_{99} \le \text{SLA\_limit}$.
   - If $P_{99} > \text{SLA\_limit} \implies$ Trigger `VENDOR_LATENCY_SLA_BREACH_ALERT`.
4. **Audit Report Generation**: Output structured `VendorLatencyReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Averaging Percentiles Across Nodes**: Averaging $P_{99}$ metrics across multiple servers, mathematically invalidating tail-end outlier detection.
- **Measuring Post-Kernel Latency**: Timestamping packets after OS kernel context switches instead of at the hardware NIC layer.
- **Relying on Average Latency**: Ignoring $P_{99.9}$ spikes caused by GC garbage collection pauses or thread lock contention.

## Verification

- Instantiate `MarketDataLatencyMonitorEngine`. Ingest 1,000 microsecond samples for Vendor A ($P_{50}=50\mu\text{s}$, $P_{99}=120\mu\text{s} \le 500\mu\text{s}$) $\implies$ verify `VENDOR_LATENCY_HEALTHY`. Ingest 1,000 samples for Vendor B ($P_{99}=1,250\mu\text{s} > 500\mu\text{s}$) $\implies$ verify `VENDOR_LATENCY_SLA_BREACH_ALERT`.
- Run `python scripts/test_market_data_latency_monitoring_per_vendor.py`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `cross-vendor-timestamp-precision-reconciliation`
---
