"""Unit tests for fpga-based-market-data-processing-evaluation.

Expected values are derived by hand from the stated definitions rather than by
re-running the engine's own arithmetic. Where a test guards a specific defect
fixed in v2.0.0 the docstring names the old behaviour it would have accepted.
"""
import logging
import math
import unittest

from fpga_based_market_data_processing_evaluation import (
    BASIS_APPLICATION_INTERNAL,
    BASIS_STAC_T0_ACTIONABLE,
    BASIS_VENDOR_COMPONENT,
    BASIS_WIRE_TO_WIRE,
    FLAG_ALPHA_DECAY_INCONSISTENT,
    FLAG_APPLICATION_BASIS,
    FLAG_BASIS_MISMATCH,
    FLAG_COMPONENT_BASIS,
    FLAG_INSUFFICIENT_SAMPLES,
    FLAG_NEGATIVE_LATENCY_DELTA,
    FLAG_ZERO_TAIL_SPREAD,
    FpgaEvaluationReport,
    FpgaHardwareCosts,
    FpgaMarketDataEvaluationEngine,
    LatencyProfile,
    RECOMMENDATION_FPGA,
    RECOMMENDATION_INSUFFICIENT,
    RECOMMENDATION_SOFTWARE,
    StrategyAlphaMetrics,
)

#: 5 minutes expressed in nanoseconds -- the "low-turnover strategy" of the
#: skill's first documented pitfall.
FIVE_MINUTES_NS = 5 * 60 * 1e9


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def cpu_profile(basis=BASIS_WIRE_TO_WIRE, **overrides):
    """Software kernel-bypass path: p50 2,500ns, p99 8,000ns, max 15,000ns."""
    kwargs = dict(
        p50_latency_ns=2_500.0,
        p99_latency_ns=8_000.0,
        max_latency_ns=15_000.0,
        std_dev_jitter_ns=1_200.0,
        measurement_basis=basis,
    )
    kwargs.update(overrides)
    return LatencyProfile(**kwargs)


def fpga_profile(basis=BASIS_WIRE_TO_WIRE, **overrides):
    """FPGA SmartNIC path: p50 250ns, p99 300ns, max 450ns."""
    kwargs = dict(
        p50_latency_ns=250.0,
        p99_latency_ns=300.0,
        max_latency_ns=450.0,
        std_dev_jitter_ns=15.0,
        measurement_basis=basis,
    )
    kwargs.update(overrides)
    return LatencyProfile(**kwargs)


def standard_costs(**overrides):
    """Capex $40,000 (card $15k + perpetual IP core $25k); recurring $20,000/yr."""
    kwargs = dict(
        smartnic_hardware_usd=15_000.0,
        ip_core_licensing_usd=25_000.0,
        annual_engineering_maintenance_usd=20_000.0,
    )
    kwargs.update(overrides)
    return FpgaHardwareCosts(**kwargs)


class TestLatencyMetrics(unittest.TestCase):
    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine()

    def test_latency_and_jitter_metrics_are_independently_derivable(self):
        """dL = 2500-250 = 2250ns; worst case 15000-450 = 14550ns;
        tail spreads 5500ns and 50ns give a ratio of exactly 110."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.median_latency_reduction_ns, 2_250.0)
        self.assertEqual(report.worst_case_latency_reduction_ns, 14_550.0)
        self.assertEqual(report.tail_jitter_reduction_ratio, 110.0)
        self.assertEqual(report.measurement_basis, BASIS_WIRE_TO_WIRE)
        self.assertIsInstance(report, FpgaEvaluationReport)

    def test_zero_fpga_tail_spread_reports_infinity_not_a_floored_ratio(self):
        """Regression: the previous implementation divided by max(1.0, spread),
        silently returning 5500.0 for a zero spread instead of flagging it."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(p50_latency_ns=250.0, p99_latency_ns=250.0),
            StrategyAlphaMetrics(15_000, None, 250_000.0),
            standard_costs(),
        )
        self.assertTrue(math.isinf(report.tail_jitter_reduction_ratio))
        self.assertIn(FLAG_ZERO_TAIL_SPREAD, report.data_quality_flags)

    def test_identical_profiles_give_unit_jitter_ratio(self):
        flat = dict(p50_latency_ns=250.0, p99_latency_ns=250.0, max_latency_ns=250.0)
        report = self.engine.evaluate_fpga_acceleration(
            fpga_profile(**flat),
            fpga_profile(**flat),
            StrategyAlphaMetrics(10, None, 0.0),
            standard_costs(),
        )
        self.assertEqual(report.tail_jitter_reduction_ratio, 1.0)
        self.assertEqual(report.median_latency_reduction_ns, 0.0)

    def test_fpga_slower_than_software_is_flagged_and_rejected(self):
        report = self.engine.evaluate_fpga_acceleration(
            fpga_profile(),
            cpu_profile(),
            StrategyAlphaMetrics(15_000, None, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.median_latency_reduction_ns, -2_250.0)
        self.assertIn(FLAG_NEGATIVE_LATENCY_DELTA, report.data_quality_flags)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)

    def test_sample_count_below_p99_resolution_is_flagged(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(sample_count=50),
            fpga_profile(sample_count=10_000),
            StrategyAlphaMetrics(15_000, None, 250_000.0),
            standard_costs(),
        )
        self.assertIn(FLAG_INSUFFICIENT_SAMPLES, report.data_quality_flags)

    def test_adequate_sample_counts_raise_no_flag(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(sample_count=1_000_000),
            fpga_profile(sample_count=1_000_000),
            StrategyAlphaMetrics(15_000, None, 250_000.0),
            standard_costs(),
        )
        self.assertNotIn(FLAG_INSUFFICIENT_SAMPLES, report.data_quality_flags)


class TestMeasurementBasisComparability(unittest.TestCase):
    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine()

    def test_mismatched_bases_block_the_recommendation(self):
        """Regression: comparing a wire-to-wire software number against a
        STAC-T0 actionable-latency FPGA number previously returned
        FPGA_RECOMMENDED off an arithmetic delta with no physical meaning."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_WIRE_TO_WIRE),
            fpga_profile(basis=BASIS_STAC_T0_ACTIONABLE),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_INSUFFICIENT)
        self.assertIn(FLAG_BASIS_MISMATCH, report.data_quality_flags)
        # The audit record must not file a mismatched comparison under one basis.
        self.assertEqual(
            report.measurement_basis,
            f"{BASIS_WIRE_TO_WIRE}/{BASIS_STAC_T0_ACTIONABLE}",
        )

    def test_vendor_component_figure_never_supports_a_purchase(self):
        """AMD publishes <3ns *transceiver* latency for the Alveo UL3524. That
        is a component figure, not a tick-to-trade path."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_WIRE_TO_WIRE),
            fpga_profile(
                p50_latency_ns=3.0,
                p99_latency_ns=3.0,
                max_latency_ns=3.0,
                std_dev_jitter_ns=0.2,
                basis=BASIS_VENDOR_COMPONENT,
            ),
            StrategyAlphaMetrics(15_000, 3_000.0, 5_000_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_INSUFFICIENT)
        self.assertIn(FLAG_COMPONENT_BASIS, report.data_quality_flags)

    def test_vendor_component_on_both_sides_still_blocks(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_VENDOR_COMPONENT),
            fpga_profile(basis=BASIS_VENDOR_COMPONENT),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_INSUFFICIENT)
        self.assertIn(FLAG_COMPONENT_BASIS, report.data_quality_flags)

    def test_matching_stac_t0_bases_are_comparable(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_STAC_T0_ACTIONABLE),
            fpga_profile(basis=BASIS_STAC_T0_ACTIONABLE),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)
        self.assertNotIn(FLAG_BASIS_MISMATCH, report.data_quality_flags)

    def test_application_internal_basis_is_warned_but_not_blocking(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_APPLICATION_INTERNAL),
            fpga_profile(basis=BASIS_APPLICATION_INTERNAL),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)
        self.assertIn(FLAG_APPLICATION_BASIS, report.data_quality_flags)


class TestCostOfOwnership(unittest.TestCase):
    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine()

    def test_capex_is_amortised_over_the_horizon_not_charged_annually(self):
        """Regression: capex $40,000 + recurring $20,000 was previously summed
        to a $60,000 "annual" TCO. Over a 3-year horizon the true figures are a
        $100,000 horizon TCO and a $33,333.33 annualised cost."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.evaluation_horizon_years, 3.0)
        self.assertEqual(report.total_cost_of_ownership_usd, 100_000.00)
        self.assertEqual(report.annualized_cost_of_ownership_usd, 33_333.33)
        self.assertEqual(report.net_annual_roi_usd, 216_666.67)
        self.assertEqual(report.net_horizon_roi_usd, 650_000.00)

    def test_amortisation_flips_a_marginal_build_that_the_old_model_rejected(self):
        """$45,000/yr gain loses against a $60,000 lump sum but wins against a
        $33,333.33 annualised cost; over 3 years it nets $35,000."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 45_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)
        self.assertEqual(report.net_annual_roi_usd, 11_666.67)
        self.assertEqual(report.net_horizon_roi_usd, 35_000.00)

    def test_payback_period_uses_capex_over_surplus_of_recurring_cost(self):
        """$40,000 capex / ($250,000 - $20,000) = 0.1739 years."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.payback_period_years, 0.1739)

    def test_no_payback_when_gain_never_covers_recurring_cost(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(200, None, 20_000.0),
            standard_costs(),
        )
        self.assertIsNone(report.payback_period_years)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)

    def test_annual_licence_subscription_is_treated_as_recurring(self):
        """Capex $40,000; recurring $20,000 + $12,000 = $32,000/yr.
        Horizon TCO = 40,000 + 96,000 = $136,000; annualised = $45,333.33."""
        costs = standard_costs(annual_ip_core_subscription_usd=12_000.0)
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            costs,
        )
        self.assertEqual(report.total_cost_of_ownership_usd, 136_000.00)
        self.assertEqual(report.annualized_cost_of_ownership_usd, 45_333.33)

    def test_one_time_hdl_engineering_is_treated_as_capex(self):
        """Capex $40,000 + $90,000 NRE = $130,000; annualised
        130,000/3 + 20,000 = $63,333.33."""
        costs = standard_costs(one_time_hdl_engineering_usd=90_000.0)
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 250_000.0),
            costs,
        )
        self.assertEqual(report.annualized_cost_of_ownership_usd, 63_333.33)
        self.assertEqual(report.total_cost_of_ownership_usd, 190_000.00)


class TestDecisionGates(unittest.TestCase):
    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine()

    def test_latency_gate_passes_at_exactly_the_threshold(self):
        """dL = 1,250 - 250 = 1,000ns, exactly the 1,000ns policy floor."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(p50_latency_ns=1_250.0),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 300.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.median_latency_reduction_ns, 1_000.0)
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)

    def test_latency_gate_fails_one_nanosecond_below_the_threshold(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(p50_latency_ns=1_249.0),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 300.0, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.median_latency_reduction_ns, 999.0)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)

    def test_financial_gate_is_strict_so_break_even_does_not_recommend(self):
        """Gain exactly equal to the $33,333.33 annualised cost nets zero, which
        does not 'exceed' the cost."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 33_333.33),
            standard_costs(),
        )
        self.assertEqual(report.net_annual_roi_usd, 0.0)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)

    def test_one_cent_above_break_even_recommends(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 33_333.34),
            standard_costs(),
        )
        self.assertEqual(report.net_annual_roi_usd, 0.01)
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)

    def test_explicit_margin_of_safety_is_honoured(self):
        engine = FpgaMarketDataEvaluationEngine(min_roi_net_benefit_usd=25_000.0)
        report = engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 3_000.0, 50_000.0),
            standard_costs(),
        )
        self.assertEqual(report.net_annual_roi_usd, 16_666.67)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)


class TestAlphaDecayConsistency(unittest.TestCase):
    def setUp(self):
        self.engine = FpgaMarketDataEvaluationEngine()

    def test_low_turnover_strategy_cannot_justify_the_build(self):
        """A 5-minute alpha half-life means 2,250ns improves edge retention by
        2 ** (2250 / 3e11) ~= 1.0000000052 -- far below 1% -- so a claimed
        $250,000 gain contradicts the caller's own decay assumption."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(200, FIVE_MINUTES_NS, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_INSUFFICIENT)
        self.assertIn(FLAG_ALPHA_DECAY_INCONSISTENT, report.data_quality_flags)
        self.assertAlmostEqual(report.alpha_capture_uplift_factor, 1.0, places=7)

    def test_uplift_factor_matches_the_half_life_definition(self):
        """A 2,250ns saving against a 2,250ns half-life doubles retained edge."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 2_250.0, 250_000.0),
            standard_costs(),
        )
        self.assertAlmostEqual(report.alpha_capture_uplift_factor, 2.0, places=12)
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)

    def test_consistency_check_can_be_disabled_but_still_flags(self):
        engine = FpgaMarketDataEvaluationEngine(enforce_alpha_decay_consistency=False)
        report = engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(200, FIVE_MINUTES_NS, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)
        self.assertIn(FLAG_ALPHA_DECAY_INCONSISTENT, report.data_quality_flags)

    def test_unknown_half_life_skips_the_check_rather_than_assuming_one(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, None, 250_000.0),
            standard_costs(),
        )
        self.assertIsNone(report.alpha_capture_uplift_factor)
        self.assertNotIn(FLAG_ALPHA_DECAY_INCONSISTENT, report.data_quality_flags)
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)

    def test_negligible_uplift_with_no_claimed_gain_is_not_a_contradiction(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(200, FIVE_MINUTES_NS, 0.0),
            standard_costs(),
        )
        self.assertNotIn(FLAG_ALPHA_DECAY_INCONSISTENT, report.data_quality_flags)
        self.assertEqual(report.recommendation, RECOMMENDATION_SOFTWARE)

    def test_an_absurdly_short_half_life_overflows_to_infinity_not_an_exception(self):
        """2 ** (2250 / 0.001) overflows a float; the guard must return inf."""
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(),
            fpga_profile(),
            StrategyAlphaMetrics(15_000, 0.001, 250_000.0),
            standard_costs(),
        )
        self.assertTrue(math.isinf(report.alpha_capture_uplift_factor))
        self.assertEqual(report.recommendation, RECOMMENDATION_FPGA)

    def test_a_basis_mismatch_outranks_the_alpha_check(self):
        report = self.engine.evaluate_fpga_acceleration(
            cpu_profile(basis=BASIS_WIRE_TO_WIRE),
            fpga_profile(basis=BASIS_STAC_T0_ACTIONABLE),
            StrategyAlphaMetrics(200, FIVE_MINUTES_NS, 250_000.0),
            standard_costs(),
        )
        self.assertEqual(report.recommendation, RECOMMENDATION_INSUFFICIENT)
        self.assertIn("not comparable", report.audit_notes)


class TestInputValidation(unittest.TestCase):
    def test_percentiles_must_be_monotonic(self):
        with self.assertRaises(ValueError):
            cpu_profile(p50_latency_ns=8_000.0, p99_latency_ns=2_500.0)
        with self.assertRaises(ValueError):
            cpu_profile(p99_latency_ns=20_000.0, max_latency_ns=15_000.0)

    def test_nan_and_infinite_latencies_are_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    cpu_profile(p50_latency_ns=bad)

    def test_negative_latency_is_rejected(self):
        with self.assertRaises(ValueError):
            cpu_profile(p50_latency_ns=-1.0)
        with self.assertRaises(ValueError):
            cpu_profile(std_dev_jitter_ns=-1.0)

    def test_unknown_measurement_basis_is_rejected_not_defaulted(self):
        with self.assertRaises(ValueError):
            cpu_profile(basis="WIRE-TO-WIRE")

    def test_sample_count_must_be_a_positive_integer(self):
        for bad in (0, -5, 2.5):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    cpu_profile(sample_count=bad)

    def test_costs_must_be_non_negative_and_finite(self):
        with self.assertRaises(ValueError):
            standard_costs(smartnic_hardware_usd=-1.0)
        with self.assertRaises(ValueError):
            standard_costs(annual_engineering_maintenance_usd=float("nan"))

    def test_alpha_half_life_must_be_positive_when_supplied(self):
        for bad in (0.0, -1.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    StrategyAlphaMetrics(100, bad, 1_000.0)

    def test_nan_alpha_gain_is_rejected_rather_than_silently_failing_the_gate(self):
        with self.assertRaises(ValueError):
            StrategyAlphaMetrics(100, 1_000.0, float("nan"))

    def test_daily_trade_frequency_must_be_a_non_negative_integer(self):
        for bad in (-1, 3.5):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    StrategyAlphaMetrics(bad, 1_000.0, 1_000.0)

    def test_engine_thresholds_are_validated(self):
        with self.assertRaises(ValueError):
            FpgaMarketDataEvaluationEngine(evaluation_horizon_years=0.0)
        with self.assertRaises(ValueError):
            FpgaMarketDataEvaluationEngine(min_latency_reduction_threshold_ns=-1.0)
        with self.assertRaises(ValueError):
            FpgaMarketDataEvaluationEngine(min_alpha_uplift_for_material_gain=0.0)

    def test_wrong_argument_types_raise_type_error(self):
        engine = FpgaMarketDataEvaluationEngine()
        with self.assertRaises(TypeError):
            engine.evaluate_fpga_acceleration(
                cpu_profile(),
                fpga_profile(),
                standard_costs(),
                StrategyAlphaMetrics(100, None, 1.0),
            )


if __name__ == "__main__":
    unittest.main()
