# Pre-Flight / Sign-off Checklist — feed-handler-cpu-pinning-and-numa-awareness

Use this before considering the skill's implementation complete. Record the observed
value next to each item — a ticked box with no value is not evidence.

## Topology

- [ ] **Discovery source confirmed:** `topology_source == "sysfs"` and `affinity_backend != "none"`. If either fails, the items below marked (Linux) cannot be signed off and must be recorded as *not verified*.
- [ ] **Physical cores counted, not guessed:** `physical_core_count` came from distinct `(physical_package_id, core_id)` pairs. A `None` is recorded as unknown, never replaced with `logical // 2`.
- [ ] **NUMA map read from sysfs (Linux):** `numa_node_to_cpus` matches `/sys/devices/system/node/*/cpulist`. No CPU-index threshold is used anywhere in the plan.
- [ ] **cpuset checked:** `available_cpu_ids` compared against `online_cpu_ids` on the *unpinned* process; any gap is explained by a known cpuset/cgroup and the plan stays inside it.

## CPU plan

- [ ] **One process per physical core:** no two pinned processes share an SMT sibling pair.
- [ ] **Reserved siblings documented:** `sibling_cpus_to_reserve()` output recorded, and those CPUs are confirmed idle.
- [ ] **Single NUMA node per process:** `validate_core_selection().spans_numa_nodes` is `False`, or `allow_cross_numa=True` was used deliberately and the reason is written down.
- [ ] **Housekeeping headroom:** at least one CPU (and, where practical, CPU 0) left unpinned for OS, logging and monitoring.
- [ ] **Not over-subscribed:** pinned worker processes ≤ dedicated physical cores.

## Binding

- [ ] **Bind verified by read-back:** `report.is_success` is `True` and `report.assigned_cores` equals the requested set. A `False` blocks the deployment — it is not a warning.
- [ ] **Worker pinned, not the launcher:** confirmed the bound PID is the feed handler itself (a `fork(2)` child inherits the mask).
- [ ] **Failure paths exercised:** an intentionally bad request (offline CPU, a cpuset-forbidden CPU, or a cross-node set without `allow_cross_numa`) was confirmed to return `is_success=False` on this host.
- [ ] **Rollback recorded:** `report.previous_cores` captured.

## NUMA memory locality (Linux)

- [ ] **Audited at steady state:** `audit_numa_locality()` run after warm-up, not at startup (first-touch allocation).
- [ ] **Allocation ordered correctly:** bind happened before the handler's buffers were allocated, or the process was launched under `numactl --membind`/`--cpunodebind`.
- [ ] **Remote pages explained:** `remote_pages == 0`, or every remote region is accounted for by an `interleave` policy in `pages_per_node`.
- [ ] **Unknown recorded as unknown:** an `is_available=False` result is written up as *not verified*, never as local.

## Isolation configuration (outside this module — state the actual setting)

- [ ] `isolcpus=` / `cpuset.sched_load_balance` : ______________________
- [ ] `nohz_full=` : ______________________
- [ ] `rcu_nocbs=` : ______________________
- [ ] IRQ affinity moved off the pinned CPUs : ______________________
- [ ] **Understood:** pinning removes migration jitter only; the above control interference.

## Evidence

- [ ] **Latency measured before and after** on this host, tail percentiles recorded (no generic latency figure is claimed by this skill).
- [ ] **Automated testing:** `python -m unittest discover -s skills/feed-handler-cpu-pinning-and-numa-awareness/scripts` — 100% pass rate.

## Sign-off

- Host / instance type: ___________________________
- Reviewed by: ___________________________
- Date: ___________________________
