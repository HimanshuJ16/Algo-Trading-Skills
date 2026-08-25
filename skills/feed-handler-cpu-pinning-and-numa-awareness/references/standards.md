# Standards & Platform Facts — feed-handler-cpu-pinning-and-numa-awareness

No regulator or standards body mandates CPU pinning. Everything below is OS and
hardware behaviour, cited to the primary documentation, plus the operational
conventions this skill applies on top of it.

## Kernel interfaces this skill reads and writes

| Interface | Path / call | What it actually provides |
|---|---|---|
| Online CPUs | `/sys/devices/system/cpu/online` | CPU-list string, format "0-3, 8-11, 14,17" per the sysfs ABI. |
| SMT siblings | `/sys/devices/system/cpu/cpuN/topology/thread_siblings_list` (modern alias `core_cpus_list`) | "human-readable list of CPUs within the same core". |
| Physical core identity | `.../topology/physical_package_id` + `.../topology/core_id` | Hardware socket and core ids. Distinct pairs = physical core count. |
| NUMA node → CPUs | `/sys/devices/system/node/nodeN/cpulist` | "The CPUs associated to the node." The only correct CPU→node source. |
| Set/read affinity (Linux) | `os.sched_setaffinity` / `os.sched_getaffinity` → `sched_setaffinity(2)` | Standard library; no third-party dependency. |
| Set/read affinity (Windows/FreeBSD) | `psutil.Process.cpu_affinity()` | Availability documented as "Linux, Windows, FreeBSD". |
| NUMA residency | `/proc/<pid>/numa_maps` | Read-only; one line per mapped range with `N<node>=<nr_pages>`, `anon=`, `dirty=`. |

## `sched_setaffinity(2)` semantics relied on

Source: [`sched_setaffinity(2)`, man7.org](https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html).

| Fact | Consequence for this skill |
|---|---|
| Affinity is "a per-thread attribute that can be adjusted independently for each of the threads in a thread group" | Binding a *process* moves all its threads; per-thread pinning needs per-thread calls. |
| `EINVAL` when the mask "contains no processors that are currently physically on the system and permitted to the thread according to any restrictions" | An empty or fully-offline mask is rejected before the syscall. |
| cpuset restrictions "are silently imposed by the kernel" | The plan is built from `sched_getaffinity`, not from `os.cpu_count()`; because that mask also narrows once a process is pinned, a CPU outside it is warned about rather than refused, and the mask is re-read after every write. |
| `EPERM` unless the caller's UID matches or it holds `CAP_SYS_NICE` | Classified explicitly in the failure message. |
| A child from `fork(2)` inherits the mask; it survives `execve(2)` | Pinning a supervisor pins everything it spawns — pin the worker, not the launcher. |

## Windows constraint

Source: [`SetProcessAffinityMask`, Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setprocessaffinitymask).

- "On a system with more than 64 processors, the affinity mask must specify processors in a single [processor group]."
- "If the process affinity mask requests a processor that is not configured in the system, the last error code is **ERROR_INVALID_PARAMETER**."
- "Starting with Windows 11 and Windows Server 2022, on a system with more than 64 processors, process and thread affinities span all processors in the system, across all processor groups, by default."

This is why the read-back comparison is mandatory rather than advisory.

## Pinning is not isolation

| Mechanism | Scope | Note |
|---|---|---|
| `sched_setaffinity` / `cpu_affinity` | Where a task *may* run | Does not prevent other tasks, kthreads, ticks or IRQs on that CPU. Set at runtime by this module. |
| `isolcpus=` boot parameter | Excludes CPUs from scheduler load balancing | Boot-time only. Per the kernel cpusets doc, CPUs in `cpuset.isolcpus` "will never be load balanced regardless of the value of `cpuset.sched_load_balance` in any cpuset". |
| `cpuset.sched_load_balance` (cgroup) | Same effect, adjustable at runtime | The kernel cpusets documentation directs larger/real-time setups to disable load balancing through the cpuset hierarchy rather than relying only on a static boot parameter. |
| `nohz_full=` | Removes the scheduler-clock tick from adaptive-ticks CPUs | Recommended by the kernel per-CPU kthreads document for de-jittering a CPU. |
| `rcu_nocbs=` | Offloads RCU callbacks off the de-jittered CPU | Same source; prevents `rcuc` kthreads waking on that CPU. |
| IRQ affinity (`/proc/irq/*/smp_affinity`) | Moves device interrupts off the CPU | Out of scope for this module. |

**None of the boot/cgroup mechanisms are set or verified by `affinity_manager.py`.** A
deployment record must state which of them are configured; the module can only prove
the affinity mask and the NUMA residency.

## Latency claims

This skill deliberately publishes **no** numeric latency figure for migration jitter or
cross-socket access. The magnitudes depend on the CPU generation, interconnect, memory
configuration and workload, and any single number quoted as universal would be wrong on
most hardware. Measure your own host (see `tick-to-trade-latency-measurement`) and record
the before/after distribution — the tail percentiles, not the mean — in the deployment record.

## Sources

- Linux sysfs ABI: [`sysfs-devices-system-cpu`](https://www.kernel.org/doc/Documentation/ABI/stable/sysfs-devices-system-cpu), [`sysfs-devices-node`](https://www.kernel.org/doc/Documentation/ABI/stable/sysfs-devices-node)
- [`numa(7)`](https://man7.org/linux/man-pages/man7/numa.7.html) — `/proc/<pid>/numa_maps` field definitions
- [`sched_setaffinity(2)`](https://man7.org/linux/man-pages/man2/sched_setaffinity.2.html)
- [Kernel CPUSETS documentation](https://docs.kernel.org/admin-guide/cgroup-v1/cpusets.html)
- [Reducing OS jitter due to per-cpu kthreads](https://docs.kernel.org/admin-guide/kernel-per-CPU-kthreads.html)
- [psutil API reference](https://psutil.io/api/) — `cpu_affinity()` availability, `cpu_count(logical=False)` returning `None`
- [Microsoft Learn — `SetProcessAffinityMask`](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setprocessaffinitymask)

## Category

`real-time-architecture` — see top-level `mappings/` directory.
