"""
Unit tests for feed-handler-cpu-pinning-and-numa-awareness.

The tests build a synthetic sysfs/proc tree describing a known machine and point the
manager at it, so the real parsing, validation and NUMA logic is exercised on any host
without requiring a dual-socket NUMA box, root, or psutil. The affinity backend is a
recording fake that models the documented OS contract (unknown CPU -> OSError, empty
mask -> EINVAL, mask narrowing under a cpuset).

Reference machine used throughout (``_write_reference_sysfs``):

    8 logical CPUs, SMT enabled, 4 physical cores, 2 NUMA nodes
    package 0 -> CPUs 0-3, package 1 -> CPUs 4-7
    SMT pairs: (0,1) (2,3) (4,5) (6,7)
    NUMA node 0 -> CPUs 0-3, NUMA node 1 -> CPUs 4-7

CPU 4 is the key case: it lives on NUMA node 1 while its index is below 8, so any
implementation that infers the node from the core index reports node 0 for it.
"""
import errno
import logging
import os
import shutil
import tempfile
import unittest

from affinity_manager import (
    AffinityBackend,
    CPUAffinityNUMAManager,
    detect_affinity_backend,
    parse_cpu_list,
)

# Failure paths are asserted through the returned reports, not the log stream.
logging.getLogger("affinity_manager").setLevel(logging.CRITICAL)


class RecordingBackend:
    """Fake affinity backend modelling the documented OS behaviour."""

    def __init__(self, initial, present=range(8), narrow_to=None):
        self.affinity = {0: sorted(initial)}
        self.present = set(present)
        self.narrow_to = narrow_to
        self.set_calls = []

    def as_backend(self, name="fake"):
        return AffinityBackend(
            name=name, get_affinity=self.get_affinity, set_affinity=self.set_affinity
        )

    def get_affinity(self, pid):
        return sorted(self.affinity.get(pid, self.affinity[0]))

    def set_affinity(self, pid, cores):
        self.set_calls.append((pid, sorted(cores)))
        cores = list(cores)
        if not cores:
            raise OSError(errno.EINVAL, "empty affinity mask")
        unknown = sorted(set(cores) - self.present)
        if unknown:
            raise OSError(errno.EINVAL, f"CPUs not present: {unknown}")
        # Models a cpuset / Windows processor-group boundary silently narrowing the mask.
        applied = sorted(set(cores) & set(self.narrow_to)) if self.narrow_to is not None else sorted(cores)
        self.affinity[pid] = applied
        self.affinity[0] = applied


class AffinityTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="affinity_test_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.sysfs = os.path.join(self.root, "sys")
        self.proc = os.path.join(self.root, "proc")
        self._write_reference_sysfs()
        self.fake = RecordingBackend(initial=range(8))

    def _write(self, path, content):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def _write_reference_sysfs(self):
        cpu_root = os.path.join(self.sysfs, "devices", "system", "cpu")
        self._write(os.path.join(cpu_root, "online"), "0-7\n")
        for cpu in range(8):
            core = cpu // 2
            package = 0 if cpu < 4 else 1
            sibling_low = (cpu // 2) * 2
            topo = os.path.join(cpu_root, f"cpu{cpu}", "topology")
            self._write(
                os.path.join(topo, "thread_siblings_list"), f"{sibling_low}-{sibling_low + 1}\n"
            )
            self._write(os.path.join(topo, "physical_package_id"), f"{package}\n")
            self._write(os.path.join(topo, "core_id"), f"{core}\n")

        node_root = os.path.join(self.sysfs, "devices", "system", "node")
        self._write(os.path.join(node_root, "node0", "cpulist"), "0-3\n")
        self._write(os.path.join(node_root, "node1", "cpulist"), "4-7\n")

    def make_manager(self, backend="fake", **kwargs):
        if backend == "fake":
            backend = self.fake.as_backend()
        return CPUAffinityNUMAManager(
            sysfs_root=self.sysfs,
            proc_root=self.proc,
            affinity_backend=backend,
            autodetect_backend=False,
            **kwargs,
        )


class TestParseCpuList(unittest.TestCase):
    def test_parses_kernel_abi_examples(self):
        # Format documented in the sysfs ABI: "0-3, 8-11, 14,17".
        self.assertEqual(parse_cpu_list("0-3, 8-11, 14,17"), [0, 1, 2, 3, 8, 9, 10, 11, 14, 17])

    def test_single_cpu_and_empty(self):
        self.assertEqual(parse_cpu_list("5\n"), [5])
        self.assertEqual(parse_cpu_list("   \n"), [])

    def test_deduplicates_and_sorts(self):
        self.assertEqual(parse_cpu_list("7,1-3,2"), [1, 2, 3, 7])

    def test_malformed_input_raises_rather_than_silently_dropping(self):
        for bad in ("3-", "a-b", "1-", "5-3", "-2", "x"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_cpu_list(bad)


class TestTopologyDiscovery(AffinityTestBase):
    def test_topology_is_read_from_sysfs_not_inferred(self):
        topo = self.make_manager().discover_topology()
        self.assertEqual(topo.logical_core_count, 8)
        self.assertEqual(topo.physical_core_count, 4)  # 4 distinct (package, core) pairs
        self.assertEqual(topo.numa_nodes, 2)
        self.assertTrue(topo.numa_topology_available)
        self.assertEqual(topo.topology_source, "sysfs")
        self.assertEqual(topo.numa_node_to_cpus, {0: [0, 1, 2, 3], 1: [4, 5, 6, 7]})

    def test_cpu_to_numa_node_map_matches_sysfs(self):
        topo = self.make_manager().discover_topology()
        self.assertEqual(topo.cpu_to_numa_node[3], 0)
        self.assertEqual(topo.cpu_to_numa_node[4], 1)

    def test_available_cpus_reflect_backend_restriction(self):
        # Models a cpuset confining the process to node 0.
        restricted = RecordingBackend(initial=[0, 1, 2, 3]).as_backend()
        topo = self.make_manager(backend=restricted).discover_topology()
        self.assertEqual(topo.available_cpu_ids, [0, 1, 2, 3])
        self.assertEqual(topo.online_cpu_ids, [0, 1, 2, 3, 4, 5, 6, 7])

    def test_hyperthread_siblings_and_reserve_list(self):
        mgr = self.make_manager()
        self.assertEqual(mgr.hyperthread_siblings(2), [2, 3])
        self.assertEqual(mgr.hyperthread_siblings(7), [6, 7])
        # Pinning to CPU 2 means CPU 3 must be kept free of other work.
        self.assertEqual(mgr.sibling_cpus_to_reserve([2]), [3])
        self.assertEqual(mgr.sibling_cpus_to_reserve([2, 3]), [])
        self.assertEqual(mgr.sibling_cpus_to_reserve([0, 4]), [1, 5])

    def test_core_cpus_list_alias_is_used_when_legacy_file_is_unusable(self):
        # Newer kernels expose core_cpus_list; thread_siblings_list is the legacy name.
        # An unreadable or corrupt legacy file must fall through to the alias, and the
        # physical core count must still be derivable from the sibling groups alone.
        for legacy in (None, "bogus-\n"):
            with self.subTest(legacy=legacy):
                root = os.path.join(self.root, f"alias-{legacy!r}")
                cpu_root = os.path.join(root, "devices", "system", "cpu")
                self._write(os.path.join(cpu_root, "online"), "0-3")
                for cpu in range(4):
                    low = (cpu // 2) * 2
                    topo = os.path.join(cpu_root, f"cpu{cpu}", "topology")
                    if legacy is not None:
                        self._write(os.path.join(topo, "thread_siblings_list"), legacy)
                    self._write(os.path.join(topo, "core_cpus_list"), f"{low}-{low + 1}")
                mgr = CPUAffinityNUMAManager(
                    sysfs_root=root, proc_root=self.proc, autodetect_backend=False
                )
                topo_info = mgr.discover_topology()
                self.assertEqual(topo_info.thread_siblings[3], [2, 3])
                # No package/core ids present: fall back to counting sibling groups.
                self.assertEqual(topo_info.physical_core_count, 2)

    def test_missing_sysfs_reports_degraded_not_fabricated(self):
        mgr = CPUAffinityNUMAManager(
            sysfs_root=os.path.join(self.root, "does-not-exist"),
            proc_root=self.proc,
            affinity_backend=self.fake.as_backend(),
            autodetect_backend=False,
        )
        topo = mgr.discover_topology()
        self.assertEqual(topo.topology_source, "unavailable")
        self.assertFalse(topo.numa_topology_available)
        self.assertEqual(topo.numa_nodes, 0)
        # No invented "logical // 2" physical core count and no invented NUMA map.
        self.assertIsNone(topo.physical_core_count)
        self.assertEqual(topo.cpu_to_numa_node, {})


class TestCoreSelectionValidation(AffinityTestBase):
    def test_valid_single_core_selection(self):
        audit = self.make_manager().validate_core_selection([2])
        self.assertTrue(audit.is_valid)
        self.assertEqual(audit.numa_node_ids, [0])
        self.assertFalse(audit.spans_numa_nodes)

    def test_empty_selection_is_rejected(self):
        audit = self.make_manager().validate_core_selection([])
        self.assertFalse(audit.is_valid)
        self.assertIn("EINVAL", audit.errors[0])

    def test_negative_non_integer_and_boolean_ids_rejected(self):
        mgr = self.make_manager()
        for bad in ([-1], [1.5], ["2"], [True], [None]):
            with self.subTest(bad=bad):
                self.assertFalse(mgr.validate_core_selection(bad).is_valid)

    def test_duplicate_ids_rejected(self):
        audit = self.make_manager().validate_core_selection([2, 2])
        self.assertFalse(audit.is_valid)
        self.assertIn("duplicate", audit.errors[0])

    def test_offline_cpu_rejected(self):
        audit = self.make_manager().validate_core_selection([99])
        self.assertFalse(audit.is_valid)
        self.assertIn("not online", " ".join(audit.errors))

    def test_cpu_outside_current_mask_warns_and_lets_the_kernel_decide(self):
        # available_cpu_ids is the *current* mask, which narrows once a process is
        # pinned. Treating it as a permission ceiling would make re-pinning or widening
        # an already-pinned process impossible, so this is a warning and the kernel's
        # EINVAL remains the authority.
        restricted = RecordingBackend(initial=[0, 1, 2, 3]).as_backend()
        audit = self.make_manager(backend=restricted).validate_core_selection([5])
        self.assertTrue(audit.is_valid)
        self.assertIn("outside this process's current affinity mask", " ".join(audit.warnings))

    def test_already_pinned_process_can_be_repinned_to_another_core(self):
        mgr = self.make_manager()
        first = mgr.bind_process_affinity([2], pid=31)
        self.assertTrue(first.is_success)
        second = mgr.bind_process_affinity([0], pid=31)
        self.assertTrue(second.is_success)
        self.assertEqual(second.assigned_cores, [0])
        self.assertEqual(second.previous_cores, [2])

    def test_cpu_forbidden_by_the_kernel_is_reported_as_einval(self):
        # The fake backend models a host where CPUs 4-7 are not present/permitted.
        backend = RecordingBackend(initial=[0, 1, 2, 3], present=range(4)).as_backend()
        report = self.make_manager(backend=backend).bind_process_affinity([5], pid=33)
        self.assertFalse(report.is_success)
        self.assertIn("EINVAL", report.message)

    def test_smt_sibling_pair_warns_but_does_not_block(self):
        audit = self.make_manager().validate_core_selection([2, 3])
        self.assertTrue(audit.is_valid)
        self.assertEqual(audit.sibling_collisions, [(2, 3)])
        self.assertIn("SMT sibling", " ".join(audit.warnings))

    def test_non_sibling_pair_produces_no_collision(self):
        audit = self.make_manager().validate_core_selection([0, 2])
        self.assertEqual(audit.sibling_collisions, [])

    def test_cross_node_selection_is_flagged(self):
        audit = self.make_manager().validate_core_selection([3, 4])
        self.assertTrue(audit.spans_numa_nodes)
        self.assertEqual(audit.numa_node_ids, [0, 1])


class TestBinding(AffinityTestBase):
    def test_successful_bind_records_previous_and_assigned(self):
        report = self.make_manager().bind_process_affinity([2], pid=4242)
        self.assertTrue(report.is_success)
        self.assertEqual(report.assigned_cores, [2])
        self.assertEqual(report.previous_cores, [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(report.pid, 4242)
        self.assertEqual(self.fake.set_calls, [(4242, [2])])

    def test_numa_node_comes_from_sysfs_not_from_the_core_index(self):
        # Regression: CPU 4 is on NUMA node 1 despite an index below 8. An
        # index-threshold heuristic reports node 0 here.
        report = self.make_manager().bind_process_affinity([4], pid=1)
        self.assertTrue(report.is_success)
        self.assertEqual(report.numa_node_id, 1)
        self.assertEqual(report.numa_node_ids, [1])

    def test_no_backend_reports_failure_and_never_fakes_success(self):
        # Regression: the process must never be reported as pinned on a host where no
        # affinity API exists (e.g. macOS), which is what a mock fallback would do.
        mgr = CPUAffinityNUMAManager(
            sysfs_root=self.sysfs,
            proc_root=self.proc,
            autodetect_backend=False,
        )
        report = mgr.bind_process_affinity([2], pid=7)
        self.assertFalse(report.is_success)
        self.assertEqual(report.assigned_cores, [])
        self.assertEqual(report.numa_node_id, -1)
        self.assertIn("NOT pinned", report.message)
        self.assertEqual(report.backend, "none")

    def test_readback_mismatch_is_reported_as_failure(self):
        # Models a cpuset (or Windows processor group) narrowing the requested mask.
        narrowing = RecordingBackend(initial=range(8), narrow_to=[0, 1])
        mgr = self.make_manager(backend=narrowing.as_backend("narrowing"))
        report = mgr.bind_process_affinity([0, 1, 2], pid=9)
        self.assertFalse(report.is_success)
        self.assertIn("read-back mismatch", report.message)
        self.assertEqual(report.assigned_cores, [])

    def test_cross_numa_blocked_by_default_and_allowed_explicitly(self):
        mgr = self.make_manager()
        blocked = mgr.bind_process_affinity([3, 4], pid=11)
        self.assertFalse(blocked.is_success)
        self.assertIn("spans NUMA nodes", blocked.message)
        self.assertEqual(self.fake.set_calls, [])  # no OS call attempted

        allowed = mgr.bind_process_affinity([3, 4], pid=11, allow_cross_numa=True)
        self.assertTrue(allowed.is_success)
        self.assertEqual(allowed.numa_node_id, -1)  # spans nodes -> no single node
        self.assertEqual(allowed.numa_node_ids, [0, 1])

    def test_invalid_selection_never_reaches_the_os(self):
        mgr = self.make_manager()
        for bad in ([], [99], [2, 2], [-3]):
            with self.subTest(bad=bad):
                report = mgr.bind_process_affinity(bad, pid=5)
                self.assertFalse(report.is_success)
        self.assertEqual(self.fake.set_calls, [])

    def test_eperm_is_classified_not_raised(self):
        def raising_set(pid, cores):
            raise OSError(errno.EPERM, "operation not permitted")

        backend = AffinityBackend(
            name="raising", get_affinity=lambda pid: [0, 1, 2, 3], set_affinity=raising_set
        )
        report = self.make_manager(backend=backend).bind_process_affinity([2], pid=13)
        self.assertFalse(report.is_success)
        self.assertIn("CAP_SYS_NICE", report.message)
        self.assertEqual(report.previous_cores, [0, 1, 2, 3])

    def test_esrch_is_classified(self):
        def raising_set(pid, cores):
            raise OSError(errno.ESRCH, "no such process")

        backend = AffinityBackend(
            name="raising", get_affinity=lambda pid: [0, 1], set_affinity=raising_set
        )
        report = self.make_manager(backend=backend).bind_process_affinity([0], pid=999999)
        self.assertFalse(report.is_success)
        self.assertIn("ESRCH", report.message)

    def test_sibling_warning_is_carried_into_the_report(self):
        report = self.make_manager().bind_process_affinity([2, 3], pid=17)
        self.assertTrue(report.is_success)
        self.assertIn("SMT sibling", " ".join(report.warnings))


class TestNUMALocalityAudit(AffinityTestBase):
    def _write_numa_maps(self, pid, content):
        self._write(os.path.join(self.proc, str(pid), "numa_maps"), content)

    def test_detects_remote_pages(self):
        self._write_numa_maps(
            501,
            "00400000 default file=/opt/feed/handler mapped=40 N0=40 kernelpagesize_kB=4\n"
            "7f0000000000 default anon=150 dirty=150 N0=60 N1=90 kernelpagesize_kB=4\n",
        )
        mgr = self.make_manager(backend=RecordingBackend(initial=[0, 1, 2, 3]).as_backend())
        report = mgr.audit_numa_locality(pid=501)
        self.assertTrue(report.is_available)
        self.assertEqual(report.pages_per_node, {0: 100, 1: 90})
        self.assertEqual(report.local_node_ids, [0])
        self.assertEqual(report.local_pages, 100)
        self.assertEqual(report.remote_pages, 90)
        self.assertAlmostEqual(report.remote_page_fraction, 90 / 190)
        self.assertFalse(report.is_local)

    def test_all_local_allocation(self):
        self._write_numa_maps(502, "7f0000000000 bind:1 anon=200 dirty=200 N1=200\n")
        mgr = self.make_manager(backend=RecordingBackend(initial=[4, 5, 6, 7]).as_backend())
        report = mgr.audit_numa_locality(pid=502)
        self.assertTrue(report.is_local)
        self.assertEqual(report.remote_pages, 0)
        self.assertEqual(report.local_node_ids, [1])
        self.assertEqual(report.remote_page_fraction, 0.0)

    def test_missing_numa_maps_is_unavailable_not_local(self):
        report = self.make_manager().audit_numa_locality(pid=99991)
        self.assertFalse(report.is_available)
        self.assertFalse(report.is_local)  # unknown is never reported as local
        self.assertEqual(report.pages_per_node, {})
        self.assertIn("unavailable", report.message)

    def test_locality_unknown_when_numa_map_missing(self):
        self._write_numa_maps(503, "7f0000000000 default anon=10 N0=10\n")
        mgr = CPUAffinityNUMAManager(
            sysfs_root=os.path.join(self.root, "does-not-exist"),
            proc_root=self.proc,
            affinity_backend=self.fake.as_backend(),
            autodetect_backend=False,
        )
        report = mgr.audit_numa_locality(pid=503)
        self.assertTrue(report.is_available)
        self.assertEqual(report.local_node_ids, [])
        self.assertFalse(report.is_local)
        self.assertIn("cannot be judged", report.message)


class TestBackendDetection(unittest.TestCase):
    def test_detection_matches_this_host(self):
        backend = detect_affinity_backend()
        if hasattr(os, "sched_setaffinity"):
            self.assertIsNotNone(backend)
            self.assertEqual(backend.name, "os.sched_setaffinity")
        else:
            # Windows/FreeBSD need psutil; macOS has no affinity API at all.
            self.assertTrue(backend is None or backend.name.startswith("psutil"))


if __name__ == "__main__":
    unittest.main()
