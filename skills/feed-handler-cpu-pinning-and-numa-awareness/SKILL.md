---
name: feed-handler-cpu-pinning-and-numa-awareness
description: >-
  Use when deploying ultra-low-latency feed handlers and strategy execution loops to pin worker processes to dedicated CPU cores and enforce NUMA node memory locality, eliminating OS context switching and cross-socket bus latency.
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "cpu-pinning", "numa-awareness", "cpu-affinity", "low-latency", "os-optimization"]
brokers_frameworks: ["CPU Affinity Manager", "Python psutil"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when optimizing latency-critical feed handler processes or high-frequency strategy execution threads. When OS kernel schedulers move feed handler threads across different CPU cores or NUMA sockets, L1/L2 cache invalidations and cross-UPI/QPI bus memory accesses introduce latency jitter spikes (e.g. 50 $\mu$s to 2 ms delay). Pinning feed handlers to dedicated physical CPU cores (`cpu_affinity([core_id])`) and ensuring NUMA node memory locality guarantees deterministic execution.

## Prerequisites

- Server hardware architecture with multi-core CPU and NUMA topology (e.g., dual-socket Intel Xeon / AMD EPYC).
- Operating System process permissions allowing CPU affinity assignment (`sched_setaffinity` on Linux, `SetProcessAffinityMask` on Windows).

## Workflow

1. **Discover System CPU & NUMA Topology**:
   - Query logical cores, physical cores, and NUMA node mapping using `psutil` or `os.sched_getaffinity`.

2. **Allocate Isolated CPU Cores**:
   - Assign dedicated physical cores (e.g., Core 2 for Market Data Ingestion, Core 3 for Strategy Engine) avoiding Hyper-Threaded sibling contention.

3. **Enforce Process CPU Affinity**:
   - Bind current process PID to target core list:
     ```python
     p = psutil.Process()
     p.cpu_affinity([target_core_id])
     ```

4. **Audit NUMA Node Memory Locality**:
   - Verify process memory allocations reside on the local NUMA node attached to the assigned CPU socket.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sharing Hyper-Threaded Cores**: Pinning feed handler to Core 2 and background DB logger to Core 2's HT sibling, leading to execution pipeline contention.
- **Cross-NUMA Memory Access**: Pinning process to CPU Socket 0 while allocating memory on NUMA Node 1, incurring heavy cross-socket interconnect latency.
- **Over-subscribing Cores**: Assigning more worker threads than available physical cores, causing OS thread context switching.

## Verification

- Bind process to target core list `[0]` and verify `p.cpu_affinity() == [0]`.
- Inspect NUMA node assignment and verify zero affinity violation.
- Run `python scripts/test_affinity_manager.py` and confirm 100% pass rate.

## Related Skills

- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `binary-protocol-parsing-for-low-latency-feeds`
- `high-frequency-time-synchronization-ptp-ntp`
---
