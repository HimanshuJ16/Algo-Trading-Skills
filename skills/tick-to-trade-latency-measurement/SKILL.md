---
name: tick-to-trade-latency-measurement
description: "Institutional high-frequency trading (HFT) latency profiling skill for sub-microsecond Tick-to-Trade (T2T) measurement, hardware timestamping, stage-by-stage pipeline decomposition, and percentile SLA breach monitoring."
domain: Market Microstructure
subdomain: Ultra-Low Latency & High-Frequency Trading
tags:
- latency
- tick-to-trade
- hft
- low-latency
- ptp-clock-sync
- kernel-bypass
- hardware-timestamping
- percentile-sla
brokers_frameworks:
- solarflare-onload
- dpdk
- fpga
- quickfix
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when measuring, profiling, and optimizing **Tick-to-Trade (T2T) latency** in high-frequency trading (HFT), market making, or algorithmic execution infrastructure.

This skill provides institutional mechanisms to:
- Capture sub-microsecond timestamped event samples across all pipeline stages: NIC Ingress ($t_0 \to t_1$), Feed Handler Parsing ($t_1 \to t_2$), Strategy & Alpha Engine ($t_2 \to t_3$), Order Formatting & Serialization ($t_3 \to t_4$), and NIC Egress ($t_4 \to t_5$).
- Evaluate statistical distributions including Average, $P_{50}$ (Median), $P_{90}$, $P_{99}$, $P_{99.9}$, Max (Tail Spikes), and Jitter (standard deviation).
- Monitor SLA breaches against strict latency targets (e.g. $P_{50} \le 5.0\ \mu\text{s}$, $P_{99} \le 15.0\ \mu\text{s}$).
- Identify internal processing bottlenecks and kernel/OS context switch delays.

## Prerequisites

- Python 3.9+
- Hardware timestamping support on NIC (Solarflare / AMD Onload, Mellanox ConnectX, or FPGA capture cards).
- PTP (Precision Time Protocol IEEE 1588v2) hardware clock synchronization across trading hosts (< 100 ns skew).
- Monotonic nanosecond timestamp sources (e.g., `clock_gettime(CLOCK_MONOTONIC_RAW)` or `rdtsc`).

## Workflow

1. **Configure SLA Targets**: Instantiate `SLAConfig` with target bounds (`max_p50_us`, `max_p99_us`, `max_p999_us`, `max_tail_us`).
2. **Collect Nanosecond Timestamps**: For each incoming market tick that triggers an order, record a `LatencySample` containing `ingress_ns`, `decoded_ns`, `strategy_ns`, `serialized_ns`, and `egress_ns`.
3. **Validate Sample Monotonicity**: Invoke `sample.validate()` to ensure timestamps are strictly non-decreasing and free from clock drift corruption.
4. **Record Samples**: Submit samples to `TickToTradeLatencyEngine.record_sample()`.
5. **Evaluate Distribution**: Call `evaluate_latency_distribution()` to compute aggregate percentiles ($P_{50}, P_{90}, P_{99}, P_{99.9}$, Max), standard deviation (jitter), per-stage microsecond breakdowns, and percentage contribution of each stage to total T2T latency.
6. **Detect SLA Breaches**: Inspect `summary.sla_breaches` to trigger automated alerts when tail latency or median thresholds are exceeded.

## Common Pitfalls

- **Using Software Timestamps (`gettimeofday` / Python `time.time`)**: Software timestamps include OS kernel interrupt and context switch overhead. Always use hardware NIC timestamps (`SO_TIMESTAMPING`) or CPU TSC counters (`rdtsc`).
- **Ignoring Clock Skew Across NIC & CPU**: Failing to synchronize hardware NIC clocks with CPU clocks introduces negative or corrupt latency deltas. Monotonicity validation is mandatory.
- **Focusing Solely on Average Latency**: In HFT, average latency masks dangerous tail spikes ($P_{99.9}$). A system with a 2 µs average but a 200 µs $P_{99}$ spike will suffer severe adverse selection during market volatility.
- **Memory Allocation / Garbage Collection Traps**: Memory allocation inside the critical path (C++ `malloc` or Python object allocation) creates catastrophic latency jitter. Pre-allocate sample ring buffers.

## Verification

Run the test suite to validate monotonic sample checks, percentile calculations, stage breakdowns, and SLA breach detection:

```bash
python -m unittest discover -s skills/tick-to-trade-latency-measurement/scripts
```

## Related Skills

- `hardware-timestamping-vs-software-timestamping-accuracy`
- `clock-synchronization-ptp-for-trading-hosts`
- `binary-protocol-parsing-for-low-latency-feeds`
- `memory-mapped-ring-buffer-for-ultra-low-latency`

