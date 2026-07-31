import unittest
from network_segmentation_auditor import (
    NetworkSegmentationAuditorEngine, NetworkSubnet, FirewallRule, NetworkSegmentationReport
)

class TestNetworkSegmentationAuditorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NetworkSegmentationAuditorEngine()

    def test_compliant_multi_tier_topology(self):
        # 3 subnets: Public DMZ, Strategy Engine, Key Custody
        subnets = [
            NetworkSubnet("SUB_PUBLIC", "Public Web Ingress", "PUBLIC_DMZ", "10.0.1.0/24"),
            NetworkSubnet("SUB_STRAT", "Strategy Signal Engine", "STRATEGY_ENGINE", "10.0.2.0/24"),
            NetworkSubnet("SUB_VAULT", "MPC Key Custody Vault", "KEY_CUSTODY", "10.0.3.0/24"),
        ]

        # Valid rules:
        # Public -> Strategy Engine (port 443 HTTPS ALLOW)
        # Strategy Engine -> Key Custody (port 8443 ALLOW)
        rules = [
            FirewallRule("R1", "SUB_PUBLIC", "SUB_STRAT", "TCP", 443, "ALLOW"),
            FirewallRule("R2", "SUB_STRAT", "SUB_VAULT", "TCP", 8443, "ALLOW"),
        ]

        report = self.engine.audit_segmentation(subnets, rules)

        self.assertTrue(report.is_compliant)
        self.assertEqual(report.status, "COMPLIANT")
        self.assertEqual(len(report.violations_found), 0)

    def test_critical_security_violations_detection(self):
        # Subnets: Public DMZ, Dev Management, Execution Zone, Key Custody
        subnets = [
            NetworkSubnet("SUB_PUBLIC", "Public Web", "PUBLIC_DMZ", "10.0.1.0/24"),
            NetworkSubnet("SUB_DEV", "Dev Jump Host", "DEV_MANAGEMENT", "10.0.9.0/24"),
            NetworkSubnet("SUB_EXEC", "FIX Execution Host", "TRADING_EXECUTION", "10.0.2.0/24"),
            NetworkSubnet("SUB_VAULT", "Key Custody Vault", "KEY_CUSTODY", "10.0.3.0/24"),
        ]

        # Illegal rules:
        # 1. Public -> Execution Zone (Direct ingress) -> CRITICAL
        # 2. Public -> Dev SSH Port 22 -> HIGH
        # 3. Dev Management -> Key Custody Vault -> CRITICAL
        rules = [
            FirewallRule("R_BAD1", "SUB_PUBLIC", "SUB_EXEC", "TCP", 10443, "ALLOW"),
            FirewallRule("R_BAD2", "SUB_PUBLIC", "SUB_DEV", "TCP", 22, "ALLOW"),
            FirewallRule("R_BAD3", "SUB_DEV", "SUB_VAULT", "TCP", 22, "ALLOW"),
        ]

        report = self.engine.audit_segmentation(subnets, rules)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, "NON_COMPLIANT_SECURITY_VIOLATION")
        self.assertGreaterEqual(len(report.violations_found), 3)

if __name__ == '__main__':
    unittest.main()
