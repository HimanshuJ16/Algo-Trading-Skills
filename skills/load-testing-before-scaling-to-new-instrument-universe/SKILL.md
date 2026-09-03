---
name: load-testing-before-scaling-to-new-instrument-universe
description: >-
  Use as the gate before expanding an instrument universe, projecting tick throughput,
  order-book memory, bandwidth and database write IOPS from measured per-symbol figures
  to decide whether a replay test is warranted.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: load-testing, universe-scaling, throughput, memory-footprint, network-bandwidth, capacity-planning, hft-infrastructure
  brokers_frameworks: "k6 / Gatling Load Benchmarks; Prometheus / Grafana Observability; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when expanding a strategy to a larger instrument universe — 50 S&P 500 names to 3,000 Russell 3000 names, or a domestic equity book to a global equity/options universe. Universe growth scales tick throughput (msg/sec), L2 order book memory (GB), network bandwidth (Mbps) and database write IOPS roughly linearly in symbol count, and the failure shows up at market open under volatility, not in a quiet backtest: exhausted memory, dropped WebSocket frames, a saturated writer, and a strategy trading on stale books.

For EU/EEA investment firms this is also a regulatory trigger, not just an engineering one. ESMA lists "deploying the algorithm in new instruments, venues, or asset classes" as a material change warranting retesting, and RTS 6 Art. 10 requires stress testing that systems can withstand increased order flows as part of the annual self-assessment. See `references/standards.md` for who is bound and by what.

## When NOT to Use

- **As a load test.** Nothing here observes a running system. It is a first-order projection from per-symbol figures *you* measured, and its job is to tell you whether a replay is worth running and at what rate. A `LOAD_TEST_PASSED_READY_TO_SCALE` verdict is a licence to run the replay, not a substitute for it.
- **When you have not measured the per-symbol inputs.** Every default in `UniverseScaleSpec` is an illustrative placeholder, and each scales the projection linearly. Unmeasured inputs give a confidently wrong verdict.
- **For CPU core sizing or wire-accurate bandwidth.** This skill gates on memory, network and storage IO only, and charges payload bytes rather than packet framing. Use `capacity-planning-for-symbol-universe-growth`, which models cores, per-packet framing, feed batching, A/B redundancy and retransmits — then feed its bandwidth ratio back in here as `wire_overhead_factor`.
- **When per-symbol rates are heavily skewed and you need a tight answer.** One uniform average multiplied by symbol count understates the handful of hot names that actually break a feed handler. Model liquidity tiers and audit each tier.

## Prerequisites

- Measured universe scale inputs: `current_universe_size`, `target_universe_size`, `avg_ticks_sec_per_symbol`, `peak_volatility_multiplier`, `bytes_per_tick`, `memory_mb_per_orderbook`, `db_write_fraction`.
- Measured overhead factors, or a deliberate decision to leave them at their defaults: `memory_allocation_buffer` (1.25), `wire_overhead_factor` (1.0 = payload only), `ticks_per_write_io` (1.0 = one IO per tick).
- Hardware capacity: `available_ram_gb`, `max_network_mbps`, `max_db_iops`.

## Units

Mixed by design, matching how each domain is quoted commercially:
- **Network is decimal** — 1 Mbps = 10⁶ bits/sec, so a "10 GbE" NIC is `max_network_mbps=10000`.
- **Memory is binary** — 1 GB = 1024 MB (GiB/MiB), matching how RAM is sold and reported.

## Workflow

1. **Measure the per-symbol inputs** on the current universe. Derive `peak_volatility_multiplier` from a peak observed on a **sub-second** window — a one-second average hides the microburst that overruns the socket buffer.
2. **Project peak load** for the target universe:
   - Peak throughput: $\text{Msg/sec}_{\text{peak}} = N_{\text{target}} \times \text{AvgTicksPerSec} \times \text{PeakMultiplier}$
   - Order book memory: $\text{RAM}_{\text{gb}} = \dfrac{N_{\text{target}} \times \text{MB\_per\_book} \times \text{AllocBuffer}}{1024}$
   - Network: $\text{Mbps} = \dfrac{\text{Msg/sec}_{\text{peak}} \times \text{BytesPerTick} \times \text{WireFactor} \times 8}{10^6}$
   - Storage: $\text{IOPS} = \dfrac{\text{Msg/sec}_{\text{peak}} \times \text{WriteFraction}}{\text{TicksPerWriteIO}}$
3. **Audit utilization** against `max_safe_utilization_pct` (default 80.0). The comparison is made on unrounded values — rounding an 80.04% projection to 80.0% would let it pass an 80.0% ceiling.
4. **Read every breach.** `status` names one breach in priority order — memory, then network, then IOPS — and is one of `LOAD_TEST_PASSED_READY_TO_SCALE`, `LOAD_TEST_FAILED_MEMORY_EXCEEDED`, `LOAD_TEST_FAILED_NETWORK_EXCEEDED`, `LOAD_TEST_FAILED_IOPS_EXCEEDED` (exported as `STATUS_*` constants). `breached_resources` lists them all. Before buying hardware for a breach, check the modelling assumption behind it: a network breach at `wire_overhead_factor=1.0` is understated, and an IOPS breach at `ticks_per_write_io=1.0` may vanish once the writer's real batch factor is used.
5. **Replay before scaling.** Run captured market data through the full pipeline at the projected peak rate, against the real database rather than a cache, then add instruments in tranches.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Scaling an average and calling it a peak.** Sizing for 1 tick/sec average when market open delivers 10x, and measuring that peak over a one-second window that averages the microburst away. Related but distinct: RTS 6 Art. 10's stress level is twice the highest volume *observed* over six months, which is not the same quantity as a multiple of an average — if your peak-to-average ratio is 8x, a 5x multiplier on the average is below that floor.
- **Trusting the per-symbol defaults.** `memory_mb_per_orderbook=20.0` is a placeholder for a Python-object-heavy full-depth book, not a measured constant; a compact fixed-depth array book is kilobytes. It drives the entire RAM verdict linearly. Measure with an RSS delta or `tracemalloc`.
- **Reading `status` and stopping.** A 5,000-symbol projection can breach RAM at 191% *and* write IOPS at 250% while `status` reports only the memory failure. Fix the RAM, re-run, and the storage layer is still over the ceiling. Read `breached_resources`.
- **Treating payload bytes as wire bandwidth.** Framing is charged per packet, and A/B multicast redundancy doubles the bill. With `wire_overhead_factor=1.0` the bandwidth figure is payload-only and therefore under-states the real load — the dangerous direction for a capacity gate.
- **Equating persisted ticks with IOPS.** A group-commit WAL or LSM-tree writer coalesces many ticks into one storage IO. Leaving `ticks_per_write_io=1.0` fails scale-ups the storage layer could actually absorb.
- **Load testing against a warm cache.** A synthetic benchmark that is served by local Redis never exercises end-to-end database write IOPS, which is exactly the axis that breaks first on a large universe.

## Verification

- 50 → 500 symbols on 64 GB / 1,000 Mbps / 50,000 IOPS: peak 25,000 msg/sec, RAM 12.207 GB (19.07%), 64.0 Mbps (6.4%), 12,500 IOPS (25%) ⟹ `LOAD_TEST_PASSED_READY_TO_SCALE`, `breached_resources == []`.
- 50 → 5,000 symbols on the same hardware ⟹ `LOAD_TEST_FAILED_MEMORY_EXCEEDED` with `breached_resources == ["ram", "db_iops"]` (RAM 190.7%, IOPS 250%) — the status names one breach, the list names both.
- A projection at 80.04% network utilization fails the 80.0% ceiling even though it rounds to 80.0% at one decimal place.
- A negative or zero `HardwareCapacitySpec` capacity raises `ValueError` rather than inverting the ratio into a negative utilization that reports PASSED.
- Run `python -m unittest discover -s skills/load-testing-before-scaling-to-new-instrument-universe/scripts`.

## Related Skills

- `capacity-planning-for-symbol-universe-growth`
- `historical-tick-data-storage-and-compaction`
- `cross-region-data-replication-lag-monitoring`
- `tick-buffering-burst-handling`
- `matching-engine-throttle-and-message-gapping-detection`
