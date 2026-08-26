# Workflows for Load Testing Before Scaling to a New Instrument Universe

The engine in `scripts/infrastructure_load_tester.py` performs steps 2-4. Steps 1 and 5
are yours: the projection is only as good as the measurements fed into it, and a passing
projection is a licence to run a load test, not a substitute for one.

## 1. Measure the per-symbol inputs on the current universe

Every default in `UniverseScaleSpec` is an illustrative placeholder, and each one scales the
projection linearly. Before running the audit, measure on your own stack:

- `avg_ticks_sec_per_symbol` — mean messages/sec per symbol over a full session.
- `peak_volatility_multiplier` — observed peak divided by that average. Derive the peak from
  a **sub-second** window (100 ms or 10 ms), not a one-second average: a one-second mean
  hides the microburst that actually overruns a socket buffer.
- `bytes_per_tick` — measured payload size. A verbose JSON WebSocket message is hundreds of
  bytes; a binary exchange feed is tens.
- `memory_mb_per_orderbook` — RSS delta or `tracemalloc` snapshot per subscribed symbol,
  taken after the books have reached steady-state depth. Do not estimate this from the
  theoretical size of a price-level array; language and container overhead dominate.
- `db_write_fraction` and `ticks_per_write_io` — the fraction of ticks you persist, and how
  many of them your writer coalesces into a single storage IO.

Record where each number came from. An unmeasured input produces a confidently wrong verdict.

## 2. Project peak load for the target universe

`InfrastructureLoadTesterEngine.audit_universe_scaling_load` computes, for
`target_universe_size` symbols:

| Quantity | Formula | Units |
|---|---|---|
| Peak message rate | `N x avg_ticks_sec_per_symbol x peak_volatility_multiplier` | msg/sec |
| Order book RAM | `N x memory_mb_per_orderbook x memory_allocation_buffer / 1024` | GB (binary) |
| Network bandwidth | `peak_msg_rate x bytes_per_tick x wire_overhead_factor x 8 / 1e6` | Mbps (decimal) |
| DB write IOPS | `peak_msg_rate x db_write_fraction / ticks_per_write_io` | IOPS |

Both specs are validated on construction *and* again at audit time, because dataclasses are
mutable and a non-positive capacity would otherwise invert a utilization ratio into a
negative number that clears the ceiling check as a PASS.

## 3. Audit utilization against the ceiling

Each projection is divided by the corresponding `HardwareCapacitySpec` figure and compared —
**unrounded** — against `max_safe_utilization_pct` (default 80.0). Rounding a utilization
figure before the comparison lets an 80.04% projection pass an 80.0% ceiling.

CPU cores are deliberately not modelled here; see `capacity-planning-for-symbol-universe-growth`.

## 4. Read every breach, not just the status

`status` names one breach in priority order (memory, then network, then IOPS).
`breached_resources` lists them all. Reading only `status` leads to the classic loop of
buying RAM, re-running, and discovering the storage layer was over the ceiling all along.

Decision points:

- **`LOAD_TEST_FAILED_MEMORY_EXCEEDED`** — before buying RAM, check whether you need
  full-depth books for every symbol. Capping depth, or keeping top-of-book only for the tail
  of the universe, usually beats scaling the box.
- **`LOAD_TEST_FAILED_NETWORK_EXCEEDED`** — check `wire_overhead_factor` first. If it is
  still 1.0 the figure is payload-only and the real breach is worse than reported. Consider a
  filtered subscription or conflated feed before a larger NIC.
- **`LOAD_TEST_FAILED_IOPS_EXCEEDED`** — check `ticks_per_write_io` first. A default of 1.0
  assumes one IO per tick and will fail a plan that a batching writer could absorb.
- **`LOAD_TEST_PASSED_READY_TO_SCALE`** — a projection passed. Go to step 5.

## 5. Run the actual load test, then scale incrementally

The projection tells you the rate to test at. Replay real captured market data at the
projected peak message rate against the full pipeline — feed handler, book builder, strategy,
and the **real** database, not a cache in front of it — and confirm the measured utilization
matches the projection. Then add instruments in tranches rather than all at once, watching
for queue depth growth, dropped sequence numbers and GC pauses at each step.

For EU/EEA investment firms this is not optional housekeeping: ESMA treats deployment into
new instruments or asset classes as a material change warranting retesting, and RTS 6 Art. 10
requires stress testing capacity as part of the annual self-assessment. See
`references/standards.md` for exactly who is bound and by what.
