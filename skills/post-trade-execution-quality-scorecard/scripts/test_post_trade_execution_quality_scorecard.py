"""
Tests for post-trade-execution-quality-scorecard.

Expected values below are derived by hand from the published definitions (Rule 605
effective spread, Perold 1988 implementation shortfall), not by re-running the
engine's own arithmetic, so a formula regression fails the assertion rather than
moving the target with it.
"""
import unittest

from post_trade_execution_quality_scorecard import (
    BPS,
    Config,
    Engine,
    ExecutedOrderRecord,
    ExecutionQualityScorecardReport,
    PostTradeExecutionQualityScorecard,
    SingleOrderMetrics,
    VenueScorecard,
)


def _order(**overrides) -> ExecutedOrderRecord:
    """A fully-filled, at-the-midpoint BUY; individual fields overridden per test."""
    base = dict(
        order_id="ORD_1",
        venue="NASDAQ",
        symbol="AAPL",
        side="BUY",
        parent_qty=1000.0,
        executed_qty=1000.0,
        avg_fill_price=100.05,
        arrival_price=100.00,
        market_vwap=100.10,
        arrival_midquote=100.00,
        arrival_quoted_spread=0.10,
    )
    base.update(overrides)
    return ExecutedOrderRecord(**base)


class TestLegacyEngine(unittest.TestCase):
    def test_execute_reflects_enabled_flag(self):
        self.assertTrue(Engine(Config(enabled=True)).execute())
        self.assertFalse(Engine(Config(enabled=False)).execute())


class TestCoreMetrics(unittest.TestCase):
    def setUp(self):
        self.engine = PostTradeExecutionQualityScorecard(Config(enabled=True))

    def test_reference_buy_order_matches_hand_computed_metrics(self):
        # BUY 1,000 @ 100.05 vs 100.00 arrival, 100.10 VWAP, 100.00 mid, 0.10 quoted.
        #   arrival slippage = (100.05 - 100.00)/100.00 * 10000       = +5.00 bps
        #   VWAP slippage    = (100.05 - 100.10)/100.10 * 10000       = -4.995... -> -5.00
        #   effective spread = 2 * (100.05 - 100.00)                  =  0.10
        #   E/Q              = 0.10 / 0.10                            =  1.00
        #   fill rate        = 1000/1000                              =  100%
        #   score            = 100 - max(0, 5 - 10)*2 - max(0, 0)*20 - 0 = 100 -> 'A'
        report = self.engine.evaluate_scorecard([_order()])

        self.assertEqual(report.status, "SCORECARD_AUDIT_PASSED")
        self.assertEqual(report.composite_scorecard_rating, "A")
        self.assertEqual(report.avg_arrival_slippage_bps, 5.0)
        self.assertEqual(report.avg_vwap_slippage_bps, -5.0)
        self.assertEqual(report.order_metrics[0].effective_spread, 0.10)
        self.assertEqual(report.avg_eqr_ratio, 1.0)
        self.assertEqual(report.eqr_ratio_of_averages, 1.0)
        self.assertEqual(report.overall_fill_rate_pct, 100.0)
        self.assertTrue(report.order_metrics[0].is_fully_filled)

    def test_sell_side_sign_is_inverted_for_every_metric(self):
        # SELL 1,000 @ 99.95 vs 100.00 arrival / mid: selling BELOW the arrival price
        # is a cost, so all three cost metrics must come out POSITIVE.
        #   arrival slippage = -1 * (99.95 - 100.00)/100.00 * 10000 = +5.00 bps
        #   effective spread = 2 * -1 * (99.95 - 100.00)            =  0.10
        #   E/Q              = 0.10 / 0.10                          =  1.00
        report = self.engine.evaluate_scorecard(
            [_order(side="SELL", avg_fill_price=99.95, market_vwap=99.90)]
        )
        metrics = report.order_metrics[0]
        self.assertEqual(metrics.arrival_slippage_bps, 5.0)
        self.assertEqual(metrics.effective_spread, 0.10)
        self.assertEqual(metrics.eqr_ratio, 1.0)
        # Sold above VWAP -> beat the benchmark -> negative (a saving).
        self.assertEqual(metrics.vwap_slippage_bps, -5.01)

    def test_price_improvement_inside_the_quote_yields_negative_effective_spread(self):
        # BUY filled at the midpoint: effective spread 0, E/Q 0 -- a full spread saved.
        report = self.engine.evaluate_scorecard([_order(avg_fill_price=100.00)])
        metrics = report.order_metrics[0]
        self.assertEqual(metrics.effective_spread, 0.0)
        self.assertEqual(metrics.eqr_ratio, 0.0)
        self.assertEqual(metrics.arrival_slippage_bps, 0.0)

    def test_severe_slippage_grades_f(self):
        # BUY @ 105.00 vs 100.00 arrival = 500 bps, 50% filled.
        #   score = 100 - (500 - 10)*2 - max(0, 100 - 1)*20 - 50 -> clamped to 0 -> 'F'
        report = self.engine.evaluate_scorecard(
            [_order(order_id="ORD_BAD", venue="DARK", executed_qty=500.0,
                    avg_fill_price=105.00, market_vwap=100.00)]
        )
        self.assertEqual(report.status, "SCORECARD_AUDIT_FAILED")
        self.assertEqual(report.composite_scorecard_rating, "F")
        self.assertEqual(report.avg_arrival_slippage_bps, 500.0)
        self.assertEqual(report.overall_fill_rate_pct, 50.0)
        self.assertEqual(report.order_metrics[0].score_points, 0.0)


class TestImplementationShortfall(unittest.TestCase):
    """Perold (1988): IS = execution cost on filled shares + opportunity cost on the rest."""

    def setUp(self):
        self.engine = PostTradeExecutionQualityScorecard(Config(enabled=True))

    def test_is_is_none_without_end_price(self):
        # Without a mark for the unfilled residual the opportunity-cost term is
        # unknowable. Reporting the filled-share cost as "IS" would understate the
        # true shortfall on any partially filled order.
        report = self.engine.evaluate_scorecard([_order(executed_qty=500.0)])
        self.assertIsNone(report.order_metrics[0].implementation_shortfall_bps)
        self.assertIsNone(report.order_metrics[0].opportunity_cost_bps)
        self.assertIsNone(report.avg_implementation_shortfall_bps)
        self.assertFalse(report.implementation_shortfall_complete)
        self.assertEqual(report.orders_missing_end_price, 1)
        self.assertIn("lack end_price", report.audit_notes)
        # The filled-share cost is still reported, under its own honest name.
        self.assertEqual(report.order_metrics[0].arrival_slippage_bps, 5.0)

    def test_missed_shares_in_a_rising_market_cost_more_than_the_fills(self):
        # BUY 1,000, only 500 filled @ 100.05 vs 100.00 arrival; stock closes at 102.00.
        #   execution cost   = 5.00 bps * 0.5                          = +2.50 bps
        #   opportunity cost = (102.00 - 100.00)/100.00 * 10000 * 0.5  = +100.00 bps
        #   IS               = 2.50 + 100.00                           = +102.50 bps
        # The price-based statistics alone would have shown a 5 bps execution.
        report = self.engine.evaluate_scorecard(
            [_order(executed_qty=500.0, end_price=102.00)]
        )
        metrics = report.order_metrics[0]
        self.assertEqual(metrics.opportunity_cost_bps, 100.0)
        self.assertEqual(metrics.implementation_shortfall_bps, 102.50)
        self.assertEqual(report.avg_implementation_shortfall_bps, 102.50)
        self.assertTrue(report.implementation_shortfall_complete)

    def test_missed_shares_in_a_falling_market_are_a_saving_for_a_buyer(self):
        # Same order, stock closes at 98.00: not buying the residual SAVED money.
        #   opportunity cost = (98.00 - 100.00)/100.00 * 10000 * 0.5 = -100.00 bps
        #   IS               = 2.50 - 100.00                         = -97.50 bps
        report = self.engine.evaluate_scorecard(
            [_order(executed_qty=500.0, end_price=98.00)]
        )
        self.assertEqual(report.order_metrics[0].implementation_shortfall_bps, -97.50)

    def test_opportunity_cost_sign_flips_for_a_seller(self):
        # SELL 1,000, 500 filled @ 100.00 (= arrival, zero execution cost); close 98.00.
        # A seller who failed to sell into a falling market lost money:
        #   opportunity cost = -1 * (98.00 - 100.00)/100.00 * 10000 * 0.5 = +100.00 bps
        report = self.engine.evaluate_scorecard(
            [_order(side="SELL", executed_qty=500.0, avg_fill_price=100.00,
                    end_price=98.00)]
        )
        self.assertEqual(report.order_metrics[0].opportunity_cost_bps, 100.0)
        self.assertEqual(report.order_metrics[0].implementation_shortfall_bps, 100.0)

    def test_fully_filled_order_carries_no_opportunity_cost(self):
        report = self.engine.evaluate_scorecard([_order(end_price=120.00)])
        self.assertEqual(report.order_metrics[0].opportunity_cost_bps, 0.0)
        self.assertEqual(report.order_metrics[0].implementation_shortfall_bps, 5.0)


class TestAggregationWeighting(unittest.TestCase):
    """Regression tests for the unweighted-mean defect."""

    def setUp(self):
        self.engine = PostTradeExecutionQualityScorecard(Config(enabled=True))

    def test_aggregates_are_notional_weighted_not_order_weighted(self):
        # One tiny excellent fill and one large terrible fill.
        #   A: 1 share @ 100.00 (0 bps),      executed notional = 100
        #   B: 100,000 @ 101.00 (100 bps),    executed notional = 10,100,000
        # Unweighted mean would report (0 + 100)/2 = 50 bps -- halving a cost that was
        # in fact paid on essentially the whole programme.
        #   weighted = (0*100 + 100*10,100,000) / 10,100,100 = 99.999 bps
        orders = [
            _order(order_id="TINY", parent_qty=1.0, executed_qty=1.0,
                   avg_fill_price=100.00, market_vwap=100.00),
            _order(order_id="BIG", parent_qty=100_000.0, executed_qty=100_000.0,
                   avg_fill_price=101.00, market_vwap=101.00),
        ]
        report = self.engine.evaluate_scorecard(orders)
        self.assertEqual(report.unweighted_avg_arrival_slippage_bps, 50.0)
        self.assertAlmostEqual(report.avg_arrival_slippage_bps, 100.0, places=2)
        self.assertGreater(report.avg_arrival_slippage_bps, 99.0)

    def test_overall_fill_rate_is_quantity_weighted_not_a_mean_of_rates(self):
        # 100% of a 10-share order and 50% of a 10,000-share order.
        #   mean of rates      = (100 + 50)/2               = 75.0%  (wrong)
        #   sum(exec)/sum(qty) = (10 + 5000) / (10 + 10000) = 50.05% (right)
        orders = [
            _order(order_id="SMALL", parent_qty=10.0, executed_qty=10.0),
            _order(order_id="LARGE", parent_qty=10_000.0, executed_qty=5_000.0),
        ]
        report = self.engine.evaluate_scorecard(orders)
        self.assertAlmostEqual(report.overall_fill_rate_pct, 50.05, places=2)

    def test_eqr_ratio_of_averages_differs_from_mean_of_ratios(self):
        # Rule 605 publishes avg(effective)/avg(quoted), not avg(effective/quoted).
        #   A: 1,000 sh, eff = 2*(100.05-100.00) = 0.10, quoted 0.10 -> ratio 1.0
        #   B: 1,000 sh, eff = 2*(100.05-100.00) = 0.10, quoted 0.02 -> ratio 5.0
        #   ratio of share-weighted averages = 0.10 / 0.06 = 1.6667
        #   mean of per-order ratios         = 3.0
        orders = [
            _order(order_id="WIDE", arrival_quoted_spread=0.10),
            _order(order_id="TIGHT", arrival_quoted_spread=0.02),
        ]
        report = self.engine.evaluate_scorecard(orders)
        self.assertAlmostEqual(report.eqr_ratio_of_averages, 1.6667, places=3)
        self.assertAlmostEqual(report.avg_eqr_ratio, 3.0, places=6)


class TestUnfilledOrders(unittest.TestCase):
    def setUp(self):
        self.engine = PostTradeExecutionQualityScorecard(Config(enabled=True))

    def test_zero_fill_contributes_no_price_metric(self):
        # A wholly unfilled order carries a placeholder avg_fill_price. Feeding that
        # into the slippage formula previously produced a huge fictional saving that
        # dragged the aggregate down; it must be excluded instead.
        orders = [
            _order(order_id="FILLED"),
            _order(order_id="UNFILLED", executed_qty=0.0, avg_fill_price=0.01),
        ]
        report = self.engine.evaluate_scorecard(orders)
        unfilled = report.order_metrics[1]
        self.assertIsNone(unfilled.arrival_slippage_bps)
        self.assertIsNone(unfilled.vwap_slippage_bps)
        self.assertIsNone(unfilled.effective_spread)
        self.assertIsNone(unfilled.eqr_ratio)
        self.assertEqual(unfilled.fill_rate_pct, 0.0)
        self.assertEqual(report.unfilled_orders, 1)
        # The one real fill is the only thing the aggregate reflects.
        self.assertEqual(report.avg_arrival_slippage_bps, 5.0)
        # ...but it is fully penalised through the fill term.
        self.assertEqual(unfilled.score_points, 0.0)
        self.assertEqual(report.overall_fill_rate_pct, 50.0)

    def test_zero_fill_price_is_not_validated_when_nothing_executed(self):
        # avg_fill_price is meaningless for a zero-fill order, so a 0.0 placeholder
        # must be accepted rather than tripping the positive-price check.
        report = self.engine.evaluate_scorecard(
            [_order(executed_qty=0.0, avg_fill_price=0.0)]
        )
        self.assertEqual(report.overall_fill_rate_pct, 0.0)


class TestFractionalQuantities(unittest.TestCase):
    def test_sub_unit_parent_quantity_reports_a_true_fill_rate(self):
        # Regression: the fill rate denominator was max(1.0, parent_qty), so a fully
        # filled 0.5-unit crypto/fractional-share order reported 50%.
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard(
            [_order(symbol="BTC-USD", parent_qty=0.5, executed_qty=0.5)]
        )
        self.assertEqual(report.overall_fill_rate_pct, 100.0)
        self.assertEqual(report.order_metrics[0].fill_rate_pct, 100.0)
        self.assertTrue(report.order_metrics[0].is_fully_filled)

    def test_partially_filled_sub_unit_quantity(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard(
            [_order(parent_qty=0.5, executed_qty=0.25)]
        )
        self.assertEqual(report.order_metrics[0].fill_rate_pct, 50.0)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = PostTradeExecutionQualityScorecard(Config(enabled=True))

    def _assert_rejects(self, exc, **overrides):
        with self.assertRaises(exc):
            self.engine.evaluate_scorecard([_order(**overrides)])

    def test_unknown_side_raises_instead_of_defaulting_to_sell(self):
        # Regression: any side other than 'BUY' silently scored as a SELL, inverting
        # the sign of every cost metric on the order.
        self._assert_rejects(ValueError, side="B")
        self._assert_rejects(ValueError, side="")
        self._assert_rejects(ValueError, side="SHORT")

    def test_side_is_case_and_whitespace_insensitive(self):
        report = self.engine.evaluate_scorecard([_order(side="  buy ")])
        self.assertEqual(report.order_metrics[0].arrival_slippage_bps, 5.0)

    def test_non_positive_prices_raise(self):
        self._assert_rejects(ValueError, arrival_price=0.0)
        self._assert_rejects(ValueError, arrival_price=-100.0)
        self._assert_rejects(ValueError, market_vwap=0.0)
        self._assert_rejects(ValueError, arrival_midquote=0.0)
        self._assert_rejects(ValueError, avg_fill_price=0.0)
        self._assert_rejects(ValueError, end_price=0.0)

    def test_non_finite_values_raise(self):
        self._assert_rejects(ValueError, arrival_price=float("nan"))
        self._assert_rejects(ValueError, avg_fill_price=float("inf"))
        self._assert_rejects(ValueError, executed_qty=float("nan"))
        self._assert_rejects(ValueError, arrival_quoted_spread=float("inf"))

    def test_non_positive_quoted_spread_raises(self):
        # A locked (0) or crossed (<0) book has no meaningful E/Q denominator.
        self._assert_rejects(ValueError, arrival_quoted_spread=0.0)
        self._assert_rejects(ValueError, arrival_quoted_spread=-0.01)

    def test_invalid_quantities_raise(self):
        self._assert_rejects(ValueError, parent_qty=0.0)
        self._assert_rejects(ValueError, parent_qty=-100.0)
        self._assert_rejects(ValueError, executed_qty=-1.0)

    def test_overfill_raises_as_a_reconciliation_break(self):
        self._assert_rejects(ValueError, parent_qty=1000.0, executed_qty=1100.0)

    def test_missing_order_id_raises(self):
        self._assert_rejects(ValueError, order_id="")

    def test_non_numeric_price_raises_typeerror(self):
        self._assert_rejects(TypeError, arrival_price="100.00")

    def test_validation_precedes_any_aggregation(self):
        # A bad record anywhere in the batch must abort the whole run: a scorecard
        # computed from a partially validated batch is worse than no scorecard.
        orders = [_order(order_id="GOOD"), _order(order_id="BAD", parent_qty=0.0)]
        with self.assertRaises(ValueError):
            self.engine.evaluate_scorecard(orders)


class TestVenueRollup(unittest.TestCase):
    def test_venues_are_graded_separately_and_sorted_worst_first(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        orders = [
            _order(order_id="G1", venue="GOOD_VENUE"),
            _order(order_id="B1", venue="BAD_VENUE", avg_fill_price=105.00,
                   market_vwap=100.00),
        ]
        report = engine.evaluate_scorecard(orders)
        self.assertEqual(len(report.venue_scorecards), 2)
        self.assertEqual(report.venue_scorecards[0].venue, "BAD_VENUE")
        self.assertEqual(report.venue_scorecards[0].rating, "F")
        self.assertEqual(report.venue_scorecards[1].venue, "GOOD_VENUE")
        self.assertEqual(report.venue_scorecards[1].rating, "A")
        self.assertEqual(report.venue_scorecards[1].avg_arrival_slippage_bps, 5.0)

    def test_venue_below_notional_floor_is_reported_but_not_graded(self):
        engine = PostTradeExecutionQualityScorecard(
            Config(enabled=True, min_venue_notional_for_grade=1_000_000.0)
        )
        report = engine.evaluate_scorecard(
            [_order(venue="THIN", parent_qty=1.0, executed_qty=1.0)]
        )
        self.assertEqual(report.venue_scorecards[0].rating, "NR")

    def test_venue_fill_rate_uses_quantities_not_notional(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard(
            [_order(venue="V", parent_qty=1000.0, executed_qty=250.0)]
        )
        self.assertEqual(report.venue_scorecards[0].fill_rate_pct, 25.0)


class TestScoringAndConfig(unittest.TestCase):
    def test_benchmark_target_is_bps_is_actually_applied(self):
        # Regression: the field was documented as a prerequisite but never read.
        # 20 bps arrival slippage, filled exactly at the midquote so the E/Q term is
        # zero and the IS target is the only thing moving the score.
        order = _order(avg_fill_price=100.20, arrival_midquote=100.20)
        # target 10 bps -> penalty (20 - 10) * 2 = 20 -> score 80 -> 'B'
        lenient = PostTradeExecutionQualityScorecard(
            Config(enabled=True, benchmark_target_is_bps=10.0)
        )
        self.assertEqual(lenient.evaluate_scorecard([order]).order_metrics[0].score_points, 80.0)
        # target 0 bps  -> penalty 20 * 2 = 40 -> score 60 -> 'D'
        strict = PostTradeExecutionQualityScorecard(
            Config(enabled=True, benchmark_target_is_bps=0.0)
        )
        self.assertEqual(strict.evaluate_scorecard([order]).order_metrics[0].score_points, 60.0)

    def test_penalty_weights_are_configurable(self):
        order = _order(avg_fill_price=100.20, arrival_midquote=100.20)
        engine = PostTradeExecutionQualityScorecard(
            Config(enabled=True, benchmark_target_is_bps=10.0, is_penalty_per_bps=5.0)
        )
        # penalty (20 - 10) * 5 = 50 -> score 50 -> 'F'
        report = engine.evaluate_scorecard([order])
        self.assertEqual(report.order_metrics[0].score_points, 50.0)
        self.assertEqual(report.composite_scorecard_rating, "F")

    def test_grade_boundaries_are_inclusive_at_the_threshold(self):
        # Exactly 90.0 must be an 'A', exactly 70.0 a 'C' and a PASS.
        engine = PostTradeExecutionQualityScorecard(
            Config(enabled=True, fill_penalty_per_pct=1.0)
        )
        at_90 = engine.evaluate_scorecard([_order(executed_qty=900.0)])
        self.assertEqual(at_90.order_metrics[0].score_points, 90.0)
        self.assertEqual(at_90.composite_scorecard_rating, "A")

        at_70 = engine.evaluate_scorecard([_order(executed_qty=700.0)])
        self.assertEqual(at_70.order_metrics[0].score_points, 70.0)
        self.assertEqual(at_70.composite_scorecard_rating, "C")
        self.assertEqual(at_70.status, "SCORECARD_AUDIT_PASSED")

        just_under = engine.evaluate_scorecard([_order(executed_qty=699.0)])
        self.assertEqual(just_under.composite_scorecard_rating, "D")
        self.assertEqual(just_under.status, "SCORECARD_AUDIT_FAILED")

    def test_score_is_clamped_to_the_zero_hundred_range(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        # Massive price improvement must not score above 100.
        great = engine.evaluate_scorecard([_order(avg_fill_price=90.00)])
        self.assertEqual(great.order_metrics[0].score_points, 100.0)
        # Catastrophic slippage must not score below 0.
        awful = engine.evaluate_scorecard([_order(avg_fill_price=200.00)])
        self.assertEqual(awful.order_metrics[0].score_points, 0.0)

    def test_eqr_penalty_saturates_a_worked_parent_order(self):
        # Documented limitation, pinned so it cannot regress silently. A parent order
        # worked through a 2c-spread name fills 12c through the arrival midquote:
        #   effective spread = 2 * 0.12 = 0.24, E/Q = 0.24 / 0.02 = 12.0
        #   penalty = (12 - 1) * 20 = 220 -> score clamped to 0, despite a 100% fill
        #   and only 6.3 bps of arrival slippage.
        worked = _order(parent_qty=50_000.0, executed_qty=50_000.0, avg_fill_price=190.12,
                        arrival_price=190.00, market_vwap=190.20, arrival_midquote=190.00,
                        arrival_quoted_spread=0.02)
        default = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = default.evaluate_scorecard([worked])
        self.assertAlmostEqual(report.order_metrics[0].eqr_ratio, 12.0, places=6)
        self.assertEqual(report.order_metrics[0].score_points, 0.0)

        # Zeroing the E/Q weight restores discrimination: the order is then graded on
        # its slippage and fill rate alone.
        #   6.32 bps slippage, target 10 -> no penalty; 100% filled -> score 100 -> 'A'
        recalibrated = PostTradeExecutionQualityScorecard(
            Config(enabled=True, eqr_penalty_per_unit=0.0)
        )
        rescored = recalibrated.evaluate_scorecard([worked])
        self.assertEqual(rescored.order_metrics[0].score_points, 100.0)
        self.assertEqual(rescored.composite_scorecard_rating, "A")

    def test_composite_score_is_notional_weighted(self):
        # A perfect 1-share order must not rescue a terrible 100,000-share order.
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        orders = [
            _order(order_id="TINY", parent_qty=1.0, executed_qty=1.0),
            _order(order_id="BIG", parent_qty=100_000.0, executed_qty=100_000.0,
                   avg_fill_price=105.00, market_vwap=105.00),
        ]
        report = engine.evaluate_scorecard(orders)
        self.assertEqual(report.composite_scorecard_rating, "F")


class TestDegenerateInputs(unittest.TestCase):
    def test_disabled_engine_returns_na(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=False))
        report = engine.evaluate_scorecard([_order()])
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertEqual(report.composite_scorecard_rating, "N/A")
        self.assertEqual(report.order_metrics, [])
        self.assertIsNone(report.avg_implementation_shortfall_bps)

    def test_empty_batch_returns_na_without_dividing_by_zero(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard([])
        self.assertEqual(report.status, "NO_ORDERS_AUDITED")
        self.assertEqual(report.composite_scorecard_rating, "N/A")
        self.assertEqual(report.total_orders_audited, 0)
        self.assertEqual(report.overall_fill_rate_pct, 0.0)

    def test_all_orders_unfilled_yields_no_price_aggregates(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard(
            [_order(order_id="U1", executed_qty=0.0),
             _order(order_id="U2", executed_qty=0.0)]
        )
        self.assertIsNone(report.avg_arrival_slippage_bps)
        self.assertIsNone(report.avg_eqr_ratio)
        self.assertIsNone(report.eqr_ratio_of_averages)
        self.assertEqual(report.overall_fill_rate_pct, 0.0)
        self.assertEqual(report.composite_scorecard_rating, "F")
        self.assertIn("wholly unfilled", report.audit_notes)


class TestReportShape(unittest.TestCase):
    def test_report_is_serialisable_and_carries_provenance_counters(self):
        engine = PostTradeExecutionQualityScorecard(Config(enabled=True))
        report = engine.evaluate_scorecard([_order(end_price=100.5)])
        self.assertIsInstance(report, ExecutionQualityScorecardReport)
        self.assertIsInstance(report.order_metrics[0], SingleOrderMetrics)
        self.assertIsInstance(report.venue_scorecards[0], VenueScorecard)
        self.assertEqual(report.total_parent_notional, 100_000.0)
        self.assertEqual(report.total_executed_notional, 100_050.0)
        self.assertEqual(report.orders_missing_end_price, 0)
        self.assertEqual(BPS, 10_000.0)


if __name__ == "__main__":
    unittest.main()
