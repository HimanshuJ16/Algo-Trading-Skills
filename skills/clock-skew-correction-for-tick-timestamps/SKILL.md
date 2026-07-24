---
name: clock-skew-correction-for-tick-timestamps
description: >-
  Use when processing market data feeds to dynamically estimate and correct local clock drift against exchange matching engine timestamps using EWMA filtering and network jitter rejection
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "clock-skew", "timestamp-calibration", "ewma-filter", "latency-measurement"]
brokers_frameworks: ["All Market Data Streams", "Fix Protocol", "WebSockets"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a trading engine ingests tick data from exchanges where precise timestamping is required for feature calculation, micro-structure analysis, or backtest parity. Local host system clocks drift relative to exchange atomic clocks due to NTP synchronization intervals, CPU thermal throttling, or virtual machine virtualization. If left uncorrected, clock skew distorts order book reconstruction and introduces false arbitrage signals. Implementing dynamic Exponentially Weighted Moving Average (EWMA) skew estimation with outlier network jitter rejection is mandatory.

## Prerequisites

- Exchange-provided tick timestamp ($T_{\text{exchange}}$) in payload.
- Local receipt timestamp ($T_{\text{local}}$) recorded at socket read time.
- Configured maximum acceptable clock drift threshold (e.g., $100\text{ms}$).

## Workflow

1. **Calculate Raw Time Delta**:
   - For each incoming tick, compute raw difference $\Delta_{\text{raw}} = T_{\text{exchange}} - T_{\text{local\_receipt}}$.

2. **Filter Network Jitter Spikes**:
   - Calculate Median Absolute Deviation (MAD) over recent samples.
   - Reject raw samples where $|\Delta_{\text{raw}} - \mu| > 3 \times \text{MAD}$ to prevent packet network transport delays from corrupting clock skew estimates.

3. **Update EWMA Clock Skew Estimate**:
   - Update rolling skew estimate: $\hat{\Delta}_t = \alpha \cdot \Delta_{\text{raw}} + (1 - \alpha) \cdot \hat{\Delta}_{t-1}$ (default $\alpha = 0.05$).

4. **Calibrate Tick Timestamps**:
   - Compute calibrated timestamp: $T_{\text{calibrated}} = T_{\text{local\_receipt}} + \hat{\Delta}_t$.

5. **Clock Drift Alarm Threshold**:
   - If $|\hat{\Delta}_t| > 100\text{ms}$, trigger alert notifying infrastructure operations to execute PTP/NTP clock resynchronization (`clock-synchronization-ptp-for-trading-hosts`).

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Network Latency with Clock Skew**: Mistaking one-way network packet transit time for local clock drift, over-correcting local timestamps.
- **Unfiltered Outlier Spikes**: Allowing TCP retransmission delays or network spikes to distort the clock skew filter.
- **Static Skew Assumptions**: Assuming local clock offset is constant throughout a trading session, ignoring thermal and NTP drift.

## Verification

- Submit ticks with constant $+25\text{ms}$ exchange clock offset and verify `ClockSkewCorrector` converges to $+25\text{ms}$ skew estimate.
- Submit network latency outlier spike ($+500\text{ms}$) and verify jitter filter rejects the outlier.
- Verify `calibrate_timestamp()` applies estimated skew accurately.
- Run unit test suite `python scripts/test_clock_skew_corrector.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `market-microstructure-latency`
- `clock-synchronization-ptp-for-trading-hosts`
---
