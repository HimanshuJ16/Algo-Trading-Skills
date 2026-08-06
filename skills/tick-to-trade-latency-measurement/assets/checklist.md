# Institutional Tick-to-Trade Latency Profiling Checklist

## System & Infrastructure Preparation
- [ ] **Hardware Timestamping Enablement**: Enable `SO_TIMESTAMPING` or Solarflare EF_VI / OpenOnload hardware packet capture on NIC.
- [ ] **PTP Clock Synchronization**: Verify IEEE 1588v2 PTP daemon (`ptp4l`) is locked to grandmaster clock with $< 100\ \text{ns}$ offset.
- [ ] **CPU Core Pinning & Isolation**: Isolate CPU cores via `isolcpus` / `taskset` and disable CPU frequency scaling (governor = `performance`).
- [ ] **Kernel Bypass Drivers**: Configure OpenOnload or DPDK driver ring buffer sizes to prevent packet drops during tick bursts.

## Latency Measurement & Stage Profiling
- [ ] **Timestamp Monotonicity Validation**: Enforce $t_0 \le t_1 \le t_2 \le t_3 \le t_4 \le t_5$ check on recorded samples.
- [ ] **Per-Stage Microsecond Breakdown**: Measure stage deltas (NIC Ingress, Decoder, Strategy, Serializer, NIC Egress).
- [ ] **Percentile Distribution Calculation**: Compute $P_{50}$ (Median), $P_{90}$, $P_{99}$, $P_{99.9}$, and Maximum tail latency.
- [ ] **Jitter Calculation**: Measure standard deviation ($\sigma$) of T2T latency across trading sessions.

## SLA Breach Monitoring & Optimizations
- [ ] **Percentile SLA Verification**: Verify $P_{50} \le 5.0\ \mu\text{s}$ and $P_{99} \le 15.0\ \mu\text{s}$ targets.
- [ ] **Zero-Allocation Critical Path**: Audit C++ / Python execution path to confirm zero dynamic memory allocations (`malloc` / GC) during tick processing.
- [ ] **Automated SLA Alerting**: Execute `evaluate_latency_distribution()` and verify zero SLA breach warnings before production deployment.
