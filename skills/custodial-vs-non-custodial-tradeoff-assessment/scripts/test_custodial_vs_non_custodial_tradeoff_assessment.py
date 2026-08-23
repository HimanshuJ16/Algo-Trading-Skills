import unittest

from custodial_vs_non_custodial_tradeoff_assessment import (
    CustodialTradeoffAssessorEngine,
    CustodyArchitectureProfile,
    KEY_CONTROL_EXCHANGE,
    KEY_CONTROL_SELF,
    KEY_CONTROL_SHARED_CUSTODIAN,
    NoEligibleArchitectureError,
    REGULATORY_QUALIFIED_CUSTODIAN_REQUIRED,
    StrategyRequirements,
)


class TestCustodialTradeoffAssessorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodialTradeoffAssessorEngine()

    @staticmethod
    def _req(**kw):
        base = dict(
            strategy_name="Test",
            required_latency_ms=12_000.0,
            monthly_volume_usd=500_000.0,
            max_counterparty_risk_pct=100.0,
            gas_sensitivity_score=0.1,
            requires_key_sovereignty=False,
        )
        base.update(kw)
        return StrategyRequirements(**base)

    def _names(self, report):
        return [s.architecture_name for s in report.rankings]

    # ------------------------------------------------------------------
    # Hard constraints
    # ------------------------------------------------------------------
    def test_zero_counterparty_tolerance_disqualifies_custodial(self):
        # REGRESSION: the previous scoring inverted this. A tolerance of 0.0
        # produced a CEX security score of 100 (the best possible), and
        # CUSTODIAL_CEX was recommended at 89.5 to a zero-tolerance mandate.
        report = self.engine.evaluate_strategy_custody(
            self._req(strategy_name="Zero_Tolerance_Treasury", max_counterparty_risk_pct=0.0)
        )
        self.assertEqual(report.recommended_architecture, "NON_CUSTODIAL_DEX")
        self.assertNotIn("CUSTODIAL_CEX", self._names(report))
        self.assertNotIn("HYBRID_OFF_EXCHANGE_SETTLEMENT", self._names(report))

        reasons = {s.architecture_name: s.disqualification_reasons for s in report.disqualified}
        self.assertIn("CUSTODIAL_CEX", reasons)
        self.assertTrue(any("exceeds the mandate tolerance" in r for r in reasons["CUSTODIAL_CEX"]))

    def test_counterparty_tolerance_is_monotone_in_the_right_direction(self):
        # REGRESSION: previously a HIGHER tolerance produced a LOWER CEX score.
        # Raising tolerance must only ever admit more architectures, never fewer.
        admitted = []
        for tol in (0.0, 15.0, 100.0):
            report = self.engine.evaluate_strategy_custody(
                self._req(max_counterparty_risk_pct=tol)
            )
            admitted.append(set(self._names(report)))
        self.assertTrue(admitted[0] <= admitted[1] <= admitted[2])
        self.assertNotIn("CUSTODIAL_CEX", admitted[1])   # 100% exposure > 15% tolerance
        self.assertIn("HYBRID_OFF_EXCHANGE_SETTLEMENT", admitted[1])  # 15% == 15% passes
        self.assertIn("CUSTODIAL_CEX", admitted[2])

    def test_exposure_exactly_at_tolerance_is_eligible(self):
        # Boundary: the constraint is `residual > tolerance` disqualifies, so an
        # architecture sitting exactly on the mandate limit survives.
        report = self.engine.evaluate_strategy_custody(self._req(max_counterparty_risk_pct=15.0))
        self.assertIn("HYBRID_OFF_EXCHANGE_SETTLEMENT", self._names(report))
        report_below = self.engine.evaluate_strategy_custody(
            self._req(max_counterparty_risk_pct=14.99)
        )
        self.assertNotIn("HYBRID_OFF_EXCHANGE_SETTLEMENT", self._names(report_below))

    def test_key_sovereignty_vetoes_non_self_custody(self):
        # REGRESSION: sovereignty previously only lowered the CEX score to 30,
        # leaving a custodial architecture eligible and, at some inputs, winning.
        report = self.engine.evaluate_strategy_custody(
            self._req(requires_key_sovereignty=True)
        )
        self.assertEqual(self._names(report), ["NON_CUSTODIAL_DEX"])
        reasons = {s.architecture_name: s.disqualification_reasons for s in report.disqualified}
        self.assertTrue(any("sole key control" in r for r in reasons["CUSTODIAL_CEX"]))
        self.assertTrue(
            any("sole key control" in r for r in reasons["HYBRID_OFF_EXCHANGE_SETTLEMENT"])
        )

    def test_dex_disqualified_below_block_inclusion_floor(self):
        # REGRESSION: the old model gave NON_CUSTODIAL_DEX a latency score of 90
        # for ANY budget above 100 ms, so a 200 ms budget scored it near-perfect
        # despite Ethereum L1's ~12 s slot time.
        report = self.engine.evaluate_strategy_custody(self._req(required_latency_ms=200.0))
        self.assertNotIn("NON_CUSTODIAL_DEX", self._names(report))
        reasons = {s.architecture_name: s.disqualification_reasons for s in report.disqualified}
        self.assertTrue(any("Latency floor" in r for r in reasons["NON_CUSTODIAL_DEX"]))

    def test_latency_floor_exactly_at_budget_is_eligible(self):
        report = self.engine.evaluate_strategy_custody(self._req(required_latency_ms=12_000.0))
        self.assertIn("NON_CUSTODIAL_DEX", self._names(report))
        report_tight = self.engine.evaluate_strategy_custody(self._req(required_latency_ms=11_999.0))
        self.assertNotIn("NON_CUSTODIAL_DEX", self._names(report_tight))

    def test_qualified_custodian_requirement_filters_candidates(self):
        report = self.engine.evaluate_strategy_custody(
            self._req(regulatory_constraint=REGULATORY_QUALIFIED_CUSTODIAN_REQUIRED)
        )
        self.assertEqual(self._names(report), ["HYBRID_OFF_EXCHANGE_SETTLEMENT"])
        reasons = {s.architecture_name: s.disqualification_reasons for s in report.disqualified}
        self.assertTrue(any("qualified custodian" in r for r in reasons["NON_CUSTODIAL_DEX"]))

    def test_no_eligible_architecture_raises_instead_of_recommending(self):
        # Zero counterparty tolerance AND a 1 ms budget is unsatisfiable: the only
        # zero-exposure architecture cannot settle inside a block. Fail closed
        # rather than returning the least-bad mandate breach.
        with self.assertRaises(NoEligibleArchitectureError):
            self.engine.evaluate_strategy_custody(
                self._req(required_latency_ms=1.0, max_counterparty_risk_pct=0.0)
            )

    # ------------------------------------------------------------------
    # Ranking behaviour
    # ------------------------------------------------------------------
    def test_hft_strategy_recommends_custodial_or_hybrid(self):
        req = self._req(
            strategy_name="HFT_Arbitrage",
            required_latency_ms=1.0,
            monthly_volume_usd=50_000_000.0,
            max_counterparty_risk_pct=100.0,
            gas_sensitivity_score=0.9,
        )
        report = self.engine.evaluate_strategy_custody(req)
        self.assertIn(
            report.recommended_architecture,
            ["CUSTODIAL_CEX", "HYBRID_OFF_EXCHANGE_SETTLEMENT"],
        )
        self.assertNotIn("NON_CUSTODIAL_DEX", self._names(report))

    def test_treasury_strategy_recommends_non_custodial_dex(self):
        req = self._req(
            strategy_name="Long_Term_Treasury",
            required_latency_ms=12_000.0,
            monthly_volume_usd=500_000.0,
            max_counterparty_risk_pct=0.0,
            gas_sensitivity_score=0.1,
            requires_key_sovereignty=True,
        )
        report = self.engine.evaluate_strategy_custody(req)
        self.assertEqual(report.recommended_architecture, "NON_CUSTODIAL_DEX")

    def test_composite_matches_declared_weights(self):
        # Independently derived: latency headroom for the 1 ms CEX floor against a
        # 100 ms budget is 100*(1 - 1/100) = 99.0; custody 100 - 100 = 0.0;
        # cost 100 - 0.5*0 = 100.0. Composite = .40*99 + .35*0 + .25*100 = 64.6.
        report = self.engine.evaluate_strategy_custody(
            self._req(required_latency_ms=100.0, gas_sensitivity_score=0.5)
        )
        cex = next(s for s in report.rankings if s.architecture_name == "CUSTODIAL_CEX")
        self.assertEqual(cex.latency_headroom_score, 99.0)
        self.assertEqual(cex.custody_control_score, 0.0)
        self.assertEqual(cex.cost_efficiency_score, 100.0)
        self.assertEqual(cex.composite_suitability_score, 64.6)
        self.assertEqual(report.weights, {"latency": 0.40, "custody": 0.35, "cost": 0.25})

    def test_latency_headroom_is_zero_with_no_margin(self):
        # An architecture sitting exactly on the budget has no tail margin.
        report = self.engine.evaluate_strategy_custody(self._req(required_latency_ms=1.0))
        cex = next(s for s in report.rankings if s.architecture_name == "CUSTODIAL_CEX")
        self.assertEqual(cex.latency_headroom_score, 0.0)

    def test_ties_break_toward_lower_counterparty_exposure(self):
        engine = CustodialTradeoffAssessorEngine(
            profiles=[
                CustodyArchitectureProfile(
                    "RISKY_LISTED_FIRST", KEY_CONTROL_EXCHANGE, 1.0, 50.0, 0.0, False, "risky"
                ),
                CustodyArchitectureProfile(
                    "SAFER_LISTED_SECOND", KEY_CONTROL_SHARED_CUSTODIAN, 1.0, 50.0, 0.0, True, "safer"
                ),
            ]
        )
        report = engine.evaluate_strategy_custody(self._req())
        a, b = report.rankings
        self.assertEqual(a.composite_suitability_score, b.composite_suitability_score)
        self.assertEqual(a.architecture_name, "RISKY_LISTED_FIRST")  # equal exposure -> by name

        engine2 = CustodialTradeoffAssessorEngine(
            profiles=[
                CustodyArchitectureProfile(
                    "ZZ_RISKY_FIRST", KEY_CONTROL_EXCHANGE, 1.0, 60.0, 0.0, False, "risky"
                ),
                CustodyArchitectureProfile(
                    "AA_SAFE_SECOND", KEY_CONTROL_SHARED_CUSTODIAN, 1.0, 10.0, 100.0, True, "safe"
                ),
            ]
        )
        # Weights chosen so the two composites tie exactly; the safer one must win.
        report2 = engine2.evaluate_strategy_custody(
            self._req(gas_sensitivity_score=0.5)
        )
        self.assertEqual(report2.recommended_architecture, "AA_SAFE_SECOND")

    # ------------------------------------------------------------------
    # Volume advisory (previously an unused input)
    # ------------------------------------------------------------------
    def test_volume_triggers_off_exchange_advisory(self):
        # REGRESSION: monthly_volume_usd was documented in Prerequisites, the
        # Workflow and standards.md, but never read — $1 and $1B were identical.
        # Cost-weighted so the custodial venue (zero gas burden) actually wins;
        # under the default weights off-exchange settlement dominates it.
        cost_engine = CustodialTradeoffAssessorEngine(
            weights={"latency": 0.0, "custody": 0.0, "cost": 1.0}
        )
        low = cost_engine.evaluate_strategy_custody(
            self._req(required_latency_ms=1.0, gas_sensitivity_score=1.0, monthly_volume_usd=1.0)
        )
        high = cost_engine.evaluate_strategy_custody(
            self._req(
                required_latency_ms=1.0,
                gas_sensitivity_score=1.0,
                monthly_volume_usd=1_000_000_000.0,
            )
        )
        self.assertEqual(low.recommended_architecture, "CUSTODIAL_CEX")
        self.assertEqual(high.recommended_architecture, "CUSTODIAL_CEX")
        self.assertFalse(any("off-exchange settlement" in a for a in low.advisories))
        self.assertTrue(any("off-exchange settlement" in a for a in high.advisories))

    def test_volume_advisory_not_raised_for_non_custodial_recommendation(self):
        report = self.engine.evaluate_strategy_custody(
            self._req(monthly_volume_usd=1_000_000_000.0, requires_key_sovereignty=True)
        )
        self.assertEqual(report.recommended_architecture, "NON_CUSTODIAL_DEX")
        self.assertFalse(any("off-exchange settlement" in a for a in report.advisories))

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------
    def test_nan_and_infinite_inputs_are_rejected(self):
        # REGRESSION: a NaN latency budget previously produced a confident
        # recommendation numerically identical to a valid one, because every
        # `<=` comparison against NaN is False.
        for bad in (float("nan"), float("inf"), -1.0, 0.0):
            with self.assertRaises(ValueError):
                self._req(required_latency_ms=bad)

    def test_out_of_range_scores_are_rejected(self):
        # REGRESSION: a negative gas sensitivity previously produced a composite
        # of 107.5, breaking the documented 0-100 range.
        for bad in (-1.0, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                self._req(gas_sensitivity_score=bad)
        for bad in (-1.0, 101.0):
            with self.assertRaises(ValueError):
                self._req(max_counterparty_risk_pct=bad)

    def test_invalid_enums_and_types_are_rejected(self):
        with self.assertRaises(ValueError):
            self._req(regulatory_constraint="SOMETHING_MADE_UP")
        with self.assertRaises(ValueError):
            self._req(requires_key_sovereignty="yes")
        with self.assertRaises(ValueError):
            self._req(strategy_name="   ")
        with self.assertRaises(TypeError):
            self.engine.evaluate_strategy_custody({"strategy_name": "dict_not_dataclass"})

    def test_invalid_profile_definitions_are_rejected(self):
        with self.assertRaises(ValueError):  # unknown key control model
            CustodyArchitectureProfile("X", "MAGIC", 1.0, 0.0, 0.0, False, "s")
        with self.assertRaises(ValueError):  # exposure outside 0-100
            CustodyArchitectureProfile("X", KEY_CONTROL_SELF, 1.0, 150.0, 0.0, False, "s")
        with self.assertRaises(ValueError):  # zero latency floor is not physical
            CustodyArchitectureProfile("X", KEY_CONTROL_SELF, 0.0, 0.0, 0.0, False, "s")

    # ------------------------------------------------------------------
    # Engine configuration
    # ------------------------------------------------------------------
    def test_invalid_weights_are_rejected(self):
        with self.assertRaises(ValueError):  # does not sum to 1
            CustodialTradeoffAssessorEngine(weights={"latency": 0.5, "custody": 0.5, "cost": 0.5})
        with self.assertRaises(ValueError):  # wrong key set
            CustodialTradeoffAssessorEngine(weights={"latency": 1.0})
        with self.assertRaises(ValueError):  # negative weight
            CustodialTradeoffAssessorEngine(
                weights={"latency": 1.5, "custody": -0.5, "cost": 0.0}
            )

    def test_duplicate_and_empty_profiles_are_rejected(self):
        p = CustodyArchitectureProfile("DUP", KEY_CONTROL_SELF, 1.0, 0.0, 0.0, False, "s")
        with self.assertRaises(ValueError):
            CustodialTradeoffAssessorEngine(profiles=[p, p])
        with self.assertRaises(ValueError):
            CustodialTradeoffAssessorEngine(profiles=[])

    def test_profiles_are_copied_against_caller_mutation(self):
        p = CustodyArchitectureProfile("P", KEY_CONTROL_SELF, 1.0, 0.0, 0.0, False, "s")
        engine = CustodialTradeoffAssessorEngine(profiles=[p])
        p.residual_counterparty_exposure_pct = 99.0
        report = engine.evaluate_strategy_custody(self._req(max_counterparty_risk_pct=0.0))
        self.assertEqual(report.recommended_architecture, "P")

    def test_weights_change_the_ranking(self):
        req = self._req(required_latency_ms=12_000.0, max_counterparty_risk_pct=100.0)
        custody_heavy = CustodialTradeoffAssessorEngine(
            weights={"latency": 0.0, "custody": 1.0, "cost": 0.0}
        )
        self.assertEqual(
            custody_heavy.evaluate_strategy_custody(req).recommended_architecture,
            "NON_CUSTODIAL_DEX",
        )
        # Cost-heavy with a gas-sensitive strategy flips the answer to the venue
        # carrying no on-chain cost. CUSTODIAL_CEX and HYBRID share a latency
        # floor, so latency weighting alone cannot separate them — cost can.
        cost_heavy = CustodialTradeoffAssessorEngine(
            weights={"latency": 0.0, "custody": 0.0, "cost": 1.0}
        )
        self.assertEqual(
            cost_heavy.evaluate_strategy_custody(
                self._req(
                    required_latency_ms=12_000.0,
                    max_counterparty_risk_pct=100.0,
                    gas_sensitivity_score=1.0,
                )
            ).recommended_architecture,
            "CUSTODIAL_CEX",
        )

    def test_illustrative_default_warning_is_surfaced(self):
        report = self.engine.evaluate_strategy_custody(self._req())
        self.assertTrue(report.uses_illustrative_defaults)
        self.assertTrue(any("illustrative default" in a for a in report.advisories))

        custom = CustodialTradeoffAssessorEngine(
            profiles=[
                CustodyArchitectureProfile(
                    "VETTED", KEY_CONTROL_SELF, 1.0, 0.0, 0.0, False, "own due diligence"
                )
            ]
        )
        vetted = custom.evaluate_strategy_custody(self._req())
        self.assertFalse(vetted.uses_illustrative_defaults)

    def test_single_eligible_candidate_is_flagged(self):
        report = self.engine.evaluate_strategy_custody(self._req(requires_key_sovereignty=True))
        self.assertEqual(len(report.rankings), 1)
        self.assertTrue(any("Only one architecture is eligible" in a for a in report.advisories))

    def test_mev_mitigation_does_not_overclaim_prevention(self):
        report = self.engine.evaluate_strategy_custody(self._req(requires_key_sovereignty=True))
        mev = next(m for m in report.key_risk_mitigations if "mempool" in m)
        self.assertIn("mitigates rather than eliminates", mev)


if __name__ == '__main__':
    unittest.main()
