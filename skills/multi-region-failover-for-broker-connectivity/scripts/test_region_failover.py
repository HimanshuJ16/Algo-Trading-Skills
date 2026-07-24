"""
Unit tests for multi-region-failover-for-broker-connectivity skill.
"""
import unittest
from region_failover import EndpointState, RegionFailoverManager


class TestRegionFailoverManager(unittest.TestCase):

    def test_failover_on_primary_down(self):
        """When primary is down after threshold failures, failover to backup."""
        # Health check always fails for primary
        call_count = {"n": 0}
        def health_fn(ep):
            if ep.name == "primary":
                return False
            return True

        mgr = RegionFailoverManager(failure_threshold=3, cooldown_seconds=0, health_check_fn=health_fn)
        mgr.register_endpoint("primary", "us-east-1", "https://api-east.broker.com", is_primary=True)
        mgr.register_endpoint("backup", "us-west-2", "https://api-west.broker.com")

        # Probe primary 3 times to trigger DOWN
        for _ in range(3):
            mgr.probe_health("primary")
        mgr.probe_health("backup")

        self.assertEqual(mgr.endpoints["primary"].state, EndpointState.DOWN)

        event = mgr.evaluate_failover()
        self.assertIsNotNone(event)
        self.assertEqual(event.to_endpoint, "backup")
        self.assertEqual(mgr.active_endpoint, "backup")

    def test_no_failover_when_healthy(self):
        """No failover when primary is healthy."""
        mgr = RegionFailoverManager(health_check_fn=lambda ep: True)
        mgr.register_endpoint("primary", "us-east-1", "https://api.broker.com", is_primary=True)
        mgr.register_endpoint("backup", "eu-west-1", "https://api-eu.broker.com")

        mgr.probe_health("primary")
        event = mgr.evaluate_failover()
        self.assertIsNone(event)
        self.assertEqual(mgr.active_endpoint, "primary")

    def test_failback_after_recovery(self):
        """After primary recovers and cooldown passes, failback should occur."""
        fail_primary = {"fail": True}
        def health_fn(ep):
            if ep.name == "primary":
                return not fail_primary["fail"]
            return True

        mgr = RegionFailoverManager(failure_threshold=2, cooldown_seconds=0, health_check_fn=health_fn)
        mgr.register_endpoint("primary", "us-east-1", "https://api.broker.com", is_primary=True)
        mgr.register_endpoint("backup", "eu-west-1", "https://api-eu.broker.com")

        # Fail primary and failover
        for _ in range(2):
            mgr.probe_health("primary")
        mgr.probe_health("backup")
        mgr.evaluate_failover()
        self.assertEqual(mgr.active_endpoint, "backup")

        # Primary recovers
        fail_primary["fail"] = False
        mgr.probe_health("primary")
        event = mgr.evaluate_failback()
        self.assertIsNotNone(event)
        self.assertEqual(mgr.active_endpoint, "primary")


if __name__ == "__main__":
    unittest.main()
