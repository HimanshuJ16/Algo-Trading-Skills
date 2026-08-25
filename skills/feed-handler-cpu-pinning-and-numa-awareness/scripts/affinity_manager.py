"""
feed-handler-cpu-pinning-and-numa-awareness: CPU topology discovery, process affinity
binder with mandatory read-back verification, and NUMA memory-locality auditor.

What this module reads, and from where
--------------------------------------
Topology is read from the Linux sysfs ABI rather than inferred from core counts:

    /sys/devices/system/cpu/online                              -> online CPU ids
    /sys/devices/system/cpu/cpuN/topology/thread_siblings_list   -> SMT siblings
        (modern alias: core_cpus_list)
    /sys/devices/system/cpu/cpuN/topology/physical_package_id     -> socket id
    /sys/devices/system/cpu/cpuN/topology/core_id                 -> physical core id
    /sys/devices/system/node/nodeN/cpulist                        -> NUMA node -> CPUs

NUMA residency is read from ``/proc/<pid>/numa_maps``, the per-node resident page
counts the kernel publishes, rather than being guessed from the pinned core id.

Design rule: never report an enforcement that did not happen
------------------------------------------------------------
Every operation that cannot be performed on the running host returns a report with
``is_success=False`` and a message naming the reason. There is deliberately **no
simulated binding path**: a feed handler that believes it is pinned but is not is
worse than one that knows it is not, because the jitter the pinning was deployed to
remove then gets attributed to the network, the vendor, or the exchange. For the same
reason every successful bind re-reads the affinity mask from the OS and compares it
against the request before reporting success -- the kernel and Windows both narrow a
requested mask silently under some configurations (see below).

Verified platform facts
-----------------------
* ``psutil.Process.cpu_affinity()`` availability is documented as "Linux, Windows,
  FreeBSD" (psutil API reference). It does not exist on macOS, where accessing it
  raises ``AttributeError``. ``psutil.cpu_count(logical=False)`` may return ``None``
  when the physical core count cannot be determined.
* ``sched_setaffinity(2)``: the affinity mask is a *per-thread* attribute; ``EINVAL``
  is returned when the mask "contains no processors that are currently physically on
  the system and permitted to the thread according to any restrictions"; cpuset/cgroup
  restrictions are "silently imposed by the kernel"; a child created via ``fork(2)``
  inherits the mask and it is preserved across ``execve(2)``; setting another
  process's affinity requires a matching UID or ``CAP_SYS_NICE`` (``EPERM``).
* Windows ``SetProcessAffinityMask``: "On a system with more than 64 processors, the
  affinity mask must specify processors in a single processor group", and a request
  for a processor not configured in the system fails with ``ERROR_INVALID_PARAMETER``.

Limitations (documented, deliberate)
------------------------------------
* **Full topology discovery is Linux-only.** SMT sibling pairs, physical core counts
  and the CPU->NUMA node map come from sysfs. On Windows/FreeBSD the module can still
  bind and verify an affinity mask, but ``numa_topology_available`` is ``False`` and
  cross-node checks report "could not verify" rather than a verdict.
* **Pinning is not isolation.** ``sched_setaffinity`` constrains *where* a task may
  run; it does not stop other runnable tasks, per-CPU kthreads, timer ticks or
  interrupts from preempting it on that CPU. Excluding other work requires boot- or
  cgroup-level configuration (``isolcpus``, ``cpuset.sched_load_balance``,
  ``nohz_full``, ``rcu_nocbs``, IRQ affinity), which this module cannot set and does
  not claim to verify.
* **Process-level, not thread-level.** ``bind_process_affinity`` moves every thread of
  the target process. Linux affinity is per-thread; pinning individual feed-handler
  threads to separate cores requires per-thread calls this module does not make.
* **numa_maps is a point-in-time snapshot** of currently mapped pages, and includes
  file-backed and shared mappings. A region under an ``interleave`` policy will
  legitimately show pages on remote nodes. Read ``pages_per_node`` before treating a
  non-zero remote count as a defect.
* **This module does not migrate memory.** It reports remote residency; moving pages
  (``migratepages``, ``numactl --membind`` at launch, or first-touch allocation on the
  pinned core) is an operational decision left to the caller.
"""
from __future__ import annotations

import errno
import glob
import logging
import os
import platform
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: Root of the sysfs mount used for topology discovery (overridable for testing).
DEFAULT_SYSFS_ROOT = "/sys"

#: Root of the proc filesystem used for NUMA residency (overridable for testing).
DEFAULT_PROC_ROOT = "/proc"

#: sysfs file exposing SMT siblings; ``core_cpus_list`` is the modern alias.
_SIBLING_FILES = ("thread_siblings_list", "core_cpus_list")

#: ``N<node>=<nr_pages>`` token in /proc/<pid>/numa_maps (numa(7)).
_NUMA_MAPS_NODE_TOKEN = re.compile(r"^N(\d+)=(\d+)$")

try:  # psutil is optional: it is not a repo dependency and is absent on many hosts.
    import psutil

    HAS_PSUTIL = True
except ImportError:  # pragma: no cover - exercised by environments without psutil
    psutil = None  # type: ignore[assignment]
    HAS_PSUTIL = False

#: Exceptions an affinity backend can raise. psutil.Error does not derive from OSError,
#: so it has to be named explicitly rather than swallowed by a bare ``except``.
_BACKEND_EXCEPTIONS: Tuple[type, ...] = (OSError, ValueError, AttributeError, LookupError)
if HAS_PSUTIL:
    _BACKEND_EXCEPTIONS = _BACKEND_EXCEPTIONS + (psutil.Error,)


def parse_cpu_list(raw: str) -> List[int]:
    """
    Parse a kernel CPU-list string into sorted, de-duplicated CPU ids.

    The sysfs ABI documents the format as "human-readable list of CPUs ... like
    0-3, 8-11, 14,17". An empty or whitespace-only string means "no CPUs" and yields
    an empty list; anything else that does not match the format raises ``ValueError``
    rather than being silently skipped, because a mis-parsed topology produces a
    confidently wrong pinning plan.
    """
    text = raw.strip()
    if not text:
        return []

    cpus: Set[int] = set()
    for chunk in text.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "-" in item:
            start_text, _, end_text = item.partition("-")
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"malformed CPU range {item!r} in {raw!r}") from exc
            if start > end or start < 0:
                raise ValueError(f"invalid CPU range {item!r} in {raw!r}")
            cpus.update(range(start, end + 1))
        else:
            try:
                cpu = int(item)
            except ValueError as exc:
                raise ValueError(f"malformed CPU id {item!r} in {raw!r}") from exc
            if cpu < 0:
                raise ValueError(f"negative CPU id {item!r} in {raw!r}")
            cpus.add(cpu)
    return sorted(cpus)


@dataclass
class AffinityBackend:
    """
    The OS mechanism actually used to read and write a process affinity mask.

    ``name`` is reported verbatim in every binding report so an operator can tell
    which syscall path produced (or failed to produce) the pinning.
    """

    name: str
    get_affinity: Callable[[int], List[int]]
    set_affinity: Callable[[int, Sequence[int]], None]


def detect_affinity_backend() -> Optional[AffinityBackend]:
    """
    Resolve the affinity backend available on this host, or ``None`` if there is none.

    ``os.sched_setaffinity`` (Linux, standard library) is preferred over psutil so the
    common case needs no third-party dependency. psutil covers Windows and FreeBSD.
    macOS has no process affinity API and correctly yields ``None`` here.
    """
    if hasattr(os, "sched_setaffinity") and hasattr(os, "sched_getaffinity"):
        return AffinityBackend(
            name="os.sched_setaffinity",
            get_affinity=lambda pid: sorted(os.sched_getaffinity(pid)),
            set_affinity=lambda pid, cores: os.sched_setaffinity(pid, set(cores)),
        )
    if HAS_PSUTIL and hasattr(psutil.Process, "cpu_affinity"):
        return AffinityBackend(
            name="psutil.Process.cpu_affinity",
            get_affinity=lambda pid: sorted(psutil.Process(pid).cpu_affinity()),
            set_affinity=lambda pid, cores: psutil.Process(pid).cpu_affinity(list(cores)),
        )
    return None


@dataclass
class CPUTopologyInfo:
    """
    Observed CPU topology.

    ``physical_core_count`` is ``None`` -- not a guess -- when the host does not expose
    enough information to derive it. ``numa_nodes`` is ``0`` with
    ``numa_topology_available=False`` when no NUMA topology could be read, which is the
    case on a ``CONFIG_NUMA=n`` kernel and on non-Linux hosts.

    ``available_cpu_ids`` is the process's *current* affinity mask, not a permission
    ceiling: it already reflects any cpuset the kernel imposes on a fresh process, but
    it also narrows once the process has been pinned, so a CPU outside it is not
    necessarily forbidden.
    """

    logical_core_count: int
    physical_core_count: Optional[int]
    numa_nodes: int
    available_cpu_ids: List[int]
    online_cpu_ids: List[int] = field(default_factory=list)
    numa_node_to_cpus: Dict[int, List[int]] = field(default_factory=dict)
    cpu_to_numa_node: Dict[int, int] = field(default_factory=dict)
    thread_siblings: Dict[int, List[int]] = field(default_factory=dict)
    numa_topology_available: bool = False
    topology_source: str = "unavailable"
    affinity_backend: str = "none"


@dataclass
class CoreSelectionAudit:
    """Result of validating a requested CPU set against the observed topology."""

    requested_cores: List[int]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    numa_node_ids: List[int] = field(default_factory=list)
    sibling_collisions: List[Tuple[int, int]] = field(default_factory=list)
    spans_numa_nodes: bool = False

    @property
    def is_valid(self) -> bool:
        return not self.errors


@dataclass
class AffinityBindingReport:
    """
    Outcome of a binding attempt.

    ``numa_node_id`` is the NUMA node shared by every assigned core, or ``-1`` when the
    selection spans multiple nodes or the NUMA topology could not be read. It is never
    inferred from the core index.
    """

    pid: int
    assigned_cores: List[int]
    previous_cores: List[int]
    numa_node_id: int
    is_success: bool
    message: str
    numa_node_ids: List[int] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    backend: str = "none"


@dataclass
class NUMALocalityReport:
    """
    Per-node resident page counts for a process, from ``/proc/<pid>/numa_maps``.

    ``is_available`` is ``False`` when the file does not exist or cannot be read, in
    which case the page counts are empty and ``is_local`` is ``False`` -- "unknown" is
    never reported as "local".
    """

    pid: int
    is_available: bool
    pages_per_node: Dict[int, int]
    local_node_ids: List[int]
    local_pages: int
    remote_pages: int
    remote_page_fraction: float
    is_local: bool
    message: str


class CPUAffinityNUMAManager:
    """
    Discovers CPU/NUMA topology, binds latency-critical processes to specific CPUs, and
    verifies both the resulting affinity mask and NUMA memory residency.

    Filesystem roots and the affinity backend are injectable so the logic can be tested
    against a known topology without requiring a NUMA host or elevated privileges.
    """

    def __init__(
        self,
        sysfs_root: str = DEFAULT_SYSFS_ROOT,
        proc_root: str = DEFAULT_PROC_ROOT,
        affinity_backend: Optional[AffinityBackend] = None,
        autodetect_backend: bool = True,
    ) -> None:
        self.sysfs_root = sysfs_root
        self.proc_root = proc_root
        if affinity_backend is not None:
            self.backend: Optional[AffinityBackend] = affinity_backend
        elif autodetect_backend:
            self.backend = detect_affinity_backend()
        else:
            self.backend = None

    # ------------------------------------------------------------------ topology

    def discover_topology(self) -> CPUTopologyInfo:
        """
        Read the live CPU and NUMA topology.

        Nothing here is inferred from core counts: SMT siblings, physical cores and the
        CPU->NUMA map are read from sysfs, and the set of CPUs this process may
        actually run on is read from the affinity backend (which reflects any cpuset
        restriction the kernel imposes silently).
        """
        online = self._read_online_cpus()
        siblings, physical_cores, source = self._read_cpu_topology(online)
        node_to_cpus = self._read_numa_nodes()

        cpu_to_node: Dict[int, int] = {}
        for node_id, node_cpus in node_to_cpus.items():
            for cpu in node_cpus:
                cpu_to_node[cpu] = node_id

        available = self._read_current_affinity()
        if available is None:
            available = list(online)

        if physical_cores is None and HAS_PSUTIL:
            # Documented to return None when undetermined; keep None rather than guess.
            physical_cores = psutil.cpu_count(logical=False)

        topology = CPUTopologyInfo(
            logical_core_count=len(online),
            physical_core_count=physical_cores,
            numa_nodes=len(node_to_cpus),
            available_cpu_ids=sorted(available),
            online_cpu_ids=sorted(online),
            numa_node_to_cpus=node_to_cpus,
            cpu_to_numa_node=cpu_to_node,
            thread_siblings=siblings,
            numa_topology_available=bool(node_to_cpus),
            topology_source=source,
            affinity_backend=self.backend.name if self.backend else "none",
        )
        logger.debug(
            "Topology: %d online CPUs, physical=%s, NUMA nodes=%d, source=%s, backend=%s",
            topology.logical_core_count,
            topology.physical_core_count,
            topology.numa_nodes,
            topology.topology_source,
            topology.affinity_backend,
        )
        return topology

    def hyperthread_siblings(self, cpu_id: int) -> List[int]:
        """
        Return every CPU sharing a physical core with ``cpu_id``, including itself.

        Returns ``[cpu_id]`` when SMT topology is unreadable -- an unknown sibling set
        must not be reported as "no siblings", so callers should check
        ``topology_source`` before treating that as proof the core is unshared.
        """
        topology = self.discover_topology()
        return topology.thread_siblings.get(cpu_id, [cpu_id])

    def sibling_cpus_to_reserve(self, target_cores: Sequence[int]) -> List[int]:
        """
        CPUs that share a physical core with the selection but are not part of it.

        These are the CPUs an operator must keep free of other work: scheduling an
        unrelated process on one of them puts it in contention with the feed handler
        for the same physical core's execution units, which is the exact failure mode
        SMT-aware pinning exists to prevent.
        """
        topology = self.discover_topology()
        selected = set(target_cores)
        reserve: Set[int] = set()
        for cpu in selected:
            reserve.update(topology.thread_siblings.get(cpu, [cpu]))
        return sorted(reserve - selected)

    # ---------------------------------------------------------------- validation

    def validate_core_selection(self, target_cores: Sequence[int]) -> CoreSelectionAudit:
        """
        Check a requested CPU set against the live topology before touching the OS.

        Errors block the bind; warnings do not. Duplicates are an error rather than
        being silently de-duplicated: in a hand-written pinning plan ``[2, 2]`` almost
        always means ``[2, 3]``, and quietly collapsing it leaves a worker unpinned.
        """
        audit = CoreSelectionAudit(requested_cores=[])

        if isinstance(target_cores, (str, bytes)):
            audit.errors.append("target_cores must be an iterable of CPU ids, not a string")
            return audit
        try:
            cores = list(target_cores)
        except TypeError:
            audit.errors.append("target_cores must be an iterable of CPU ids")
            return audit

        audit.requested_cores = cores
        if not cores:
            audit.errors.append(
                "empty CPU selection: sched_setaffinity(2) returns EINVAL for a mask "
                "containing no permitted CPU"
            )
            return audit

        for cpu in cores:
            if isinstance(cpu, bool) or not isinstance(cpu, int):
                audit.errors.append(f"CPU id {cpu!r} is not an integer")
            elif cpu < 0:
                audit.errors.append(f"CPU id {cpu} is negative")
        if audit.errors:
            return audit

        duplicates = sorted({c for c in cores if cores.count(c) > 1})
        if duplicates:
            audit.errors.append(f"duplicate CPU ids in selection: {duplicates}")

        topology = self.discover_topology()
        offline = sorted(set(cores) - set(topology.online_cpu_ids))
        if offline:
            audit.errors.append(f"CPU ids not online on this host: {offline}")

        outside_current = sorted(set(cores) - set(topology.available_cpu_ids) - set(offline))
        if outside_current:
            # A warning, not an error: available_cpu_ids is the process's *current* mask,
            # which is already narrowed once it has been pinned, so re-pinning or widening
            # a pinned process must stay possible. Where a cpuset genuinely forbids the
            # CPU the kernel returns EINVAL, which bind_process_affinity classifies.
            audit.warnings.append(
                f"CPU ids {outside_current} are outside this process's current affinity "
                "mask; if a cpuset/cgroup forbids them the kernel will reject the bind "
                "with EINVAL (such restrictions are imposed silently)"
            )

        selected = set(cores)
        for cpu in sorted(selected):
            for sibling in topology.thread_siblings.get(cpu, []):
                if sibling != cpu and sibling in selected and cpu < sibling:
                    audit.sibling_collisions.append((cpu, sibling))
        if audit.sibling_collisions:
            audit.warnings.append(
                f"selection contains SMT sibling pairs {audit.sibling_collisions}: the "
                "process owns both threads of those physical cores. Intentional when "
                "dedicating a whole core; a defect if another process is expected to "
                "run on the sibling."
            )

        if topology.numa_topology_available:
            nodes = {topology.cpu_to_numa_node[c] for c in selected if c in topology.cpu_to_numa_node}
            unmapped = sorted(c for c in selected if c not in topology.cpu_to_numa_node)
            if unmapped:
                audit.warnings.append(f"no NUMA node mapping found for CPUs {unmapped}")
            audit.numa_node_ids = sorted(nodes)
            audit.spans_numa_nodes = len(nodes) > 1
        else:
            audit.warnings.append(
                "NUMA topology unavailable on this host: cross-node locality could not "
                "be verified"
            )

        return audit

    # ------------------------------------------------------------------- binding

    def bind_process_affinity(
        self,
        target_cores: Sequence[int],
        pid: Optional[int] = None,
        allow_cross_numa: bool = False,
    ) -> AffinityBindingReport:
        """
        Bind ``pid`` (default: the current process) to ``target_cores`` and verify it.

        The mask is read back from the OS after the write and compared against the
        request; a narrowed mask -- which the kernel produces under a cpuset, and
        Windows produces when a request crosses a processor group -- is reported as a
        failure, not as a success. A selection spanning NUMA nodes is rejected unless
        ``allow_cross_numa`` is set, since binding across sockets reintroduces the
        cross-interconnect memory access this skill exists to eliminate.
        """
        target_pid = os.getpid() if pid is None else pid
        prev_cores: List[int] = []
        backend_name = self.backend.name if self.backend else "none"

        audit = self.validate_core_selection(target_cores)
        if not audit.is_valid:
            return self._failure(
                target_pid,
                prev_cores,
                f"Refusing to bind PID {target_pid}: " + "; ".join(audit.errors),
                audit,
                backend_name,
            )

        if audit.spans_numa_nodes and not allow_cross_numa:
            return self._failure(
                target_pid,
                prev_cores,
                f"Refusing to bind PID {target_pid}: selection spans NUMA nodes "
                f"{audit.numa_node_ids}; pass allow_cross_numa=True to accept the "
                "cross-socket interconnect penalty deliberately",
                audit,
                backend_name,
            )

        if self.backend is None:
            return self._failure(
                target_pid,
                prev_cores,
                f"Cannot bind PID {target_pid}: no CPU affinity backend available on "
                f"{platform.system()} (os.sched_setaffinity is Linux-only; "
                "psutil.Process.cpu_affinity supports Linux, Windows and FreeBSD). "
                "The process is NOT pinned.",
                audit,
                backend_name,
            )

        for message in audit.warnings:
            logger.warning("PID %d affinity audit: %s", target_pid, message)

        try:
            prev_cores = list(self.backend.get_affinity(target_pid))
            self.backend.set_affinity(target_pid, list(target_cores))
            actual_cores = list(self.backend.get_affinity(target_pid))
        except OSError as exc:
            return self._failure(
                target_pid, prev_cores, self._describe_os_error(target_pid, exc), audit, backend_name
            )
        except _BACKEND_EXCEPTIONS as exc:
            return self._failure(
                target_pid,
                prev_cores,
                f"Failed to bind CPU affinity for PID {target_pid} via {backend_name}: "
                f"{type(exc).__name__}: {exc}",
                audit,
                backend_name,
            )

        if set(actual_cores) != set(target_cores):
            return self._failure(
                target_pid,
                prev_cores,
                f"Affinity read-back mismatch for PID {target_pid}: requested "
                f"{sorted(set(target_cores))} but the OS reports {sorted(set(actual_cores))}. "
                "The process is NOT pinned as requested (a cpuset restriction or a "
                "Windows processor-group boundary can narrow the mask).",
                audit,
                backend_name,
            )

        numa_node = audit.numa_node_ids[0] if len(audit.numa_node_ids) == 1 else -1
        msg = (
            f"PID {target_pid} pinned to CPUs {sorted(actual_cores)} via {backend_name} "
            f"(NUMA node {numa_node if numa_node >= 0 else 'unknown'}; read-back verified)."
        )
        logger.info(msg)
        return AffinityBindingReport(
            pid=target_pid,
            assigned_cores=sorted(actual_cores),
            previous_cores=sorted(prev_cores),
            numa_node_id=numa_node,
            is_success=True,
            message=msg,
            numa_node_ids=list(audit.numa_node_ids),
            warnings=list(audit.warnings),
            backend=backend_name,
        )

    # ------------------------------------------------------------- NUMA locality

    def audit_numa_locality(self, pid: Optional[int] = None) -> NUMALocalityReport:
        """
        Compare a process's resident pages per NUMA node against the nodes local to its
        current CPU affinity.

        Pages on any other node were allocated remotely and are reached over the
        socket interconnect on every access. ``pages_per_node`` is reported raw so an
        interleaved region -- which is remote by design -- can be distinguished from an
        accidental cross-socket allocation.
        """
        target_pid = os.getpid() if pid is None else pid
        pages_per_node = self._read_numa_maps(target_pid)
        if not pages_per_node:
            msg = (
                f"NUMA residency unavailable for PID {target_pid}: "
                f"{os.path.join(self.proc_root, str(target_pid), 'numa_maps')} is not "
                "readable (non-Linux host, CONFIG_NUMA=n, or insufficient permission)."
            )
            logger.warning(msg)
            return NUMALocalityReport(
                pid=target_pid,
                is_available=False,
                pages_per_node={},
                local_node_ids=[],
                local_pages=0,
                remote_pages=0,
                remote_page_fraction=0.0,
                is_local=False,
                message=msg,
            )

        topology = self.discover_topology()
        affinity = self._read_current_affinity(target_pid)
        cpus = affinity if affinity is not None else topology.online_cpu_ids
        local_nodes = sorted(
            {topology.cpu_to_numa_node[c] for c in cpus if c in topology.cpu_to_numa_node}
        )

        local_pages = sum(p for n, p in pages_per_node.items() if n in local_nodes)
        total_pages = sum(pages_per_node.values())
        remote_pages = total_pages - local_pages
        fraction = (remote_pages / total_pages) if total_pages else 0.0

        if not local_nodes:
            msg = (
                f"PID {target_pid}: {total_pages} resident pages found but the CPU->NUMA "
                "map is unavailable, so locality cannot be judged."
            )
            logger.warning(msg)
            return NUMALocalityReport(
                pid=target_pid,
                is_available=True,
                pages_per_node=pages_per_node,
                local_node_ids=[],
                local_pages=0,
                remote_pages=0,
                remote_page_fraction=0.0,
                is_local=False,
                message=msg,
            )

        is_local = remote_pages == 0
        msg = (
            f"PID {target_pid}: {local_pages} local / {remote_pages} remote pages "
            f"({fraction:.2%} remote) against local NUMA node(s) {local_nodes}."
        )
        (logger.info if is_local else logger.warning)(msg)
        return NUMALocalityReport(
            pid=target_pid,
            is_available=True,
            pages_per_node=pages_per_node,
            local_node_ids=local_nodes,
            local_pages=local_pages,
            remote_pages=remote_pages,
            remote_page_fraction=fraction,
            is_local=is_local,
            message=msg,
        )

    # -------------------------------------------------------------- internal I/O

    def _failure(
        self,
        pid: int,
        prev_cores: List[int],
        message: str,
        audit: Optional[CoreSelectionAudit],
        backend_name: str,
    ) -> AffinityBindingReport:
        logger.error(message)
        return AffinityBindingReport(
            pid=pid,
            assigned_cores=[],
            previous_cores=sorted(prev_cores),
            numa_node_id=-1,
            is_success=False,
            message=message,
            numa_node_ids=list(audit.numa_node_ids) if audit else [],
            warnings=list(audit.warnings) if audit else [],
            backend=backend_name,
        )

    @staticmethod
    def _describe_os_error(pid: int, exc: OSError) -> str:
        if exc.errno == errno.EPERM:
            detail = (
                "EPERM: setting another process's affinity requires a matching UID or "
                "the CAP_SYS_NICE capability"
            )
        elif exc.errno == errno.EINVAL:
            detail = (
                "EINVAL: the mask contains no CPU that is both online and permitted to "
                "the thread (check cpuset/cgroup restrictions)"
            )
        elif exc.errno == errno.ESRCH:
            detail = "ESRCH: no such process"
        else:
            detail = str(exc)
        return f"Failed to bind CPU affinity for PID {pid}: {detail}"

    def _read_text(self, path: str) -> Optional[str]:
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except OSError:
            return None

    def _read_current_affinity(self, pid: Optional[int] = None) -> Optional[List[int]]:
        if self.backend is None:
            return None
        try:
            return sorted(self.backend.get_affinity(os.getpid() if pid is None else pid))
        except _BACKEND_EXCEPTIONS as exc:
            logger.debug("Affinity query failed: %s", exc)
            return None

    def _read_online_cpus(self) -> List[int]:
        raw = self._read_text(os.path.join(self.sysfs_root, "devices", "system", "cpu", "online"))
        if raw is not None:
            try:
                cpus = parse_cpu_list(raw)
                if cpus:
                    return cpus
            except ValueError as exc:
                logger.warning("Unparseable cpu/online (%s); falling back to cpu*/ dirs", exc)

        cpu_dirs = glob.glob(os.path.join(self.sysfs_root, "devices", "system", "cpu", "cpu[0-9]*"))
        cpus = sorted(
            int(match.group(1))
            for match in (re.search(r"cpu(\d+)$", d) for d in cpu_dirs)
            if match
        )
        if cpus:
            return cpus
        return list(range(os.cpu_count() or 1))

    def _read_cpu_topology(
        self, online: Sequence[int]
    ) -> Tuple[Dict[int, List[int]], Optional[int], str]:
        siblings: Dict[int, List[int]] = {}
        physical_ids: Set[Tuple[int, int]] = set()
        sibling_groups: Set[Tuple[int, ...]] = set()
        package_reads = 0

        for cpu in online:
            topo_dir = os.path.join(
                self.sysfs_root, "devices", "system", "cpu", f"cpu{cpu}", "topology"
            )
            for filename in _SIBLING_FILES:
                raw = self._read_text(os.path.join(topo_dir, filename))
                if raw is None:
                    continue
                try:
                    group = parse_cpu_list(raw)
                except ValueError as exc:
                    # Fall through to the alias file rather than giving up on this CPU.
                    logger.warning("Unparseable %s for cpu%d: %s", filename, cpu, exc)
                    continue
                if group:
                    siblings[cpu] = group
                    sibling_groups.add(tuple(group))
                    break

            package_raw = self._read_text(os.path.join(topo_dir, "physical_package_id"))
            core_raw = self._read_text(os.path.join(topo_dir, "core_id"))
            if package_raw is not None and core_raw is not None:
                try:
                    physical_ids.add((int(package_raw.strip()), int(core_raw.strip())))
                    package_reads += 1
                except ValueError:
                    logger.warning("Non-integer package/core id for cpu%d", cpu)

        if package_reads == len(online) and physical_ids:
            physical_core_count: Optional[int] = len(physical_ids)
        elif sibling_groups and len(siblings) == len(online):
            physical_core_count = len(sibling_groups)
        else:
            physical_core_count = None

        source = "sysfs" if siblings or physical_ids else "unavailable"
        return siblings, physical_core_count, source

    def _read_numa_nodes(self) -> Dict[int, List[int]]:
        node_dirs = glob.glob(os.path.join(self.sysfs_root, "devices", "system", "node", "node[0-9]*"))
        nodes: Dict[int, List[int]] = {}
        for node_dir in node_dirs:
            match = re.search(r"node(\d+)$", node_dir)
            if not match:
                continue
            raw = self._read_text(os.path.join(node_dir, "cpulist"))
            if raw is None:
                continue
            try:
                nodes[int(match.group(1))] = parse_cpu_list(raw)
            except ValueError as exc:
                logger.warning("Unparseable cpulist for %s: %s", node_dir, exc)
        return dict(sorted(nodes.items()))

    def _read_numa_maps(self, pid: int) -> Dict[int, int]:
        raw = self._read_text(os.path.join(self.proc_root, str(pid), "numa_maps"))
        if raw is None:
            return {}
        pages: Dict[int, int] = {}
        for line in raw.splitlines():
            for token in line.split():
                match = _NUMA_MAPS_NODE_TOKEN.match(token)
                if match:
                    node_id = int(match.group(1))
                    pages[node_id] = pages.get(node_id, 0) + int(match.group(2))
        return dict(sorted(pages.items()))
