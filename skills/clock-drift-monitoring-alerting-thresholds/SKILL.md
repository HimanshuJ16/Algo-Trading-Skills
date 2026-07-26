---
name: clock-drift-monitoring-alerting-thresholds
description: >-
  Infrastructure compliance engine that continuously monitors PTP (Precision Time Protocol) clock drift against strict MiFID II RTS 25 microsecond thresholds, triggering automated circuit breakers.
domain: Compliance & Regulation
subdomain: Infrastructure
tags: ["mifid-ii", "clock-drift", "ptp", "hft", "compliance", "latency"]
brokers_frameworks: ["Linux PTP", "Generic Infrastructure"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in High-Frequency Trading (HFT) or algorithmic environments subject to MiFID II RTS 25 (or similar US SEC consolidated audit trail requirements). Regulators require HFT firms to trace timestamps to Coordinated Universal Time (UTC) with a maximum divergence of **100 microseconds**. If your server's clock drifts beyond this limit due to PTP failure or network asymmetric delay, all trades executed during that window are non-compliant.

## Prerequisites

- A Precision Time Protocol (PTP) daemon running on the host (e.g., `ptp4l` or `phc2sys`).
- Trading infrastructure capable of receiving out-of-band alerts (or a kill-switch) from a compliance monitor.

## Workflow

1. **Telemetry Polling**: The monitor polls the PTP daemon (or parses its hardware management logs) at high frequency to read the absolute `clock_offset`.
2. **Threshold Evaluation**:
   - `Offset < 50µs`: Green. System is healthy.
   - `Offset >= 50µs`: Warning. A non-blocking alert is sent to operations.
   - `Offset >= 100µs`: Critical (Regulatory Breach). The monitor immediately triggers a hardware or software kill-switch to halt the trading engine.
3. **State Transitions**: The monitor also watches for critical PTP state changes (e.g., transition into `HOLDOVER` mode when the Grandmaster connection is lost).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Monitoring Software Clocks Only**: NTP (Network Time Protocol) operates at the software level and is highly susceptible to OS jitter (often > 1 millisecond). HFT requires hardware timestamping (PTP) at the NIC level.
- **Alert Fatigue**: Setting the warning threshold too tightly (e.g., 5µs) in a network with inherent jitter, causing operations to ignore alarms until a real 100µs breach occurs.
- **No Automated Kill-Switch**: Relying on a human to read a Slack alert and manually kill the trading engine. At HFT speeds, thousands of non-compliant trades will be executed before a human can react.

## Verification

- Simulate a PTP log feed with a 120µs offset. Verify that the monitor instantly trips the `CRITICAL` state and triggers the mock trading engine kill-switch.
- Run `python scripts/test_clock_drift_monitor.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `cross-venue-latency-arbitrage-defensive-design`
