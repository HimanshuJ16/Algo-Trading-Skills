import unittest
from smart_contract_audit_requirements_before_defi_integration import (
    Config, Engine,
    SmartContractAuditRequirementsBeforeDeFiIntegrationEngine,
    DeFiProtocolSpec, AuditReport, AuditFirmTier, DeFiIntegrationGateReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(engine.config.name, "smart-contract-audit-requirements-before-defi-integration")

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())

class TestSmartContractAuditEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
            min_tier1_audits_required=2,
            min_mainnet_days=90,
            min_timelock_hours=48.0,
            min_bug_bounty_usd=100000.0
        )

        self.valid_audits = [
            AuditReport("Trail of Bits", AuditFirmTier.TIER_1_TOP_REPUTATION, "2026-01-15", 0, 0, True, True),
            AuditReport("OpenZeppelin", AuditFirmTier.TIER_1_TOP_REPUTATION, "2026-03-20", 0, 0, True, True),
        ]

    def test_fully_approved_protocol(self):
        proto = DeFiProtocolSpec(
            protocol_name="Aave V3 Lending Pool",
            contract_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            tvl_usd=5_000_000_000.0,
            mainnet_days_active=365,
            audits=self.valid_audits,
            has_active_bug_bounty=True,
            bug_bounty_max_payout_usd=1000000.0,
            admin_timelock_delay_hours=72.0,
            admin_multisig_signers_count=7,
            admin_multisig_threshold_required=4,
            has_emergency_pause_circuit_breaker=True
        )
        report = self.engine.evaluate_protocol(proto)
        self.assertTrue(report.is_approved)
        self.assertEqual(report.safety_score_pct, 100.0)
        self.assertEqual(len(report.blocking_violations), 0)

    def test_unresolved_audit_findings_blocks_integration(self):
        bad_audits = [
            AuditReport("Trail of Bits", AuditFirmTier.TIER_1_TOP_REPUTATION, "2026-01-15", 1, 2, False, False), # Unresolved!
            AuditReport("OpenZeppelin", AuditFirmTier.TIER_1_TOP_REPUTATION, "2026-03-20", 0, 0, True, True),
        ]
        proto = DeFiProtocolSpec(
            protocol_name="Risky Yield Vault",
            contract_address="0x123...",
            tvl_usd=10_000_000.0,
            mainnet_days_active=120,
            audits=bad_audits,
            has_active_bug_bounty=True,
            bug_bounty_max_payout_usd=200000.0,
            admin_timelock_delay_hours=48.0,
            admin_multisig_signers_count=5,
            admin_multisig_threshold_required=3,
            has_emergency_pause_circuit_breaker=True
        )
        report = self.engine.evaluate_protocol(proto)
        self.assertFalse(report.is_approved)
        self.assertIn("UNRESOLVED_VULNERABILITIES", report.blocking_violations[0])

    def test_insufficient_timelock_and_multisig_blocks_integration(self):
        proto = DeFiProtocolSpec(
            protocol_name="Centralized Admin Protocol",
            contract_address="0x456...",
            tvl_usd=50_000_000.0,
            mainnet_days_active=100,
            audits=self.valid_audits,
            has_active_bug_bounty=True,
            bug_bounty_max_payout_usd=150000.0,
            admin_timelock_delay_hours=0.0,     # Dangerous 0h timelock!
            admin_multisig_signers_count=2,
            admin_multisig_threshold_required=1,# Dangerous 1-of-2 multisig!
            has_emergency_pause_circuit_breaker=True
        )
        report = self.engine.evaluate_protocol(proto)
        self.assertFalse(report.is_approved)
        self.assertTrue(any("DANGEROUS_TIMELOCK" in v for v in report.blocking_violations))
        self.assertTrue(any("WEAK_MULTISIG" in v for v in report.blocking_violations))

if __name__ == '__main__':
    unittest.main()
