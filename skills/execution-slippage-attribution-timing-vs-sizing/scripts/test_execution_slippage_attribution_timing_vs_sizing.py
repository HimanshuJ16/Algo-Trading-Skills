"""
Unit tests for the execution slippage attribution engine.

Expected basis-point values are derived by hand from the decision price rather than
by re-running the engine's own expression, so a formula regression fails the test
instead of moving the expectation with it.
"""
import logging
import unittest

from execution_slippage_attribution_timing_vs_sizing import (
    DEFAULT_MATERIALITY_THRESHOLD_BPS,
    ExecutionSlippageAttributionEngine,
    SlippageAttributionAuditReport,
    TradeExecutionSummary,
    _assert_decomposition_identity,
)

# The engine logs one line per attributed trade; silence it so test output stays readable.
logging.getLogger("execution_slippage_attribution_timing_vs_sizing").addHandler(logging.NullHandler())
logging.getLogger("execution_slippage_attribution_timing_vs_sizing").propagate = False


def make_trade(**overrides) -> TradeExecutionSummary:
    """A valid, fully filled BUY with zero slippage; override only what a test needs."""
    defaults = dict(
        trade_id="TR_BASE",
        symbol="AAPL",
        side="BUY",
        order_qty=10_000,
        decision_price=100.00,
        arrival_price=100.00,
        average_exec_price=100.00,
        decision_time_iso="2026-07-30T10:00:00Z",
        arrival_time_iso="2026-07-30T10:02:00Z",
        completion_time_iso="2026-07-30T10:15:00Z",
    )
    defaults.update(overrides)
    return TradeExecutionSummary(**defaults)


class TestExecutionSlippageAttributionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionSlippageAttributionEngine()

    # ------------------------------------------------------------ core behaviour ---

    def test_timing_driven_buy_slippage(self):
        # BUY: Decision=$100.00, Arrival=$100.50, Exec=$100.70.
        # timing = 0.50/100.00 * 10000 = +50.0 bps
        # sizing = 0.20/100.00 * 10000 = +20.0 bps
        # total  = 0.70/100.00 * 10000 = +70.0 bps  -> TIMING DRIVEN
        trade = make_trade(
            trade_id="TR_BUY_01", side="BUY",
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.70,
        )
        report = self.engine.attribute_execution_slippage(trade)

        self.assertEqual(report.total_is_slippage_bps, 70.0)
        self.assertEqual(report.timing_delay_slippage_bps, 50.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 20.0)
        self.assertEqual(report.primary_slippage_driver, "TIMING_DRIVEN_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "ACCELERATE_ORDER_DISPATCH")
        # Contribution shares are normalised on gross cost 50 + 20 = 70.
        self.assertEqual(report.timing_contribution_pct, 71.4)
        self.assertEqual(report.sizing_contribution_pct, 28.6)

    def test_sizing_driven_sell_slippage(self):
        # SELL: Decision=$100.00, Arrival=$99.90, Exec=$99.20. Sell costs are sign-flipped.
        # timing = -(99.90 - 100.00)/100.00 * 10000 = +10.0 bps
        # sizing = -(99.20 -  99.90)/100.00 * 10000 = +70.0 bps
        # total  = -(99.20 - 100.00)/100.00 * 10000 = +80.0 bps  -> SIZING DRIVEN
        trade = make_trade(
            trade_id="TR_SELL_02", symbol="MSFT", side="SELL", order_qty=25_000,
            decision_price=100.00, arrival_price=99.90, average_exec_price=99.20,
        )
        report = self.engine.attribute_execution_slippage(trade)

        self.assertEqual(report.total_is_slippage_bps, 80.0)
        self.assertEqual(report.timing_delay_slippage_bps, 10.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 70.0)
        self.assertEqual(report.primary_slippage_driver, "SIZING_DRIVEN_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "REDUCE_PARTICIPATION_RATE_CEILING")
        self.assertTrue(report.secondary_driver_material)

    def test_independently_derived_non_round_case(self):
        # BUY on a $50 name: Decision=$50.00, Arrival=$50.10, Exec=$50.35.
        # timing = 0.10/50.00 * 10000 = +20.0 bps
        # sizing = 0.25/50.00 * 10000 = +50.0 bps
        # total  = 0.35/50.00 * 10000 = +70.0 bps
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=50.00, arrival_price=50.10, average_exec_price=50.35,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, 20.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 50.0)
        self.assertEqual(report.total_is_slippage_bps, 70.0)
        self.assertEqual(report.primary_slippage_driver, "SIZING_DRIVEN_SLIPPAGE")

    def test_sell_above_decision_price_is_a_gain_not_a_cost(self):
        # SELL filled ABOVE the decision price -> negative (favourable) cost.
        # timing = -(100.30 - 100.00)/100 * 10000 = -30.0 bps
        # sizing = -(100.50 - 100.30)/100 * 10000 = -20.0 bps
        report = self.engine.attribute_execution_slippage(make_trade(
            side="SELL", decision_price=100.00, arrival_price=100.30, average_exec_price=100.50,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, -30.0)
        self.assertEqual(report.sizing_impact_slippage_bps, -20.0)
        self.assertEqual(report.total_is_slippage_bps, -50.0)
        self.assertEqual(report.primary_slippage_driver, "FAVORABLE_EXECUTION")
        self.assertEqual(report.strategy_action_recommendation, "NO_ACTION_COST_FAVORABLE")

    def test_zero_slippage_is_preserved(self):
        report = self.engine.attribute_execution_slippage(make_trade())
        self.assertEqual(report.total_is_slippage_bps, 0.0)
        self.assertEqual(report.primary_slippage_driver, "ZERO_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "OPTIMAL")
        self.assertEqual(report.timing_contribution_pct, 0.0)
        self.assertEqual(report.sizing_contribution_pct, 0.0)

    def test_decomposition_identity_holds_across_cases(self):
        cases = [
            ("BUY", 100.00, 100.50, 100.70),
            ("SELL", 100.00, 99.90, 99.20),
            ("BUY", 33.33, 33.29, 33.41),
            ("SELL", 1234.56, 1240.01, 1231.77),
            ("BUY", 0.0725, 0.0731, 0.0728),
        ]
        for side, decision, arrival, execution in cases:
            with self.subTest(side=side, decision=decision):
                report = self.engine.attribute_execution_slippage(make_trade(
                    side=side, decision_price=decision,
                    arrival_price=arrival, average_exec_price=execution,
                ))
                # Exact in full precision; each of the three figures is then rounded
                # independently to 2 dp, so the reported sides can differ by up to
                # one 0.01 bps reporting ulp (theoretical bound 3 x 0.005).
                self.assertAlmostEqual(
                    report.total_is_slippage_bps,
                    report.timing_delay_slippage_bps + report.sizing_impact_slippage_bps,
                    delta=0.02,
                )

    def test_reported_total_is_computed_directly_not_summed_from_rounded_parts(self):
        # SELL 1234.56 -> 1240.01 -> 1231.77 rounds to timing=-44.15, sizing=+66.74,
        # whose sum is 22.59, while the directly computed total is 22.5991... -> 22.60.
        # The old implementation reported the sum, losing an ulp against the true total.
        report = self.engine.attribute_execution_slippage(make_trade(
            side="SELL", decision_price=1234.56,
            arrival_price=1240.01, average_exec_price=1231.77,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, -44.15)
        self.assertEqual(report.sizing_impact_slippage_bps, 66.74)
        self.assertEqual(report.total_is_slippage_bps, 22.60)

    def test_identity_guard_raises_on_violation(self):
        with self.assertRaises(ArithmeticError):
            _assert_decomposition_identity(70.0, 50.0, 5.0)

    # ------------------------------------------- regression: corrupt price inputs ---

    def test_non_finite_price_raises_instead_of_reporting_optimal(self):
        # Regression: a NaN price used to produce NaN bps, fail both abs() comparisons,
        # and be reported as ZERO_SLIPPAGE / OPTIMAL -- a clean bill of health on
        # corrupt data.
        for bad in (float("nan"), float("inf"), float("-inf")):
            for field in ("decision_price", "arrival_price", "average_exec_price"):
                with self.subTest(value=bad, field=field):
                    with self.assertRaises(ValueError):
                        self.engine.attribute_execution_slippage(make_trade(**{field: bad}))

    def test_non_positive_price_raises(self):
        for field in ("decision_price", "arrival_price", "average_exec_price"):
            for bad in (0.0, -100.0):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        self.engine.attribute_execution_slippage(make_trade(**{field: bad}))

    def test_non_numeric_price_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.engine.attribute_execution_slippage(make_trade(decision_price="100.00"))

    # -------------------------------------------------- regression: side handling ---

    def test_unrecognised_side_raises_instead_of_flipping_the_sign(self):
        # Regression: `+1 if side == 'BUY' else -1` treated any typo as a SELL, so a
        # +70 bps cost on a mistyped BUY was reported as a -70 bps gain.
        for bad_side in ("BUYY", "B", "LONG", "SHORT", "", "   "):
            with self.subTest(side=bad_side):
                with self.assertRaises(ValueError):
                    self.engine.attribute_execution_slippage(make_trade(
                        side=bad_side, arrival_price=100.50, average_exec_price=100.70,
                    ))

    def test_side_is_case_and_whitespace_insensitive(self):
        for side in ("buy", " BUY ", "Buy"):
            with self.subTest(side=side):
                report = self.engine.attribute_execution_slippage(make_trade(
                    side=side, arrival_price=100.50, average_exec_price=100.70,
                ))
                self.assertEqual(report.side, "BUY")
                self.assertEqual(report.total_is_slippage_bps, 70.0)

    # ------------------------------------------- regression: driver classification ---

    def test_tied_material_components_are_not_reported_as_zero_slippage(self):
        # Regression: timing == sizing failed both `>` comparisons and fell through to
        # ZERO_SLIPPAGE / OPTIMAL, describing 100 bps of real cost as "minimal".
        # timing = 0.50/100 * 10000 = +50.0, sizing = 0.50/100 * 10000 = +50.0
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=100.00, arrival_price=100.50, average_exec_price=101.00,
        ))
        self.assertEqual(report.total_is_slippage_bps, 100.0)
        self.assertEqual(report.timing_delay_slippage_bps, 50.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 50.0)
        self.assertEqual(report.primary_slippage_driver, "BOTH_DRIVERS_MATERIAL")
        self.assertEqual(report.strategy_action_recommendation, "REDUCE_DELAY_AND_PARTICIPATION")
        self.assertTrue(report.secondary_driver_material)

    def test_favourable_component_never_drives_the_recommendation(self):
        # Regression: ranking by abs() made a -50 bps timing GAIN the "primary slippage
        # driver" and recommended ACCELERATE_ORDER_DISPATCH -- advice that would have
        # forfeited the gain while ignoring the only leg that actually cost money.
        # timing = -0.50/100 * 10000 = -50.0 (gain), sizing = 0.20/100 * 10000 = +20.0
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=100.00, arrival_price=99.50, average_exec_price=99.70,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, -50.0)
        self.assertEqual(report.sizing_impact_slippage_bps, 20.0)
        self.assertEqual(report.total_is_slippage_bps, -30.0)
        self.assertEqual(report.primary_slippage_driver, "SIZING_DRIVEN_SLIPPAGE")
        self.assertEqual(report.strategy_action_recommendation, "REDUCE_PARTICIPATION_RATE_CEILING")
        self.assertFalse(report.secondary_driver_material)

    def test_secondary_driver_flag_is_false_when_only_one_leg_is_adverse(self):
        # timing = +50.0, sizing = -0.20/100 * 10000 = ... exec below arrival -> gain
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.30,
        ))
        self.assertEqual(report.primary_slippage_driver, "TIMING_DRIVEN_SLIPPAGE")
        self.assertFalse(report.secondary_driver_material)

    # ------------------------------------------- regression: contribution shares ----

    def test_offsetting_components_do_not_explode_contribution_shares(self):
        # Regression: dividing by abs(total) gave 50000% / -49900% when the two legs
        # nearly cancelled. Gross-cost normalisation keeps both inside [-100, 100].
        # timing = 5.00/100 * 10000 = +500.0, sizing = -4.99/100 * 10000 = -499.0
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=100.00, arrival_price=105.00, average_exec_price=100.01,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, 500.0)
        self.assertEqual(report.sizing_impact_slippage_bps, -499.0)
        self.assertEqual(report.total_is_slippage_bps, 1.0)
        for pct in (report.timing_contribution_pct, report.sizing_contribution_pct):
            self.assertGreaterEqual(pct, -100.0)
            self.assertLessEqual(pct, 100.0)
        self.assertAlmostEqual(
            abs(report.timing_contribution_pct) + abs(report.sizing_contribution_pct),
            100.0, delta=0.2,
        )
        # The 500 bps delay is a real cost; the sizing leg made money.
        self.assertEqual(report.primary_slippage_driver, "TIMING_DRIVEN_SLIPPAGE")

    # ------------------------------------------------ regression: fill accounting ---

    def test_partial_fill_scales_the_contribution_to_intended_notional(self):
        # Regression: order_qty was never read, so a 40%-filled order reported the
        # per-share cost as though it were the whole IS contribution (2.5x overstated).
        # per-share total = 0.70/100 * 10000 = +70.0 bps; contribution = 70.0 * 0.4 = 28.0
        report = self.engine.attribute_execution_slippage(make_trade(
            order_qty=10_000, filled_qty=4_000,
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.70,
        ))
        self.assertEqual(report.total_is_slippage_bps, 70.0)
        self.assertEqual(report.fill_ratio, 0.4)
        self.assertTrue(report.is_partial_fill)
        self.assertEqual(report.executed_is_contribution_bps, 28.0)
        self.assertIn("PARTIAL FILL", report.audit_notes)
        self.assertIn("opportunity cost", report.audit_notes)

    def test_full_fill_contribution_equals_per_share_cost(self):
        report = self.engine.attribute_execution_slippage(make_trade(
            order_qty=10_000, filled_qty=10_000,
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.70,
        ))
        self.assertEqual(report.fill_ratio, 1.0)
        self.assertFalse(report.is_partial_fill)
        self.assertEqual(report.executed_is_contribution_bps, report.total_is_slippage_bps)
        self.assertNotIn("PARTIAL FILL", report.audit_notes)

    def test_filled_qty_defaults_to_full_fill(self):
        report = self.engine.attribute_execution_slippage(make_trade(order_qty=7_500))
        self.assertEqual(report.fill_ratio, 1.0)
        self.assertFalse(report.is_partial_fill)

    def test_invalid_quantities_raise(self):
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(order_qty=0))
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(order_qty=-100))
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(order_qty=1_000, filled_qty=0))
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(order_qty=1_000, filled_qty=1_001))
        with self.assertRaises(TypeError):
            self.engine.attribute_execution_slippage(make_trade(order_qty=1_000.0))

    # --------------------------------------------------- regression: timestamps ----

    def test_durations_are_measured_from_the_timestamps(self):
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_time_iso="2026-07-30T10:00:00Z",
            arrival_time_iso="2026-07-30T10:02:00Z",
            completion_time_iso="2026-07-30T10:15:00Z",
        ))
        self.assertEqual(report.delay_seconds, 120.0)
        self.assertEqual(report.execution_duration_seconds, 780.0)

    def test_offsets_are_honoured_across_timezones(self):
        # 12:00:00+02:00 == 10:00:00Z, so the delay is 30 seconds, not 2 hours.
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_time_iso="2026-07-30T12:00:00+02:00",
            arrival_time_iso="2026-07-30T10:00:30Z",
            completion_time_iso="2026-07-30T10:05:30+00:00",
        ))
        self.assertEqual(report.delay_seconds, 30.0)
        self.assertEqual(report.execution_duration_seconds, 300.0)

    def test_naive_timestamp_raises(self):
        # A delay measured from naive timestamps is silently wrong across a DST
        # boundary or between venues in different zones.
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(
                make_trade(decision_time_iso="2026-07-30T10:00:00"))

    def test_malformed_timestamp_raises(self):
        for bad in ("not-a-timestamp", "", "2026-13-45T99:99:99Z"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.engine.attribute_execution_slippage(make_trade(arrival_time_iso=bad))

    def test_out_of_order_timestamps_raise(self):
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(
                decision_time_iso="2026-07-30T10:05:00Z",
                arrival_time_iso="2026-07-30T10:00:00Z",
            ))
        with self.assertRaises(ValueError):
            self.engine.attribute_execution_slippage(make_trade(
                arrival_time_iso="2026-07-30T10:10:00Z",
                completion_time_iso="2026-07-30T10:05:00Z",
            ))

    # ------------------------------------------------- materiality configuration ---

    def test_materiality_threshold_is_configurable(self):
        # timing = 0.05/100 * 10000 = +5.0 bps, sizing = 0.0
        trade = make_trade(
            decision_price=100.00, arrival_price=100.05, average_exec_price=100.05)

        default_report = self.engine.attribute_execution_slippage(trade)
        self.assertEqual(default_report.primary_slippage_driver, "TIMING_DRIVEN_SLIPPAGE")
        self.assertEqual(default_report.materiality_threshold_bps, DEFAULT_MATERIALITY_THRESHOLD_BPS)

        lenient = ExecutionSlippageAttributionEngine(materiality_threshold_bps=10.0)
        lenient_report = lenient.attribute_execution_slippage(trade)
        self.assertEqual(lenient_report.primary_slippage_driver, "ZERO_SLIPPAGE")
        self.assertEqual(lenient_report.strategy_action_recommendation, "OPTIMAL")
        self.assertEqual(lenient_report.materiality_threshold_bps, 10.0)

    def test_submaterial_legs_summing_to_a_material_total_are_not_called_minimal(self):
        # timing = 0.009/100 * 10000 = +0.9 bps, sizing = +0.9 bps, total = +1.8 bps.
        # Neither leg clears the 1.0 bps threshold, so there is no leg to action -- but
        # the line is logged at WARNING, so it must not read "minimal slippage".
        report = self.engine.attribute_execution_slippage(make_trade(
            decision_price=100.000, arrival_price=100.009, average_exec_price=100.018,
        ))
        self.assertEqual(report.timing_delay_slippage_bps, 0.9)
        self.assertEqual(report.sizing_impact_slippage_bps, 0.9)
        self.assertEqual(report.total_is_slippage_bps, 1.8)
        self.assertEqual(report.primary_slippage_driver, "ZERO_SLIPPAGE")
        self.assertNotIn("Minimal slippage", report.audit_notes)
        self.assertIn("no single leg to action", report.audit_notes)

    def test_invalid_materiality_threshold_raises(self):
        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    ExecutionSlippageAttributionEngine(materiality_threshold_bps=bad)
        with self.assertRaises(TypeError):
            ExecutionSlippageAttributionEngine(materiality_threshold_bps="1.0")

    # ------------------------------------------------------------ report contract ---

    def test_report_is_the_documented_type_and_echoes_validated_inputs(self):
        report = self.engine.attribute_execution_slippage(make_trade(
            trade_id="TR_X", symbol="TSLA",
            decision_price=100.00, arrival_price=100.50, average_exec_price=100.70,
        ))
        self.assertIsInstance(report, SlippageAttributionAuditReport)
        self.assertEqual(report.trade_id, "TR_X")
        self.assertEqual(report.symbol, "TSLA")
        self.assertEqual(report.decision_price, 100.00)
        self.assertEqual(report.arrival_price, 100.50)
        self.assertEqual(report.average_exec_price, 100.70)
        self.assertIn("TR_X", report.audit_notes)


if __name__ == '__main__':
    unittest.main()
