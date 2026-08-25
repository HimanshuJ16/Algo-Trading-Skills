---
name: feed-handler-cpu-pinning-and-numa-awareness
description: Use when deploying ultra-low-latency feed handlers and strategy execution
  loops to pin worker processes to dedicated CPU cores and enforce NUMA node memory
  locality, removing scheduler migration jitter and cross-socket interconnect latency.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- cpu-pinning
- numa-awareness
- cpu-affinity
- low-latency
- os-optimization
brokers_frameworks:
- CPU Affinity Manager
- Linux sysfs topology ABI
- Python psutil
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a feed handler, order gateway, or strategy execution loop shows latency *jitter* that does not track market activity — a stable median with a heavy tail. When the kernel scheduler migrates a hot thread to another CPU it loses its warm L1/L2 working set; when it migrates it across sockets, every subsequent memory access to the original allocation crosses the QPI/UPI interconnect. Pinning the process to a chosen CPU set and confirming its pages are resident on the local NUMA node removes both effects.

Use it as a deployment/provisioning step: decide the CPU plan, apply it at process start, and **verify** it — `scripts/affinity_manager.py` reads the real topology from the Linux sysfs ABI, binds through `os.sched_setaffinity` (or psutil), reads the mask back, and audits NUMA residency from `/proc/<pid>/numa_maps`.

## When NOT to Use

- **As a substitute for CPU isolation.** `sched_setaffinity(2)` constrains *where* a task may run. It does not stop other runnable tasks, per-CPU kthreads, timer ticks, or interrupts from preempting it on that CPU. Excluding other work is boot/cgroup configuration (`isolcpus`, `cpuset.sched_load_balance`, `nohz_full`, `rcu_nocbs`, IRQ affinity) that this module cannot set and does not claim to verify.
- **On macOS.** There is no process-affinity API; `psutil.Process.cpu_affinity` does not exist there (availability is Linux, Windows, FreeBSD). The module reports failure rather than pretending.
- **For per-thread pinning.** Linux affinity is a per-thread attribute; `bind_process_affinity` moves *every* thread of the target process. Pinning individual feed-handler threads onto separate cores requires per-thread calls this module does not make.
- **When the latency problem is not jitter.** A slow parser, a syscall per tick, or a GC pause is not fixed by pinning — see `binary-protocol-parsing-for-low-latency-feeds` and `tick-to-trade-latency-measurement` before reaching for affinity.
- **On a cloud instance where the topology is not yours.** A burstable or shared-tenant vCPU is not a dedicated physical core, and the guest-visible NUMA map may not reflect the host. Pin only where the instance type guarantees dedicated cores.

## Prerequisites

- Linux for full topology discovery: `/sys/devices/system/cpu/*/topology/` and `/sys/devices/system/node/*/cpulist` must be readable. Windows/FreeBSD can bind and verify a mask via psutil, but SMT and NUMA maps are unavailable there.
- Permission to set affinity: the same UID as the target process, or `CAP_SYS_NICE` — `sched_setaffinity(2)` returns `EPERM` otherwise.
- A written CPU plan naming which core runs which process, and which sibling CPUs are being deliberately left idle.
- `psutil` **only** if the host is Windows or FreeBSD. On Linux the module uses `os.sched_setaffinity` from the standard library and needs no third-party package (psutil is not a repo dependency).

## Workflow

1. **Discover the real topology — never infer it.**
   - `CPUAffinityNUMAManager().discover_topology()` reads online CPUs, SMT sibling lists (`thread_siblings_list`, modern alias `core_cpus_list`), `(physical_package_id, core_id)` pairs, and the NUMA node → CPU map.
   - **Decision point — check `topology_source` and `numa_topology_available` first.** If they read `"unavailable"` / `False`, the host cannot answer NUMA questions; every locality check below degrades to "could not verify", and you must not record the deployment as NUMA-verified.
   - **Decision point — CPU index does not imply NUMA node.** Many BIOSes enumerate CPUs round-robin across sockets, so CPU 4 on an 8-CPU box can be on node 1. Read `cpu_to_numa_node`; any threshold rule ("cores below 8 are node 0") is wrong on a large fraction of real hardware.

2. **Choose the CPU set, then reserve its SMT siblings.**
   - `sibling_cpus_to_reserve([2])` returns the CPUs sharing a physical core with the selection. Those CPUs must carry no other work — an unrelated process on a sibling thread contends for the same core's execution units.
   - Keep the whole selection on one NUMA node. `validate_core_selection()` reports `spans_numa_nodes` before anything is applied.

3. **Bind, then verify the mask by reading it back.**
   ```python
   mgr = CPUAffinityNUMAManager()
   report = mgr.bind_process_affinity([2])          # cross-NUMA rejected by default
   if not report.is_success:
       raise RuntimeError(report.message)           # do not start the handler unpinned
   ```
   - **Decision point — treat `is_success=False` as a deployment failure, not a warning.** A handler that believes it is pinned but is not attributes its jitter to the network or the exchange for as long as it runs.
   - The module compares the read-back mask against the request. A cpuset can silently narrow it on Linux ("restrictions are silently imposed by the kernel"); on Windows a mask crossing a processor-group boundary fails or narrows. Both are reported as failures.
   - `allow_cross_numa=True` exists so spanning sockets is a deliberate, recorded decision rather than an accident.

4. **Audit NUMA memory locality after the process has allocated.**
   - `mgr.audit_numa_locality()` sums the `N<node>=<nr_pages>` counters in `/proc/<pid>/numa_maps` and compares them against the nodes local to the current affinity.
   - **Decision point — run this after warm-up, not at startup.** Linux allocates on first touch, so a process audited before it has populated its buffers reports almost nothing. Pages allocated *before* the bind stay where they were: bind first, then allocate, or launch under `numactl --membind`.
   - **Decision point — read `pages_per_node` before calling remote pages a defect.** A region under an `interleave` policy is remote by design; a `bind`/`default` region on the wrong node is not.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a bind that never happened.** The dangerous failure here is silent: a fallback path that returns "pinned" when the affinity API is missing, the call was refused, or the mask was narrowed. Every such path must return failure — a wrong pinning report sends the next month of latency investigation to the wrong subsystem.
- **Deriving the NUMA node from the core number.** `node = 0 if core < 8 else 1` is a guess that happens to hold on one machine. On round-robin CPU enumeration it puts half the "local" allocations across the interconnect. Read `/sys/devices/system/node/*/cpulist`.
- **Guessing physical cores as `logical // 2`.** That assumes SMT is enabled and 2-way everywhere. It is wrong on SMT-disabled hosts, on 4-way SMT (POWER), and inside containers. Count distinct `(physical_package_id, core_id)` pairs, or report "unknown".
- **Sharing an SMT sibling pair between two processes.** Pinning the feed handler to CPU 2 and the DB logger to CPU 3 puts both on one physical core. The plan looks like two dedicated cores and behaves like one contended one.
- **Assuming pinning gives you the core.** Without `isolcpus`/`cpuset.sched_load_balance`, `nohz_full`, `rcu_nocbs` and IRQ affinity, the kernel still schedules other work and interrupts onto that CPU. Pinning removes migration jitter, not interference.
- **Fighting a cpuset you cannot see.** Under a container or cgroup the permitted mask is a subset of the online CPUs and the kernel imposes it silently, so plan against `os.sched_getaffinity`, not `os.cpu_count()`. Do not invert that into a hard pre-check either: the current mask narrows the moment a process is pinned, so treating it as a permission ceiling makes re-pinning that process impossible. Warn on it and let the kernel's `EINVAL` be the authority.
- **Auditing NUMA locality before the process has touched its memory.** First-touch allocation means an audit at startup measures nothing; pages allocated before the bind also stay on the old node.
- **Over-subscribing.** Pinning more worker processes than dedicated physical cores reintroduces the context switching the exercise was meant to remove, now on a core with nowhere to migrate.
- **Assuming a Windows mask can span processor groups.** "On a system with more than 64 processors, the affinity mask must specify processors in a single processor group."

## Verification

- `discover_topology()` on a machine with a known layout: confirm `physical_core_count` equals the count of distinct `(package, core)` pairs (not `logical // 2`), `numa_node_to_cpus` matches `/sys/devices/system/node/*/cpulist`, and `available_cpu_ids` matches `os.sched_getaffinity(0)` under any active cpuset.
- Bind to a single core and confirm the report is verified by read-back: `report.is_success` is true, `report.assigned_cores == [core]`, and `report.numa_node_id` equals the node sysfs reports for that CPU — for a CPU whose index and node disagree, not just CPU 0.
- Negative checks, each of which must return `is_success=False` **without** reaching the OS: empty selection, duplicate ids, negative/non-integer ids, an offline CPU, and a selection spanning NUMA nodes without `allow_cross_numa`. A CPU outside the process's *current* mask is a warning instead — re-pinning an already-pinned process must stay possible — and a CPU a cpuset genuinely forbids must surface as a classified `EINVAL` failure.
- Failure-reporting checks: with no affinity backend the report must say the process is **not** pinned; a backend that narrows the mask must produce a read-back mismatch failure; `EPERM` and `ESRCH` must be classified in the message rather than raised.
- `audit_numa_locality()` against a `numa_maps` fixture with pages on a remote node: confirm `remote_pages` and `remote_page_fraction`, and that a missing `numa_maps` yields `is_available=False` with `is_local=False` — never "local by default".
- Run `python -m unittest discover -s skills/feed-handler-cpu-pinning-and-numa-awareness/scripts` and confirm 100% pass rate.

## Related Skills

- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `binary-protocol-parsing-for-low-latency-feeds`
- `clock-synchronization-ptp-for-trading-hosts`
- `tick-to-trade-latency-measurement`
- `network-interface-level-tick-timestamping`
