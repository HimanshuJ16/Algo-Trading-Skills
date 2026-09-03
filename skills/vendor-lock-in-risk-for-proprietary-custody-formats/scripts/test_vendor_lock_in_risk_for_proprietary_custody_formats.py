"""Unit tests for vendor-lock-in-risk-for-proprietary-custody-formats."""
import logging
import unittest

from vendor_lock_in_risk_for_proprietary_custody_formats import (
    RECOVERY_TOOL_DELAY_DAYS,
    AssetPortfolio,
    CustodyAnalyzerError,
    CustodyArchitecture,
    CustodyLockInAnalyzer,
    CustodyProviderProfile,
    KeyFormatType,
    LockInRiskLevel,
)

logging.getLogger("vendor_lock_in_risk_for_proprietary_custody_formats").setLevel(logging.CRITICAL)


def make_provider(**overrides) -> CustodyProviderProfile:
    """A fully portable open-standard custodian; override to introduce a flaw."""
    base = dict(
        provider_id="CUST-OPEN-01",
        provider_name="Open Custody Systems",
        architecture=CustodyArchitecture.MULTISIG_ON_CHAIN,
        supported_key_formats=[
            KeyFormatType.BIP39_MNEMONIC,
            KeyFormatType.SLIP39_SHAMIR,
            KeyFormatType.BIP32_HD_PATH,
        ],
        open_source_recovery_tool_available=True,
        requires_vendor_active_api_for_exit=False,
    )
    base.update(overrides)
    return CustodyProviderProfile(**base)


class TestCustodyLockInAnalyzer(unittest.TestCase):
    def setUp(self):
        self.analyzer = CustodyLockInAnalyzer()
        self.portfolio = AssetPortfolio(
            total_value_usd=50_000_000.0,
            num_wallets=10,
            num_blockchain_networks=5,
            avg_network_gas_fee_per_tx_usd=10.0,
        )

    # --- Baseline classification ------------------------------------------------

    def test_low_risk_open_standard_provider(self):
        res = self.analyzer.evaluate_custody_provider(make_provider(), self.portfolio)

        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.LOW)
        self.assertEqual(res.open_standard_compliance_ratio, 100.0)
        # 50 * 1.00 (BIP-39) + 30 (offline tool) + 20 (no vendor API) = 100.
        self.assertEqual(res.portability_score, 100.0)

    def test_high_risk_proprietary_mpc_provider(self):
        prop_provider = make_provider(
            provider_id="CUST-PROP-02",
            provider_name="Proprietary MPC Vault Inc",
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.PROPRIETARY_MPC_SHARE],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=True,
        )
        res = self.analyzer.evaluate_custody_provider(prop_provider, self.portfolio)

        # 50 * 0.30, with neither bonus earned.
        self.assertEqual(res.portability_score, 15.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.CRITICAL)
        self.assertEqual(res.open_standard_compliance_ratio, 0.0)
        self.assertGreaterEqual(len(res.risk_factors), 2)

    def test_mpc_share_with_offline_tool_is_recoverable_not_critical(self):
        """A published offline recovery utility is what rescues a proprietary share."""
        provider = make_provider(
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.PROPRIETARY_MPC_SHARE],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        # 50 * 0.30 + 30 + 20 = 65.
        self.assertEqual(res.portability_score, 65.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.MEDIUM)

    # --- Regression: derivation metadata is not key material --------------------

    def test_derivation_path_only_provider_is_critical(self):
        """A BIP-32 path carries no secret, so exporting only paths exports nothing.

        The previous scoring counted BIP32_HD_PATH as a portable open format,
        which rated this custodian LOW at a portability score of 100.
        """
        provider = make_provider(
            provider_id="CUST-PATHS-ONLY",
            supported_key_formats=[KeyFormatType.BIP32_HD_PATH],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        self.assertEqual(res.portability_score, 0.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.CRITICAL)
        self.assertTrue(any("carries no secret" in rf for rf in res.risk_factors))

    def test_derivation_path_only_provider_fails_drill_in_both_scenarios(self):
        provider = make_provider(
            supported_key_formats=[KeyFormatType.BIP32_HD_PATH],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        for responsive in (True, False):
            with self.subTest(is_vendor_responsive=responsive):
                success, msg = self.analyzer.simulate_disaster_recovery_drill(
                    provider, is_vendor_responsive=responsive
                )
                self.assertFalse(success)
                self.assertIn("No exportable key material", msg)

    def test_seed_export_without_disclosed_derivation_is_not_low_risk(self):
        """A seed you cannot map to accounts is not a complete recovery package."""
        provider = make_provider(supported_key_formats=[KeyFormatType.BIP39_MNEMONIC])
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        self.assertEqual(res.portability_score, 100.0)
        self.assertEqual(res.open_standard_compliance_ratio, 100.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.MEDIUM)
        self.assertTrue(any("derivation paths" in rf for rf in res.risk_factors))

    # --- Regression: non-exportable material cannot be rescued by tooling -------

    def test_hsm_blob_only_is_critical_even_with_tool_and_no_api_dependency(self):
        """Bonuses are gated on exportable material; previously this scored 50 (HIGH)."""
        provider = make_provider(
            architecture=CustodyArchitecture.PROPRIETARY_VAULT,
            supported_key_formats=[KeyFormatType.PROPRIETARY_HSM_BLOB],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=False,
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        self.assertEqual(res.portability_score, 0.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.CRITICAL)

    def test_empty_key_format_list_is_critical(self):
        provider = make_provider(supported_key_formats=[])
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        self.assertEqual(res.open_standard_compliance_ratio, 0.0)
        self.assertEqual(res.portability_score, 0.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.CRITICAL)

    # --- Regression: scoring must be monotonic in export options ----------------

    def test_extra_proprietary_formats_never_lower_the_portability_score(self):
        """Offering an additional export path cannot make a custodian less portable.

        The previous mean-based score fell from 100 to 75 here purely because the
        custodian declared two extra proprietary formats alongside the unchanged
        open export path.
        """
        lean = make_provider(
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC, KeyFormatType.BIP32_HD_PATH]
        )
        superset = make_provider(
            supported_key_formats=[
                KeyFormatType.BIP39_MNEMONIC,
                KeyFormatType.BIP32_HD_PATH,
                KeyFormatType.PROPRIETARY_MPC_SHARE,
                KeyFormatType.PROPRIETARY_HSM_BLOB,
            ]
        )
        lean_res = self.analyzer.evaluate_custody_provider(lean, self.portfolio)
        superset_res = self.analyzer.evaluate_custody_provider(superset, self.portfolio)

        self.assertGreaterEqual(superset_res.portability_score, lean_res.portability_score)
        self.assertEqual(superset_res.portability_score, 100.0)
        # The coverage diagnostic still falls; it is reported, not scored on.
        self.assertEqual(superset_res.open_standard_compliance_ratio, 50.0)

    # --- Format-specific caveats ------------------------------------------------

    def test_slip39_only_export_flags_bip39_incompatibility(self):
        provider = make_provider(
            supported_key_formats=[KeyFormatType.SLIP39_SHAMIR, KeyFormatType.BIP32_HD_PATH]
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        # 50 * 0.90 + 30 + 20 = 95.
        self.assertEqual(res.portability_score, 95.0)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.LOW)
        self.assertTrue(any("SLIP-0039" in rf for rf in res.risk_factors))

    def test_wif_only_export_flags_per_address_enumeration_risk(self):
        provider = make_provider(supported_key_formats=[KeyFormatType.WIF_PRIVATE_KEY])
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        # 50 * 0.85 + 30 + 20 = 92.5. WIF is not seed-derived, so no path is needed.
        self.assertEqual(res.portability_score, 92.5)
        self.assertEqual(res.lock_in_risk_level, LockInRiskLevel.LOW)
        self.assertTrue(any("one key per address" in rf for rf in res.risk_factors))

    # --- Migration cost and timeline --------------------------------------------

    def test_migration_cost_and_timeline_estimation(self):
        provider = make_provider(
            provider_id="CUST-03",
            provider_name="Mid-Risk Custodian",
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC, KeyFormatType.PROPRIETARY_MPC_SHARE],
            flat_export_fee_usd=5_000.0,
            estimated_exit_days=7,
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        # 10 wallets * 5 networks * $10 gas = $500 gas + $5,000 flat fee = $5,500.
        self.assertEqual(res.estimated_migration_cost_usd, 5_500.0)
        self.assertEqual(res.estimated_migration_days, 7)

    def test_missing_recovery_tool_extends_the_exit_timeline(self):
        provider = make_provider(
            open_source_recovery_tool_available=False, estimated_exit_days=7
        )
        res = self.analyzer.evaluate_custody_provider(provider, self.portfolio)

        self.assertEqual(res.estimated_migration_days, 7 + RECOVERY_TOOL_DELAY_DAYS)

    def test_zero_wallet_portfolio_costs_only_the_flat_fee(self):
        portfolio = AssetPortfolio(
            total_value_usd=0.0, num_wallets=0, num_blockchain_networks=0
        )
        provider = make_provider(flat_export_fee_usd=2_500.0)
        res = self.analyzer.evaluate_custody_provider(provider, portfolio)

        self.assertEqual(res.estimated_migration_cost_usd, 2_500.0)

    # --- Input validation -------------------------------------------------------

    def test_negative_portfolio_and_provider_inputs_are_rejected(self):
        cases = {
            "total_value_usd": AssetPortfolio(-1.0, 10, 5),
            "num_wallets": AssetPortfolio(1.0, -10, 5),
            "num_blockchain_networks": AssetPortfolio(1.0, 10, -5),
            "avg_network_gas_fee_per_tx_usd": AssetPortfolio(1.0, 10, 5, -2.0),
        }
        for field_name, portfolio in cases.items():
            with self.subTest(field=field_name):
                with self.assertRaises(CustodyAnalyzerError):
                    self.analyzer.evaluate_custody_provider(make_provider(), portfolio)

        for field_name, provider in {
            "flat_export_fee_usd": make_provider(flat_export_fee_usd=-1.0),
            "estimated_exit_days": make_provider(estimated_exit_days=-1),
        }.items():
            with self.subTest(field=field_name):
                with self.assertRaises(CustodyAnalyzerError):
                    self.analyzer.evaluate_custody_provider(provider, self.portfolio)

    # --- Disaster recovery drill ------------------------------------------------

    def test_disaster_recovery_drill_success_when_vendor_offline(self):
        provider = make_provider(
            provider_id="CUST-SAFE",
            provider_name="Safe Multisig Custody",
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC],
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=False
        )
        self.assertTrue(success)
        self.assertIn("Emergency Key Recovery SUCCESSFUL", msg)

    def test_disaster_recovery_drill_failure_when_vendor_offline(self):
        provider = make_provider(
            provider_id="CUST-LOCKED",
            provider_name="Closed Vault Inc",
            architecture=CustodyArchitecture.PROPRIETARY_VAULT,
            supported_key_formats=[KeyFormatType.PROPRIETARY_HSM_BLOB],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=True,
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=False
        )
        self.assertFalse(success)
        self.assertIn("No exportable key material", msg)

    def test_offline_drill_fails_when_exit_depends_on_the_vendor_api(self):
        """Open key formats do not help if the keys only exist behind a dead API.

        Previously this returned success, contradicting the skill's own pitfall
        that assets are locked unless shares are extractable offline.
        """
        provider = make_provider(
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC, KeyFormatType.BIP32_HD_PATH],
            open_source_recovery_tool_available=True,
            requires_vendor_active_api_for_exit=True,
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=False
        )
        self.assertFalse(success)
        self.assertIn("only obtainable through the vendor service", msg)

    def test_offline_drill_succeeds_on_open_format_without_a_vendor_tool(self):
        """A BIP-39 seed held offline needs no vendor-specific software."""
        provider = make_provider(
            supported_key_formats=[KeyFormatType.BIP39_MNEMONIC],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=False,
        )
        success, _ = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=False
        )
        self.assertTrue(success)

    def test_offline_drill_fails_on_proprietary_share_without_a_tool(self):
        provider = make_provider(
            architecture=CustodyArchitecture.MPC_THRESHOLD,
            supported_key_formats=[KeyFormatType.PROPRIETARY_MPC_SHARE],
            open_source_recovery_tool_available=False,
            requires_vendor_active_api_for_exit=False,
        )
        success, msg = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=False
        )
        self.assertFalse(success)
        self.assertIn("Assets LOCKED", msg)

    def test_responsive_vendor_drill_is_flagged_as_not_self_sovereign(self):
        """A successful export while the vendor is up proves nothing about insolvency."""
        provider = make_provider(requires_vendor_active_api_for_exit=True)
        success, msg = self.analyzer.simulate_disaster_recovery_drill(
            provider, is_vendor_responsive=True
        )
        self.assertTrue(success)
        self.assertIn("NOT evidence of self-sovereignty", msg)


if __name__ == "__main__":
    unittest.main()
