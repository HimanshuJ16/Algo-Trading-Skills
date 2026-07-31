import unittest
from iac_trading_host_manager import (
    IacTradingHostManagerEngine, TradingHostSpec, IacTradingHostReport
)

class TestIacTradingHostManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IacTradingHostManagerEngine()

    def test_iac_spec_approval_and_code_generation(self):
        spec = TradingHostSpec(
            host_name="co-ny4-hft-node-01",
            cpu_governor="performance",
            isolated_cpu_cores="2-15",
            disable_cpu_cstates=True,
            net_rmem_max_bytes=134_217_728,
            net_wmem_max_bytes=134_217_728,
            enable_ptp_clock_sync=True,
            hugepages_count=16
        )
        report = self.engine.audit_and_generate_iac(spec)

        self.assertEqual(report.status, "IAC_SPEC_APPROVED")
        self.assertTrue(report.is_cpu_governor_valid)
        self.assertTrue(report.is_cstates_disabled)
        self.assertTrue(report.is_socket_buffer_valid)
        self.assertIn("resource \"aws_instance\"", report.generated_terraform_hcl)
        self.assertIn("cpupower frequency-set -g performance", report.generated_ansible_playbook)

    def test_powersave_governor_rejection(self):
        # cpu_governor = 'powersave' -> REJECTED!
        spec = TradingHostSpec(
            host_name="co-ny4-hft-node-01",
            cpu_governor="powersave",
            isolated_cpu_cores="2-15",
            disable_cpu_cstates=True,
            net_rmem_max_bytes=134_217_728,
            net_wmem_max_bytes=134_217_728,
            enable_ptp_clock_sync=True
        )
        report = self.engine.audit_and_generate_iac(spec)

        self.assertEqual(report.status, "REJECTED_POWERSAVE_GOVERNOR")
        self.assertFalse(report.is_cpu_governor_valid)

    def test_cstates_enabled_rejection(self):
        # disable_cpu_cstates = False -> REJECTED!
        spec = TradingHostSpec(
            host_name="co-ny4-hft-node-01",
            cpu_governor="performance",
            isolated_cpu_cores="2-15",
            disable_cpu_cstates=False,
            net_rmem_max_bytes=134_217_728,
            net_wmem_max_bytes=134_217_728,
            enable_ptp_clock_sync=True
        )
        report = self.engine.audit_and_generate_iac(spec)

        self.assertEqual(report.status, "REJECTED_CSTATES_ENABLED")
        self.assertFalse(report.is_cstates_disabled)

if __name__ == '__main__':
    unittest.main()
