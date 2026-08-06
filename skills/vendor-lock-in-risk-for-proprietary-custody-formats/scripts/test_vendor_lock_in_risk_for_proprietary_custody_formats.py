import unittest
from vendor_lock_in_risk_for_proprietary_custody_formats import (
    KeyFormatType,
    LockInRiskLevel,
    CustodyArchitecture,
    CustodyProviderProfile,
    AssetPortfolio,
    CustodyLockInAssessment,
    CustodyLockInAnalyzer,
    CustodyAnalyzerError,
)


class TestCustodyLockInAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = CustodyLockInAnalyzer()
        self.portfolio = AssetPortfolio(
            total_value_usd=50_000_000.0,
            num_wallets=10,
            num_blockchain_networks=5,
            avg_network_gas_fee_per_tx_usd=10.0,
        )

    def test_low_risk_open_standard_provider(self):
        open_provider = CustodyProviderProfile(
            provider_id="CUST-OPEN-01",
            provider_name="Open Custody Systems",
            architecture=CustodyArchitecture.MULTISIG_ON_CHAIN,
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC, KeyFormatType.SLIP39_SHAMIR, KeyFormatType.BIP32_HD_PATH],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        res = self.analyzer.evaluate_custody_provider(open_provider, self.portfolio)

        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.LOW)
        self.assertEqual(res.open_standard_compliance_ratio, 100.0)
        self.assertTrue(res.portability_score >= 85.0)

    def test_high_risk_proprietary_mpc_provider(self):
        prop_provider = CustodyProviderProfile(
            provider_id="CUST-PROP-02",
            provider_name="Proprietary MPC Vault Inc",
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.PROPRIETARY_MPC_SHARE],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=True,
        )
        res = self.analyzer.evaluate_custody_provider(prop_provider, self.portfolio)

        self.assertIn(res.lock_in_risk_level, [LockInRiskLevel.HIGH, LockInRiskLevel.CRITICAL])
        self.assertEqual(res.open_standard_compliance_ratio, 0.0)
        self.assertTrue(len(res.risk_factors) >= 2)

    def test_migration_cost_and_timeline_estimation(self):
        provider = CustodyProviderProfile(
            provider_id="CUST-03",
            provider_name="Mid-Risk Custodian",
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC, KeyFormatType.PROPRIETARY_MPC_SHARE],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
            flat_export_fee_usd=5_000.0,
            estimated_exit_days=7,
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        # 10 wallets * 5 networks * $10 gas = $500 gas + $5000 flat fee = $5500
        self.assertEqual(res.estimated_migration_cost_usd, 5_500.0)
        self.assertEqual(res.estimated_migration_days, 7)

    def test_disaster_recovery_drill_success_when_vendor_offline(self):
        provider = CustodyProviderProfile(
            provider_id="CUST-SAFE",
            provider_name="Safe Multisig Custody",
            architecture=CustodyArchitecture.MULTISIG_ON_CHAIN,
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(provider, is_vendor_responsive=False)
        self.assertTrue(success)
        self.assertIn("Emergency Key Recovery SUCCESSFUL", msg)

    def test_disaster_recovery_drill_failure_when_vendor_offline(self):
        provider = CustodyProviderProfile(
            provider_id="CUST-LOCKED",
            provider_name="Closed Vault Inc",
            architecture=CustodyArchitecture.PROPRIETARY_VAULT,
            supported_key_formats=[KeyFormatType.PROPRIETARY_HSM_BLOB],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=True,
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(provider, is_vendor_responsive=False)
        self.assertFalse(success)
        self.assertIn("Assets LOCKED", msg)


if __name__ == "__main__":
    unittest.main()

