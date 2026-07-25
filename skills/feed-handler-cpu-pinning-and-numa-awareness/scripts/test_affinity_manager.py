"""
Unit tests for feed-handler-cpu-pinning-and-numa-awareness skill.
"""
import unittest
from affinity_manager import CPUAffinityNUMAManager


class TestCPUAffinityNUMAManager(unittest.TestCase):

    def setUp(self):
        self.mgr = CPUAffinityNUMAManager()

    def test_topology_discovery(self):
        topo = self.mgr.discover_topology()
        self.assertGreaterEqual(topo.logical_core_count, 1)
        self.assertGreaterEqual(topo.physical_core_count, 1)
        self.assertIn(0, topo.available_cpu_ids)

    def test_cpu_affinity_binding(self):
        # Bind process to core 0
        report = self.mgr.bind_process_affinity([0])
        self.assertTrue(report.is_success)
        self.assertEqual(report.assigned_cores, [0])
        self.assertEqual(report.numa_node_id, 0)


if __name__ == "__main__":
    unittest.main()
