import unittest

from mas_singapore_algo_trading_guidelines import (
    DEFAULT_FORCED_ORDER_RANGE_BIDS,
    DEFAULT_SGX_ST_CIRCUIT_BREAKER_PCT,
    STATUS_APPROVED,
    MasSingaporeAlgoComplianceEngine,
    SgxOrderRequest,
    SingaporeAlgoControlConfig,
)


def compliant_config(**overrides) -> SingaporeAlgoControlConfig:
    """A fully compliant control config; override one field per test."""
    base = dict(
        algo_id="SG_QUANT_01",
        approved_trader_id="AT_88213",
        is_approved_trader_registered=True,
        has_cms_licence_or_exemption=True,
        is_pre_deployment_tested=True,
        has_kill_switch=True,
        max_order_value=1_000_000.0,
        limit_currency="SGD",
        max_order_rate_per_sec=50,
    )
    base.update(overrides)
    return SingaporeAlgoControlConfig(**base)


def compliant_order(**overrides) -> SgxOrderRequest:
    """A marketable BUY on DBS (D05) sitting comfortably inside every band."""
    base = dict(
        algo_id="SG_QUANT_01",
        symbol="D05",
        side="BUY",
        quantity=1_000,
        limit_price=30.00,
        currency="SGD",
        min_bid_size=0.01,
        forced_order_range_ref_price=30.00,
        circuit_breaker_ref_price=30.00,
        opposite_best_price=30.00,
        is_circuit_breaker_eligible=True,
        session="CONTINUOUS",
        current_order_rate_per_sec=1,
    )
    base.update(overrides)
    return SgxOrderRequest(**base)


class TestApprovalPath(unittest.TestCase):
    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_compliant_order_is_approved_with_no_breaches(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order()
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.breaches, ())
        # 1,000 x SGD 30.00, computed independently of the implementation.
        self.assertAlmostEqual(report.order_value, 30_000.0)
        self.assertAlmostEqual(report.circuit_breaker_deviation_pct, 0.0)
        self.assertAlmostEqual(report.forced_order_range_bids_away, 0.0)
        self.assertTrue(report.is_marketable)


class TestCircuitBreakerBand(unittest.TestCase):
    """SGX-ST Rule 8.14: a band around a 5-minute-lagged reference price that
    bites on the incoming aggressor, not on order price per se."""

    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_marketable_order_outside_band_is_rejected(self):
        # BUY at 36.00 against a reference of 30.00 is +20%, double the band.
        order = compliant_order(limit_price=36.00, opposite_best_price=35.00)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, "REJECTED_CIRCUIT_BREAKER_BAND")
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)
        self.assertAlmostEqual(report.circuit_breaker_deviation_pct, 20.0)

    def test_deviation_is_signed_so_direction_is_auditable(self):
        order = compliant_order(
            side="SELL", limit_price=24.00, opposite_best_price=25.00,
            forced_order_range_ref_price=24.00,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertAlmostEqual(report.circuit_breaker_deviation_pct, -20.0)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)

    def test_band_is_inclusive_at_exactly_the_threshold(self):
        # "Trading must be within or at the thresholds" -- exactly 10% passes.
        order = compliant_order(
            limit_price=33.00, opposite_best_price=32.00,
            forced_order_range_ref_price=33.00,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertAlmostEqual(
            report.circuit_breaker_deviation_pct, DEFAULT_SGX_ST_CIRCUIT_BREAKER_PCT
        )

    def test_hairline_breach_is_not_rounded_away(self):
        # REGRESSION: a previous revision rounded the deviation to 2dp BEFORE
        # comparing it against the band, so 10.0049% rounded to 10.00% and was
        # approved. Ref 10.00, limit 11.00049 -> +10.0049%, strictly outside.
        order = compliant_order(
            limit_price=11.00049,
            circuit_breaker_ref_price=10.00,
            forced_order_range_ref_price=11.00,
            opposite_best_price=11.00,
            min_bid_size=0.01,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)
        self.assertGreater(report.circuit_breaker_deviation_pct, 10.0)

    def test_non_marketable_order_outside_band_rests_with_a_warning(self):
        # A BUY at 20.00 while the best ask is 30.00 cannot match, so the rule
        # cannot bite on it now. It rests as a latent Cooling-Off trigger.
        order = compliant_order(
            limit_price=20.00, opposite_best_price=30.00,
            forced_order_range_ref_price=20.00,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertFalse(report.is_marketable)
        self.assertTrue(
            any("latent Cooling-Off trigger" in w for w in report.warnings),
            report.warnings,
        )

    def test_unknown_marketability_resolves_conservatively_to_rejection(self):
        order = compliant_order(limit_price=36.00, opposite_best_price=None)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIsNone(report.is_marketable)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)

    def test_ineligible_instrument_is_not_band_checked(self):
        order = compliant_order(
            limit_price=36.00, opposite_best_price=35.00,
            is_circuit_breaker_eligible=False,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIsNone(report.circuit_breaker_deviation_pct)
        self.assertNotIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)

    def test_mechanism_does_not_run_outside_continuous_trading(self):
        order = compliant_order(
            limit_price=36.00, opposite_best_price=35.00, session="CLOSING_ROUTINE"
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIsNone(report.circuit_breaker_deviation_pct)
        self.assertNotIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)

    def test_missing_reference_price_reports_none_not_zero(self):
        # A report that says "0.00% deviation" for a check that never ran is an
        # audit trail that lies.
        order = compliant_order(circuit_breaker_ref_price=None)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIsNone(report.circuit_breaker_deviation_pct)
        self.assertTrue(
            any("NOT evaluated" in w for w in report.warnings), report.warnings
        )

    def test_unknown_eligibility_is_treated_as_eligible(self):
        order = compliant_order(
            limit_price=36.00, opposite_best_price=35.00,
            is_circuit_breaker_eligible=None,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)

    def test_derivatives_band_can_be_overridden_per_contract(self):
        # SGX-DT price limits are per contract, not a universal 10%.
        config = compliant_config(circuit_breaker_band_pct=5.0)
        order = compliant_order(limit_price=32.00, opposite_best_price=31.00)
        report = self.engine.validate_sgx_order_compliance(config, order)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)


class TestForcedOrderRange(unittest.TestCase):
    """SGX-ST Practice Note 8.6: +/-N bids, overridable by the Force Key."""

    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_order_outside_range_without_force_key_is_rejected(self):
        # 31.00 vs a reference of 30.00 at a 0.01 bid = 100 bids away.
        order = compliant_order(
            limit_price=31.00, circuit_breaker_ref_price=31.00,
            opposite_best_price=31.00, min_bid_size=0.01,
            forced_order_range_ref_price=30.00,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIn("REJECTED_FORCED_ORDER_RANGE", report.breaches)
        self.assertAlmostEqual(report.forced_order_range_bids_away, 100.0, places=6)

    def test_force_key_confirmation_permits_the_order_with_a_warning(self):
        order = compliant_order(
            limit_price=31.00, circuit_breaker_ref_price=31.00,
            opposite_best_price=31.00, min_bid_size=0.01,
            forced_order_range_ref_price=30.00, force_key_confirmed=True,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(
            any("Force Key" in w for w in report.warnings), report.warnings
        )

    def test_exactly_at_the_range_boundary_passes(self):
        # 30 bids of 0.01 above 30.00 is 30.30 -- at the boundary, not outside.
        boundary = 30.00 + DEFAULT_FORCED_ORDER_RANGE_BIDS * 0.01
        order = compliant_order(
            limit_price=boundary, circuit_breaker_ref_price=boundary,
            opposite_best_price=boundary, min_bid_size=0.01,
            forced_order_range_ref_price=30.00,
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertAlmostEqual(
            report.forced_order_range_bids_away, float(DEFAULT_FORCED_ORDER_RANGE_BIDS),
            places=6,
        )

    def test_missing_bid_size_reports_none_not_zero(self):
        order = compliant_order(min_bid_size=None)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIsNone(report.forced_order_range_bids_away)
        self.assertNotIn("REJECTED_FORCED_ORDER_RANGE", report.breaches)


class TestGovernanceChecks(unittest.TestCase):
    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_unlicensed_entity_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(has_cms_licence_or_exemption=False), compliant_order()
        )
        self.assertEqual(report.status, "REJECTED_UNLICENSED_ENTITY")

    def test_unregistered_approved_trader_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(is_approved_trader_registered=False), compliant_order()
        )
        self.assertIn("REJECTED_UNREGISTERED_APPROVED_TRADER", report.breaches)

    def test_blank_approved_trader_id_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(approved_trader_id="   "), compliant_order()
        )
        self.assertIn("REJECTED_UNREGISTERED_APPROVED_TRADER", report.breaches)

    def test_untested_algorithm_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(is_pre_deployment_tested=False), compliant_order()
        )
        self.assertIn("REJECTED_ALGO_NOT_TESTED", report.breaches)

    def test_missing_kill_switch_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(has_kill_switch=False), compliant_order()
        )
        self.assertIn("REJECTED_NO_KILL_SWITCH", report.breaches)

    def test_algo_id_mismatch_is_rejected(self):
        # REGRESSION: a previous revision ignored order.algo_id entirely, so an
        # order tagged for one algorithm was audited against another's limits.
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order(algo_id="SG_QUANT_99")
        )
        self.assertIn("REJECTED_ALGO_ID_MISMATCH", report.breaches)


class TestPreExecutionAndRateLimits(unittest.TestCase):
    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_order_value_over_the_firm_ceiling_is_rejected(self):
        # 40,000 x SGD 30.00 = SGD 1,200,000 against a SGD 1,000,000 ceiling.
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order(quantity=40_000)
        )
        self.assertIn("REJECTED_PRE_EXECUTION_LIMIT", report.breaches)
        self.assertAlmostEqual(report.order_value, 1_200_000.0)

    def test_order_value_exactly_at_the_ceiling_passes(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order(quantity=33_333, limit_price=30.0,
                                                circuit_breaker_ref_price=30.0,
                                                forced_order_range_ref_price=30.0)
        )
        self.assertAlmostEqual(report.order_value, 999_990.0)
        self.assertNotIn("REJECTED_PRE_EXECUTION_LIMIT", report.breaches)

    def test_rate_over_the_firm_ceiling_is_rejected(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order(current_order_rate_per_sec=51)
        )
        self.assertIn("REJECTED_ORDER_RATE_LIMIT", report.breaches)

    def test_rate_exactly_at_the_ceiling_passes(self):
        report = self.engine.validate_sgx_order_compliance(
            compliant_config(), compliant_order(current_order_rate_per_sec=50)
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_market_order_is_valued_off_the_opposite_best_price(self):
        order = compliant_order(limit_price=None, opposite_best_price=30.00)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertAlmostEqual(report.order_value, 30_000.0)
        self.assertTrue(report.is_marketable)
        # The touch is at the reference price, so the first fill is inside the
        # band -- but the order can still walk past it.
        self.assertAlmostEqual(report.circuit_breaker_deviation_pct, 0.0)
        self.assertTrue(
            any("walk past the band" in w for w in report.warnings), report.warnings
        )

    def test_market_order_whose_touch_is_already_outside_the_band_is_rejected(self):
        # A market BUY when the best ask is 36.00 against a reference of 30.00
        # will trade outside the band on its very first fill.
        order = compliant_order(
            limit_price=None, opposite_best_price=36.00, circuit_breaker_ref_price=30.00
        )
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertIn("REJECTED_CIRCUIT_BREAKER_BAND", report.breaches)
        self.assertAlmostEqual(report.circuit_breaker_deviation_pct, 20.0)

    def test_unpriceable_market_order_fails_closed(self):
        order = compliant_order(limit_price=None, opposite_best_price=None)
        report = self.engine.validate_sgx_order_compliance(compliant_config(), order)
        self.assertEqual(report.status, "REJECTED_UNPRICEABLE_ORDER")
        self.assertIsNone(report.order_value)


class TestAuditTrailCompleteness(unittest.TestCase):
    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_every_check_runs_and_all_breaches_are_reported(self):
        # REGRESSION: a previous revision returned on the first breach, so a
        # remediation team saw one problem at a time.
        config = compliant_config(
            has_cms_licence_or_exemption=False,
            is_approved_trader_registered=False,
            has_kill_switch=False,
        )
        order = compliant_order(
            quantity=40_000, limit_price=36.00, opposite_best_price=35.00,
            circuit_breaker_ref_price=30.00, forced_order_range_ref_price=30.00,
            min_bid_size=0.01, current_order_rate_per_sec=99,
        )
        report = self.engine.validate_sgx_order_compliance(config, order)
        for expected in (
            "REJECTED_UNLICENSED_ENTITY",
            "REJECTED_UNREGISTERED_APPROVED_TRADER",
            "REJECTED_NO_KILL_SWITCH",
            "REJECTED_PRE_EXECUTION_LIMIT",
            "REJECTED_CIRCUIT_BREAKER_BAND",
            "REJECTED_FORCED_ORDER_RANGE",
            "REJECTED_ORDER_RATE_LIMIT",
        ):
            self.assertIn(expected, report.breaches)
        # The headline status reports the most serious breach, not the last.
        self.assertEqual(report.status, "REJECTED_UNLICENSED_ENTITY")
        self.assertIn("REJECTED_UNLICENSED_ENTITY", report.audit_notes)


class TestInputValidation(unittest.TestCase):
    """Malformed input must raise, never return a clean audit."""

    def setUp(self):
        self.engine = MasSingaporeAlgoComplianceEngine()

    def test_nan_price_raises_instead_of_being_approved(self):
        # REGRESSION: NaN compares False against every ceiling, so a previous
        # revision returned an APPROVED report for a NaN-priced order.
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(limit_price=float("nan"))
            )

    def test_infinite_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(limit_price=float("inf"))
            )

    def test_non_positive_quantity_raises(self):
        # REGRESSION: a negative quantity produced a negative order value that
        # passed the value ceiling and was approved.
        for bad in (0, -1_000):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    self.engine.validate_sgx_order_compliance(
                        compliant_config(), compliant_order(quantity=bad)
                    )

    def test_non_positive_reference_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(circuit_breaker_ref_price=0.0)
            )

    def test_unknown_side_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(side="SHORT")
            )

    def test_currency_mismatch_raises_rather_than_silently_comparing(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(limit_currency="SGD"),
                compliant_order(currency="USD"),
            )

    def test_negative_rate_counter_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(current_order_rate_per_sec=-1)
            )

    def test_non_integer_quantity_raises(self):
        with self.assertRaises(TypeError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(), compliant_order(quantity=1000.0)
            )

    def test_non_positive_firm_ceiling_raises(self):
        with self.assertRaises(ValueError):
            self.engine.validate_sgx_order_compliance(
                compliant_config(max_order_value=0.0), compliant_order()
            )

    def test_empty_circuit_breaker_sessions_raises(self):
        with self.assertRaises(ValueError):
            MasSingaporeAlgoComplianceEngine(circuit_breaker_sessions=[])


if __name__ == "__main__":
    unittest.main()
