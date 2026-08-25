# Deep Workflow Reference — feed-handler-cpu-pinning-and-numa-awareness

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Topology discovery

```python
from affinity_manager import CPUAffinityNUMAManager

mgr = CPUAffinityNUMAManager()          # sysfs_root="/sys", proc_root="/proc"
topo = mgr.discover_topology()
```

Check, in this order:

| Field | Why it matters |
|---|---|
| `topology_source` | `"sysfs"` = real topology. `"unavailable"` = non-Linux or unreadable sysfs; SMT and NUMA answers below are absent, not "clean". |
| `affinity_backend` | `"none"` means the host cannot pin at all — stop here. |
| `numa_topology_available` | `False` means every locality statement in the deployment record must read "not verified". |
| `physical_core_count` | `None` when it could not be derived. It is never estimated from the logical count. |
| `available_cpu_ids` vs `online_cpu_ids` | The process's *current* mask vs all online CPUs. On a not-yet-pinned process a gap means a cpuset/cgroup is restricting it; plan inside `available_cpu_ids`. On an already-pinned process the gap is just the existing pinning. |
| `cpu_to_numa_node` | The authoritative CPU→node map. Do not substitute an index threshold. |

### 2. Building the CPU plan

For each latency-critical process, pick CPUs from one NUMA node, then determine what
must be kept idle:

```python
plan = {"feed_handler": [2], "strategy": [4]}
for name, cores in plan.items():
    reserve = mgr.sibling_cpus_to_reserve(cores)
    print(name, cores, "-> keep idle:", reserve)
```

Rules:

- One process per physical core. Two processes on an SMT sibling pair share one core's
  execution units regardless of how the plan is drawn.
- Every CPU in a process's set on the same NUMA node as that process's memory.
- Do not pin CPU 0 if avoidable: many distributions default IRQ and housekeeping work there.
- Leave at least one non-pinned CPU for the OS, logging, monitoring and the shell.
- Record the reserved sibling CPUs — they are the part of the plan an operator can most
  easily violate later by starting "just one small process".

### 3. Binding with verification

```python
report = mgr.bind_process_affinity([2])
if not report.is_success:
    raise RuntimeError(report.message)     # never start the handler unpinned
for w in report.warnings:
    log.warning(w)
```

Semantics:

- Validation runs before any OS call. Empty selections, duplicates, negative or
  non-integer ids and offline CPUs are refused without touching the kernel.
- A CPU outside `available_cpu_ids` is a **warning**, not a refusal: that field is the
  process's current mask, which narrows once it is pinned, so a hard check there would
  make re-pinning or widening an already-pinned process impossible. Where a cpuset truly
  forbids the CPU the kernel returns `EINVAL`, which is classified into the message.
- A selection spanning NUMA nodes is refused unless `allow_cross_numa=True`.
- After the write the mask is read back and compared as a set. A narrowed mask —
  possible under a cpuset on Linux, or across a Windows processor group — is a failure.
- `previous_cores` records the mask before the change, for the rollback note in the
  deployment record.
- `numa_node_id` is the shared node, or `-1` when the selection spans nodes or the map
  is unavailable. It is never inferred from the CPU index.
- `EPERM`, `EINVAL` and `ESRCH` are classified into the message rather than raised, so a
  supervisor can log and exit cleanly instead of dying on a traceback.

Bind the worker process itself, not its launcher: a `fork(2)` child inherits the mask
and it survives `execve(2)`, so pinning a supervisor silently pins every process it
starts.

### 4. NUMA memory locality audit

```python
locality = mgr.audit_numa_locality()       # after warm-up, not at startup
```

- Linux allocates on **first touch**: a process audited before it has populated its
  ring buffers and decoder tables has almost nothing to measure. Run the audit once the
  handler is at steady state.
- Pages allocated *before* the bind stay on the node they were allocated on. Order the
  startup as: bind → allocate → warm up → audit. Where that is not possible, launch
  under `numactl --membind=<node> --cpunodebind=<node>`.
- `pages_per_node` is reported raw. An `interleave` region is remote by design; a
  `default`/`bind` region on a remote node is the defect worth chasing.
- `is_available=False` means the file was unreadable (non-Linux, `CONFIG_NUMA=n`, or
  permissions). It is reported as unknown, never as local.
- The module reports residency only. Moving pages afterwards (`migratepages`) or
  preventing the problem (`numactl` at launch, first-touch on the pinned core) is an
  operational decision.

### 5. What this does not prove

The module proves two things: the affinity mask is what you asked for, and the resident
pages are on the local node. It does **not** prove the CPU is quiet. Interference from
other tasks, per-CPU kthreads, timer ticks and device interrupts is controlled by
`isolcpus` / `cpuset.sched_load_balance`, `nohz_full`, `rcu_nocbs` and IRQ affinity —
boot- and cgroup-level configuration outside this module's reach. State which of those
are in effect in the deployment record rather than implying pinning covers them.

## Production Implementation Reference

- Reference code: `scripts/affinity_manager.py`
  - `CPUAffinityNUMAManager` — `discover_topology`, `hyperthread_siblings`,
    `sibling_cpus_to_reserve`, `validate_core_selection`, `bind_process_affinity`,
    `audit_numa_locality`
  - Data classes: `CPUTopologyInfo`, `CoreSelectionAudit`, `AffinityBindingReport`,
    `NUMALocalityReport`
  - Helpers: `parse_cpu_list`, `detect_affinity_backend`, `AffinityBackend`
- Automated unit tests: `scripts/test_affinity_manager.py` — the suite builds a synthetic
  sysfs/proc tree for a known dual-socket SMT machine, so the parsing, validation and
  NUMA logic are exercised on any host without NUMA hardware, root, or psutil.
