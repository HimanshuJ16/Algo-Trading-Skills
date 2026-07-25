---
name: cross-datacenter-clock-sync-validation
description: >-
  Use when deploying multi-region or cross-datacenter trading nodes (NY4, LD4, AWS us-east-1) to measure pairwise PTP/NTP clock drift, detect clock sync degradation (>1ms), and enforce safety vetoes on cross-region arbitration.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "clock-sync", "cross-datacenter", "ptp", "ntp", "clock-drift", "multi-region"]
brokers_frameworks: ["Clock Sync Validator", "Python Real-Time Engine"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating multi-region trading infrastructure (e.g., Chicago CME, New York Equinix NY4, London LD4) feeding a unified strategy engine or multi-region arbitration module. If server clocks drift across datacenters ($> 1\text{ms}$), cross-region tick ordering produces inverted timestamps ($t_{\text{LD4}} < t_{\text{NY4}}$), causing phantom latency arbitrage trades and bad orderbook state. This skill probes node clock offsets, audits PTP/NTP sync health, and blocks arbitration when clock drift breaches tolerance thresholds.

## Prerequisites

- Multi-datacenter server nodes with active NTP or PTP daemons (`chrony`, `ptp4l`).
- Maximum allowable inter-region clock drift threshold $\Delta \tau_{\text{max}}$ (e.g. 1.0 ms).

## Workflow

1. **Query Local & Remote Node Clock Offsets**:
   - Query PTP/NTP daemon metrics (`chronyc tracking`) or issue high-precision UDP NTP/PTP offset probes.

2. **Compute Pairwise Clock Drift $\Delta \tau_{AB}$**:
   - Calculate offset $\Delta \tau_{AB} = |T_A - T_B| - \frac{\text{RTT}}{2}$.

3. **Evaluate Clock Sync Health Tier**:
   - $\Delta \tau_{AB} \le 0.1\text{ms}$: `EXCELLENT` (PTP hardware sync).
   - $0.1\text{ms} < \Delta \tau_{AB} \le 1.0\text{ms}$: `ACCEPTABLE` (NTP sync).
   - $\Delta \tau_{AB} > 1.0\text{ms}$: `BREACH` (Block cross-region arbitration).

4. **Enforce Cross-Region Safety Veto**:
   - If any datacenter pair breaches $\Delta \tau_{\text{max}}$, flag `CLOCK_UNSYNC_VETO` and fall back to single-region mode.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Asymmetric Network Latency**: Assuming symmetric network RTT when computing NTP offset, introducing clock measurement errors on asymmetric internet routes.
- **Relying Solely on Local Clock Health**: Verifying local server NTP without querying remote datacenter clock status before arbitrating cross-region quotes.
- **Failing to Veto Trades During Clock Jitter**: Continuing cross-region order routing when PTP clock loses lock during network congestion.

## Verification

- Simulate clock drift of 0.2ms and verify `ACCEPTABLE` status.
- Simulate clock drift of 2.5ms and verify `CLOCK_UNSYNC_VETO` trigger.
- Run `python scripts/test_clock_sync_validator.py` and confirm 100% pass rate.

## Related Skills

- `high-frequency-time-synchronization-ptp-ntp`
- `multi-region-active-active-tick-ingestion`
- `market-data-feed-arbitration-across-vendors`
---
