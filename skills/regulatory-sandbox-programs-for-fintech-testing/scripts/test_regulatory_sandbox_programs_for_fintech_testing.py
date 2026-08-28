import unittest
from regulatory_sandbox_programs_for_fintech_testing import (
    RegulatorySandboxProgramsForFintechTestingEngine, ComplianceResult,
    SandboxParameters, SandboxTelemetry, SandboxAuditReport,
    SANDBOX_FRAMEWORKS,
)


def fca_params(**overrides) -> SandboxParameters:
    """Boundary conditions as they would be transcribed from an approval letter."""
    kwargs = dict(
        program_name="FCA_UK_SANDBOX",
        jurisdiction="UK",
        max_allowed_clients=500,
        max_transaction_volume_usd=5_000_000.0,
        max_aum_usd=10_000_000.0,
        max_duration_months=6,
        framework_key="FCA_UK",
    )
    kwargs.update(overrides)
    return SandboxParameters(**kwargs)


class TestRegulatorySandboxProgramsForFintechTesting(unittest.TestCase):

    def setUp(self):
        self.engine = RegulatorySandboxProgramsForFintechTestingEngine(
            {"FCA_UK": fca_params()}
        )

    # --- legacy API -------------------------------------------------------

    def test_legacy_valid(self):
        res = self.engine.check({"valid": True})
        self.assertTrue(res.is_compliant)

    def test_legacy_invalid(self):
        res = self.engine.check({"valid": False})
        self.assertFalse(res.is_compliant)

    def test_legacy_edge(self):
        res = self.engine.check({})
        self.assertFalse(res.is_compliant)

    # --- no fabricated defaults ------------------------------------------

    def test_engine_ships_no_default_limits(self):
        """No regulator publishes universal caps, so the registry starts empty."""
        self.assertEqual(RegulatorySandboxProgramsForFintechTestingEngine().programs, {})

    def test_unregistered_program_fails_closed(self):
        engine = RegulatorySandboxProgramsForFintechTestingEngine()
        report = engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=1,
            cumulative_volume_usd=1.0,
            current_aum_usd=1.0,
            elapsed_months=1,
        ))
        self.assertEqual(report.status, "PROGRAM_NOT_FOUND")
        self.assertFalse(report.is_within_limits)
        self.assertEqual(report.breaches[0].breach_type, "PROGRAM_NOT_FOUND")

    def test_sebi_innovation_sandbox_marked_offline(self):
        """The Innovation Sandbox has no live customers, so it is out of scope."""
        innovation = SANDBOX_FRAMEWORKS["SEBI_IN_INNOVATION"]
        self.assertFalse(innovation.live_customers_permitted)
        self.assertTrue(SANDBOX_FRAMEWORKS["SEBI_IN"].live_customers_permitted)
        for framework in SANDBOX_FRAMEWORKS.values():
            self.assertFalse(hasattr(framework, "max_allowed_clients"))

    # --- compliant path ---------------------------------------------------

    def test_fca_sandbox_compliant(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=300,
            cumulative_volume_usd=2_000_000.0,
            current_aum_usd=4_000_000.0,
            elapsed_months=3,
            has_exit_plan=True,
        ))
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")
        self.assertTrue(report.is_within_limits)
        self.assertEqual(report.breaches, [])
        self.assertEqual(report.client_capacity_pct, 60.0)   # 300/500
        self.assertEqual(report.volume_capacity_pct, 40.0)   # 2M/5M
        self.assertEqual(report.aum_capacity_pct, 40.0)      # 4M/10M
        self.assertEqual(report.time_remaining_months, 3)
        self.assertEqual(report.warnings, [])

    def test_program_key_lookup_is_case_insensitive(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="fca_uk",
            active_clients=1,
            cumulative_volume_usd=1.0,
            current_aum_usd=1.0,
            elapsed_months=1,
        ))
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")

    # --- boundary semantics ----------------------------------------------

    def test_exactly_at_cap_is_compliant(self):
        """Caps are inclusive maxima: at the cap is permitted, above it is not."""
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=500,
            cumulative_volume_usd=5_000_000.0,
            current_aum_usd=10_000_000.0,
            elapsed_months=6,
        ))
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")
        self.assertEqual(report.client_capacity_pct, 100.0)
        self.assertEqual(report.time_remaining_months, 0)

    def test_one_over_cap_breaches_each_dimension(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=501,
            cumulative_volume_usd=5_000_000.01,
            current_aum_usd=10_000_000.01,
            elapsed_months=7,
        ))
        self.assertEqual(report.status, "SANDBOX_BREACHED")
        self.assertEqual(
            {b.breach_type for b in report.breaches},
            {"CLIENT_LIMIT_BREACH", "VOLUME_CAP_BREACH", "AUM_CAP_BREACH", "SANDBOX_EXPIRED"},
        )
        self.assertEqual(report.time_remaining_months, -1)

    def test_client_limit_and_volume_breach(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=600,
            cumulative_volume_usd=6_000_000.0,
            current_aum_usd=4_000_000.0,
            elapsed_months=4,
            has_exit_plan=True,
        ))
        self.assertEqual(report.status, "SANDBOX_BREACHED")
        self.assertFalse(report.is_within_limits)
        self.assertEqual(len(report.breaches), 2)
        self.assertEqual(
            {b.breach_type for b in report.breaches},
            {"CLIENT_LIMIT_BREACH", "VOLUME_CAP_BREACH"},
        )
        self.assertEqual(report.client_capacity_pct, 120.0)

    def test_aum_breach_is_reported_independently(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=10,
            cumulative_volume_usd=1_000.0,
            current_aum_usd=12_500_000.0,
            elapsed_months=1,
        ))
        self.assertEqual([b.breach_type for b in report.breaches], ["AUM_CAP_BREACH"])
        self.assertEqual(report.aum_capacity_pct, 125.0)

    # --- duration and extensions -----------------------------------------

    def test_sandbox_expired_breach(self):
        engine = RegulatorySandboxProgramsForFintechTestingEngine({
            "SEBI_IN": SandboxParameters(
                program_name="SEBI_IN_SANDBOX",
                jurisdiction="IN",
                max_allowed_clients=200,
                max_transaction_volume_usd=2_000_000.0,
                max_aum_usd=5_000_000.0,
                max_duration_months=6,
                framework_key="SEBI_IN",
            )
        })
        report = engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="SEBI_IN",
            active_clients=100,
            cumulative_volume_usd=1_000_000.0,
            current_aum_usd=1_000_000.0,
            elapsed_months=8,
            has_exit_plan=True,
        ))
        self.assertEqual(report.status, "SANDBOX_BREACHED")
        self.assertEqual([b.breach_type for b in report.breaches], ["SANDBOX_EXPIRED"])
        self.assertEqual(report.time_remaining_months, -2)

    def test_granted_extension_extends_the_deadline(self):
        engine = RegulatorySandboxProgramsForFintechTestingEngine(
            {"FCA_UK": fca_params(approved_extension_months=3)}
        )
        telemetry = SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=10,
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=8,
        )
        report = engine.audit_sandbox_telemetry(telemetry)
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")
        self.assertEqual(report.time_remaining_months, 1)
        # Without the extension the same elapsed time is an expiry breach.
        self.assertEqual(
            self.engine.audit_sandbox_telemetry(telemetry).status, "SANDBOX_BREACHED"
        )

    # --- exit plan --------------------------------------------------------

    def test_missing_exit_plan_breaches_when_required(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=10,
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=1,
            has_exit_plan=False,
        ))
        self.assertEqual([b.breach_type for b in report.breaches], ["MISSING_EXIT_PLAN"])

    def test_exit_plan_not_required_does_not_breach(self):
        """Regression: a program flagged as not requiring an exit plan must not
        raise MISSING_EXIT_PLAN merely because the flag is False."""
        engine = RegulatorySandboxProgramsForFintechTestingEngine(
            {"FCA_UK": fca_params(requires_exit_strategy=False)}
        )
        report = engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=10,
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=1,
            has_exit_plan=False,
        ))
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")

    # --- pre-breach warnings ---------------------------------------------

    def test_warning_emitted_before_breach(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=450,          # 90% of 500
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=1,
        ))
        self.assertEqual(report.status, "SANDBOX_COMPLIANT")
        self.assertTrue(any("client utilisation at 90.0%" in w for w in report.warnings))

    def test_expiry_warning_in_final_month(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=1,
            cumulative_volume_usd=1.0,
            current_aum_usd=1.0,
            elapsed_months=5,            # 1 of 6 months left
        ))
        self.assertTrue(any("1 month(s) of approved testing remaining" in w
                            for w in report.warnings))

    def test_no_warning_below_threshold(self):
        report = self.engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=100,
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=1,
        ))
        self.assertEqual(report.warnings, [])

    def test_custom_warning_threshold(self):
        engine = RegulatorySandboxProgramsForFintechTestingEngine(
            {"FCA_UK": fca_params()}, warning_threshold_pct=95.0
        )
        report = engine.audit_sandbox_telemetry(SandboxTelemetry(
            program_key="FCA_UK",
            active_clients=450,          # 90% -- below the 95% threshold
            cumulative_volume_usd=1_000.0,
            current_aum_usd=1_000.0,
            elapsed_months=1,
        ))
        self.assertEqual(report.warnings, [])

    def test_invalid_warning_threshold_rejected(self):
        with self.assertRaises(ValueError):
            RegulatorySandboxProgramsForFintechTestingEngine(warning_threshold_pct=0.0)
        with self.assertRaises(ValueError):
            RegulatorySandboxProgramsForFintechTestingEngine(warning_threshold_pct=101.0)

    # --- input validation -------------------------------------------------

    def test_zero_client_cap_rejected_not_divide_by_zero(self):
        with self.assertRaises(ValueError):
            fca_params(max_allowed_clients=0)

    def test_non_positive_caps_rejected(self):
        for override in (
            {"max_transaction_volume_usd": 0.0},
            {"max_aum_usd": -1.0},
            {"max_duration_months": 0},
            {"approved_extension_months": -1},
            {"program_name": "  "},
        ):
            with self.subTest(override=override):
                with self.assertRaises(ValueError):
                    fca_params(**override)

    def test_unknown_framework_key_rejected(self):
        with self.assertRaises(ValueError):
            fca_params(framework_key="NOT_A_REGULATOR")

    def test_negative_telemetry_rejected(self):
        for override in (
            {"active_clients": -1},
            {"cumulative_volume_usd": -1.0},
            {"current_aum_usd": -1.0},
            {"elapsed_months": -1},
            {"program_key": ""},
        ):
            with self.subTest(override=override):
                kwargs = dict(
                    program_key="FCA_UK",
                    active_clients=1,
                    cumulative_volume_usd=1.0,
                    current_aum_usd=1.0,
                    elapsed_months=1,
                )
                kwargs.update(override)
                with self.assertRaises(ValueError):
                    SandboxTelemetry(**kwargs)

    def test_register_program_rejects_bad_input(self):
        engine = RegulatorySandboxProgramsForFintechTestingEngine()
        with self.assertRaises(ValueError):
            engine.register_program("", fca_params())
        with self.assertRaises(TypeError):
            engine.register_program("FCA_UK", {"max_allowed_clients": 500})


if __name__ == '__main__':
    unittest.main()
