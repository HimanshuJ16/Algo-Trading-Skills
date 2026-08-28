import unittest
from datetime import date

from smart_contract_audit_requirements_before_defi_integration import (
    Config, Engine,
    SmartContractAuditRequirementsBeforeDeFiIntegrationEngine,
    DeFiProtocolSpec, AuditReport, AuditFirmTier, DeFiIntegrationGateReport,
    DeFiDueDiligenceError,
    VIOLATION_INSUFFICIENT_AUDITS, VIOLATION_UNRESOLVED_VULNERABILITIES,
    VIOLATION_UNTESTED_CODEBASE, VIOLATION_DANGEROUS_TIMELOCK,
    VIOLATION_WEAK_MULTISIG, VIOLATION_INADEQUATE_BUG_BOUNTY,
)

#: Fixed so every assertion about audit staleness is reproducible.
ASSESSMENT_DATE = date(2026, 6, 1)


def make_audit(
    firm_name="Trail of Bits",
    tier=AuditFirmTier.TIER_1_TOP_REPUTATION,
    audit_date_iso="2026-01-15",
    critical=0,
    high=0,
    remediated=True,
    verified=True,
    scope_covers_deployed_code=True,
):
    return AuditReport(
        firm_name=firm_name,
        firm_tier=tier,
        audit_date_iso=audit_date_iso,
        critical_findings_count=critical,
        high_findings_count=high,
        all_critical_high_remediated=remediated,
        fix_verification_confirmed=verified,
        scope_covers_deployed_code=scope_covers_deployed_code,
    )


def make_protocol(**overrides):
    """A protocol that clears all six gates, so each test varies one thing."""
    spec = dict(
        protocol_name="Reference Lending Pool",
        contract_address="0x0000000000000000000000000000000000000001",
        tvl_usd=10_000_000.0,
        mainnet_days_active=365,
        audits=[
            make_audit("Trail of Bits", audit_date_iso="2026-01-15"),
            make_audit("OpenZeppelin", audit_date_iso="2026-03-20"),
        ],
        has_active_bug_bounty=True,
        bug_bounty_max_payout_usd=1_000_000.0,
        admin_timelock_delay_hours=72.0,
        admin_multisig_signers_count=7,
        admin_multisig_threshold_required=4,
        has_emergency_pause_circuit_breaker=True,
    )
    spec.update(overrides)
    return DeFiProtocolSpec(**spec)


def codes(report):
    """Violation code prefixes, so assertions do not depend on message prose."""
    return [v.split(":", 1)[0] for v in report.blocking_violations]


def advisory_codes(report):
    return [a.split(":", 1)[0] for a in report.advisories]


class TestEngineLegacy(unittest.TestCase):
    """The legacy shims stay importable; other repo skills expose the same pair."""

    def test_init(self):
        engine = Engine(Config())
        self.assertEqual(
            engine.config.name,
            "smart-contract-audit-requirements-before-defi-integration",
        )

    def test_run(self):
        engine = Engine(Config())
        self.assertTrue(engine.run())


class BaseEngineTest(unittest.TestCase):
    def setUp(self):
        self.engine = SmartContractAuditRequirementsBeforeDeFiIntegrationEngine()

    def evaluate(self, **overrides):
        return self.engine.evaluate_protocol(
            make_protocol(**overrides), assessment_date=ASSESSMENT_DATE
        )


class TestApprovalPath(BaseEngineTest):

    def test_fully_approved_protocol(self):
        report = self.evaluate()
        self.assertIsInstance(report, DeFiIntegrationGateReport)
        self.assertTrue(report.is_approved)
        self.assertEqual(report.blocking_violations, [])
        self.assertEqual(report.safety_score_pct, 100.0)

    def test_default_engine_thresholds_match_documented_values(self):
        # SKILL.md, standards.md and assets/checklist.md all quote these.
        self.assertEqual(self.engine.min_tier1_audits_required, 2)
        self.assertEqual(self.engine.min_mainnet_days, 90)
        self.assertEqual(self.engine.min_timelock_hours, 48.0)
        self.assertEqual(self.engine.min_bug_bounty_usd, 100_000.0)
        self.assertEqual(self.engine.min_multisig_threshold, 3)
        self.assertEqual(self.engine.min_multisig_signers, 5)
        self.assertEqual(self.engine.min_multisig_threshold_ratio, 0.5)

    def test_score_is_fraction_of_gates_passed(self):
        # Independently derived: exactly two of six gates fail (timelock and
        # mainnet longevity), so 4/6 = 66.666... -> 66.67 after rounding.
        report = self.evaluate(admin_timelock_delay_hours=0.0, mainnet_days_active=10)
        self.assertCountEqual(
            codes(report), [VIOLATION_UNTESTED_CODEBASE, VIOLATION_DANGEROUS_TIMELOCK]
        )
        self.assertEqual(report.safety_score_pct, 66.67)
        self.assertFalse(report.is_approved)

    def test_score_of_zero_when_every_gate_fails(self):
        report = self.evaluate(
            audits=[
                make_audit(
                    tier=AuditFirmTier.UNVERIFIED_INDIVIDUAL,
                    critical=3, remediated=False, verified=False,
                )
            ],
            mainnet_days_active=0,
            admin_timelock_delay_hours=0.0,
            admin_multisig_signers_count=2,
            admin_multisig_threshold_required=1,
            has_active_bug_bounty=False,
        )
        self.assertEqual(report.safety_score_pct, 0.0)
        self.assertEqual(len(report.blocking_violations), 6)


class TestAuditScopeGate(BaseEngineTest):
    """Regression: an audit only counts if it covers the deployed bytecode."""

    def test_unattested_scope_does_not_count_as_a_tier1_audit(self):
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", scope_covers_deployed_code=True),
                make_audit("OpenZeppelin", scope_covers_deployed_code=None),
            ]
        )
        self.assertIn(VIOLATION_INSUFFICIENT_AUDITS, codes(report))
        self.assertIn("never attested", report.blocking_violations[0])
        self.assertIn("Found 1 Tier-1", report.blocking_violations[0])

    def test_scope_mismatch_names_the_deployed_address(self):
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", scope_covers_deployed_code=True),
                make_audit("OpenZeppelin", scope_covers_deployed_code=False),
            ]
        )
        self.assertIn(VIOLATION_INSUFFICIENT_AUDITS, codes(report))
        self.assertIn("does not match code deployed", report.blocking_violations[0])
        self.assertIn(
            "0x0000000000000000000000000000000000000001",
            report.blocking_violations[0],
        )

    def test_tier2_audits_do_not_satisfy_the_tier1_requirement(self):
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits"),
                make_audit("CertiK", tier=AuditFirmTier.TIER_2_REPUTABLE),
            ]
        )
        self.assertIn(VIOLATION_INSUFFICIENT_AUDITS, codes(report))
        self.assertIn("tier TIER_2_REPUTABLE", report.blocking_violations[0])

    def test_stale_audit_advises_but_does_not_block(self):
        # 2025-01-15 -> 2026-06-01 is 502 days, past the 365-day default.
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", audit_date_iso="2025-01-15"),
                make_audit("OpenZeppelin", audit_date_iso="2026-03-20"),
            ]
        )
        self.assertTrue(report.is_approved)
        self.assertIn("STALE_AUDIT", advisory_codes(report))
        stale = next(a for a in report.advisories if a.startswith("STALE_AUDIT"))
        self.assertIn("502 days old", stale)

    def test_fresh_audits_raise_no_staleness_advisory(self):
        report = self.evaluate()
        self.assertNotIn("STALE_AUDIT", advisory_codes(report))


class TestUnresolvedFindingsGate(BaseEngineTest):

    def test_unremediated_critical_findings_block_integration(self):
        report = self.evaluate(
            audits=[
                make_audit(
                    "Trail of Bits", critical=1, high=2, remediated=False, verified=False
                ),
                make_audit("OpenZeppelin"),
            ]
        )
        self.assertFalse(report.is_approved)
        self.assertIn(VIOLATION_UNRESOLVED_VULNERABILITIES, codes(report))
        self.assertIn("Trail of Bits (1C/2H)", report.blocking_violations[0])

    def test_remediated_but_unverified_findings_still_block(self):
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", critical=1, remediated=True, verified=False),
                make_audit("OpenZeppelin"),
            ]
        )
        self.assertIn(VIOLATION_UNRESOLVED_VULNERABILITIES, codes(report))

    def test_clean_audit_with_unset_remediation_flags_is_not_unresolved(self):
        # Regression: a report that found nothing has nothing to remediate. The
        # previous implementation flagged it purely because the booleans were
        # left False, producing a false UNRESOLVED_VULNERABILITIES block.
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", critical=0, high=0,
                           remediated=False, verified=False),
                make_audit("OpenZeppelin", critical=0, high=0,
                           remediated=False, verified=False),
            ]
        )
        self.assertNotIn(VIOLATION_UNRESOLVED_VULNERABILITIES, codes(report))
        self.assertTrue(report.is_approved)


class TestMultisigGate(BaseEngineTest):
    """Regression: the old gate read only the threshold, never the signer set."""

    def test_low_threshold_blocks(self):
        report = self.evaluate(
            admin_multisig_signers_count=5, admin_multisig_threshold_required=1
        )
        self.assertIn(VIOLATION_WEAK_MULTISIG, codes(report))
        self.assertIn("threshold 1 < required 3", report.blocking_violations[0])

    def test_n_of_n_scheme_blocks_even_at_high_threshold(self):
        # 5-of-5 passed the old threshold-only check. Losing one key would
        # permanently lock admin control.
        report = self.evaluate(
            admin_multisig_signers_count=5, admin_multisig_threshold_required=5
        )
        self.assertIn(VIOLATION_WEAK_MULTISIG, codes(report))
        self.assertIn("N-of-N", report.blocking_violations[0])

    def test_sub_majority_threshold_blocks(self):
        # 3-of-9 = 33%, below the 50% floor, yet passed the old check.
        report = self.evaluate(
            admin_multisig_signers_count=9, admin_multisig_threshold_required=3
        )
        self.assertIn(VIOLATION_WEAK_MULTISIG, codes(report))
        self.assertIn("33% signing threshold", report.blocking_violations[0])

    def test_too_few_total_signers_blocks(self):
        # 3-of-4: threshold and ratio are fine, but the documented "out of at
        # least 5 total keys" requirement was never implemented.
        report = self.evaluate(
            admin_multisig_signers_count=4, admin_multisig_threshold_required=3
        )
        self.assertIn(VIOLATION_WEAK_MULTISIG, codes(report))
        self.assertIn("only 4 total signer key(s)", report.blocking_violations[0])

    def test_three_of_five_is_the_documented_minimum_and_passes(self):
        report = self.evaluate(
            admin_multisig_signers_count=5, admin_multisig_threshold_required=3
        )
        self.assertNotIn(VIOLATION_WEAK_MULTISIG, codes(report))

    def test_small_signer_set_over_one_million_tvl_raises_advisory(self):
        report = self.evaluate(
            tvl_usd=2_000_000.0,
            admin_multisig_signers_count=5,
            admin_multisig_threshold_required=3,
        )
        self.assertIn("SMALL_SIGNER_SET", advisory_codes(report))

    def test_signer_independence_advisory_always_present(self):
        self.assertIn("SIGNER_INDEPENDENCE_UNVERIFIED", advisory_codes(self.evaluate()))


class TestTimelockGate(BaseEngineTest):

    def test_zero_timelock_blocks(self):
        report = self.evaluate(admin_timelock_delay_hours=0.0)
        self.assertIn(VIOLATION_DANGEROUS_TIMELOCK, codes(report))

    def test_exactly_forty_eight_hours_passes(self):
        report = self.evaluate(admin_timelock_delay_hours=48.0)
        self.assertNotIn(VIOLATION_DANGEROUS_TIMELOCK, codes(report))

    def test_just_under_forty_eight_hours_blocks(self):
        report = self.evaluate(admin_timelock_delay_hours=47.99)
        self.assertIn(VIOLATION_DANGEROUS_TIMELOCK, codes(report))

    def test_guardian_present_raises_untimelocked_power_advisory(self):
        report = self.evaluate(has_emergency_pause_circuit_breaker=True)
        self.assertIn("UNTIMELOCKED_GUARDIAN", advisory_codes(report))

    def test_no_guardian_raises_no_circuit_breaker_advisory(self):
        report = self.evaluate(has_emergency_pause_circuit_breaker=False)
        self.assertIn("NO_CIRCUIT_BREAKER", advisory_codes(report))


class TestBugBountyGate(BaseEngineTest):

    def test_absent_programme_reports_absence_not_a_payout_comparison(self):
        # Regression: the old message read "$500,000.00 < $100,000.00" when the
        # protocol simply had no programme, which is both false and confusing.
        report = self.evaluate(
            has_active_bug_bounty=False, bug_bounty_max_payout_usd=500_000.0
        )
        self.assertIn(VIOLATION_INADEQUATE_BUG_BOUNTY, codes(report))
        bounty = next(
            v for v in report.blocking_violations
            if v.startswith(VIOLATION_INADEQUATE_BUG_BOUNTY)
        )
        self.assertIn("No active bug bounty program", bounty)
        self.assertNotIn("500,000.00 <", bounty)
        self.assertIsNone(report.bug_bounty_tvl_coverage_ratio)

    def test_payout_below_absolute_floor_blocks(self):
        report = self.evaluate(bug_bounty_max_payout_usd=99_999.0)
        self.assertIn(VIOLATION_INADEQUATE_BUG_BOUNTY, codes(report))

    def test_coverage_ratio_is_payout_over_tvl(self):
        # Independently derived: 1,000,000 / 10,000,000,000 = 0.0001.
        report = self.evaluate(
            tvl_usd=10_000_000_000.0, bug_bounty_max_payout_usd=1_000_000.0
        )
        self.assertAlmostEqual(report.bug_bounty_tvl_coverage_ratio, 0.0001, places=9)
        self.assertIn("BOUNTY_SMALL_VS_TVL", advisory_codes(report))

    def test_thin_tvl_coverage_advises_but_does_not_block(self):
        report = self.evaluate(
            tvl_usd=5_000_000_000.0, bug_bounty_max_payout_usd=1_000_000.0
        )
        self.assertNotIn(VIOLATION_INADEQUATE_BUG_BOUNTY, codes(report))
        self.assertTrue(report.is_approved)
        self.assertIn("BOUNTY_SMALL_VS_TVL", advisory_codes(report))

    def test_zero_tvl_yields_no_ratio_and_no_division_error(self):
        report = self.evaluate(tvl_usd=0.0)
        self.assertIsNone(report.bug_bounty_tvl_coverage_ratio)
        self.assertNotIn("BOUNTY_SMALL_VS_TVL", advisory_codes(report))


class TestMainnetLongevityGate(BaseEngineTest):

    def test_young_deployment_blocks(self):
        report = self.evaluate(mainnet_days_active=89)
        self.assertIn(VIOLATION_UNTESTED_CODEBASE, codes(report))

    def test_exactly_ninety_days_passes(self):
        report = self.evaluate(mainnet_days_active=90)
        self.assertNotIn(VIOLATION_UNTESTED_CODEBASE, codes(report))


class TestSpecValidation(BaseEngineTest):
    """Data-entry errors must raise, never score."""

    def test_threshold_exceeding_signer_count_raises(self):
        # Regression: a 4-of-2 multisig cannot exist, yet the old engine scored
        # it as satisfying the multisig requirement.
        with self.assertRaises(DeFiDueDiligenceError) as ctx:
            self.evaluate(
                admin_multisig_signers_count=2, admin_multisig_threshold_required=4
            )
        self.assertIn("cannot exist on-chain", str(ctx.exception))

    def test_empty_audit_list_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(audits=[])

    def test_negative_tvl_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(tvl_usd=-1.0)

    def test_negative_timelock_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(admin_timelock_delay_hours=-48.0)

    def test_nan_tvl_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(tvl_usd=float("nan"))

    def test_blank_contract_address_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(contract_address="   ")

    def test_negative_finding_count_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(audits=[make_audit(critical=-1), make_audit("OpenZeppelin")])

    def test_plain_string_firm_tier_is_accepted(self):
        # AuditFirmTier subclasses str, so "TIER_1_TOP_REPUTATION" compares
        # equal to the member and callers reasonably pass it. It must evaluate,
        # not raise AttributeError on .value.
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits", tier="TIER_1_TOP_REPUTATION"),
                make_audit("OpenZeppelin", tier="TIER_1_TOP_REPUTATION"),
            ]
        )
        self.assertTrue(report.is_approved)

    def test_plain_string_lower_tier_is_excluded_with_a_readable_message(self):
        report = self.evaluate(
            audits=[
                make_audit("Trail of Bits"),
                make_audit("CertiK", tier="TIER_2_REPUTABLE"),
            ]
        )
        self.assertIn(VIOLATION_INSUFFICIENT_AUDITS, codes(report))
        self.assertIn("tier TIER_2_REPUTABLE", report.blocking_violations[0])

    def test_unrecognised_firm_tier_raises_rather_than_silently_demoting(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(audits=[make_audit(tier="TIER_1"), make_audit("OpenZeppelin")])

    def test_unparseable_audit_date_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(audits=[make_audit(audit_date_iso="Q1 2026")])

    def test_audit_dated_after_assessment_raises(self):
        with self.assertRaises(DeFiDueDiligenceError):
            self.evaluate(audits=[make_audit(audit_date_iso="2027-01-01")])


class TestEngineConfiguration(unittest.TestCase):

    def test_zero_required_audits_rejected(self):
        with self.assertRaises(DeFiDueDiligenceError):
            SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
                min_tier1_audits_required=0
            )

    def test_signer_floor_below_threshold_floor_rejected(self):
        with self.assertRaises(DeFiDueDiligenceError):
            SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
                min_multisig_threshold=5, min_multisig_signers=3
            )

    def test_out_of_range_threshold_ratio_rejected(self):
        with self.assertRaises(DeFiDueDiligenceError):
            SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
                min_multisig_threshold_ratio=1.5
            )

    def test_negative_timelock_floor_rejected(self):
        with self.assertRaises(DeFiDueDiligenceError):
            SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
                min_timelock_hours=-1.0
            )

    def test_relaxed_policy_can_admit_a_single_audit(self):
        engine = SmartContractAuditRequirementsBeforeDeFiIntegrationEngine(
            min_tier1_audits_required=1
        )
        report = engine.evaluate_protocol(
            make_protocol(audits=[make_audit("Trail of Bits")]),
            assessment_date=ASSESSMENT_DATE,
        )
        self.assertTrue(report.is_approved)


class TestDeterminism(BaseEngineTest):

    def test_same_inputs_produce_identical_reports(self):
        first = self.evaluate()
        second = self.evaluate()
        self.assertEqual(first, second)

    def test_assessment_date_appears_in_notes(self):
        report = self.evaluate()
        self.assertIn("assessed 2026-06-01", report.audit_notes)

    def test_omitted_assessment_date_defaults_to_today(self):
        # Audits are backdated, so today's date must still evaluate cleanly.
        report = self.engine.evaluate_protocol(make_protocol())
        self.assertIn(date.today().isoformat(), report.audit_notes)


if __name__ == '__main__':
    unittest.main()
