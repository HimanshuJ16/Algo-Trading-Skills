"""
Unit tests for execution-realistic-simulation.

Fee expectations are derived by hand from the published rate stack, not by re-running
the implementation's own arithmetic, so a wrong rate fails the test. Fill expectations
use inputs chosen to make the square-root law land on exact decimal values.
"""
import math
import unittest
import warnings

from fill_model import (
    DEFAULT_FEE_SCHEDULES,
    DEFAULT_IMPACT_GAMMA,
    FeeBreakdown,
    FeeSchedule,
    MarketType,
    RealisticExecutionSimulator,
    SimulatedFillResult,
    SQRT_LAW_MAX_VALIDATED_PARTICIPATION,
    estimate_fees,
    simulate_fill_price,
)


class TestDirectionalFillPricing(unittest.TestCase):
    """
    mid=100, half_spread=0.5, adv=1,000,000, sigma=0.02, gamma=0.5.

    Q/V = 10,000/1,000,000 = 0.01, sqrt = 0.1
    I(Q) = 0.5 * 0.02 * 0.1 * 100 = 0.10 exactly
    BUY  = 100 + 0.5 + 0.10 = 100.60
    SELL = 100 - 0.5 - 0.10 =  99.40
    """

    def setUp(self):
        self.sim = RealisticExecutionSimulator(impact_gamma=0.5)
        self.kwargs = dict(
            order_size=10_000.0, mid_price=100.0, half_spread=0.5,
            adv=1_000_000.0, volatility=0.02,
        )

    def test_buy_fills_above_the_ask_at_the_derived_price(self):
        res = self.sim.simulate_fill(side="BUY", **self.kwargs)
        self.assertAlmostEqual(res.fill_price, 100.60, places=10)
        self.assertAlmostEqual(res.market_impact_per_unit, 0.10, places=10)
        self.assertAlmostEqual(res.participation_ratio, 0.01, places=12)

    def test_sell_fills_below_the_bid_at_the_derived_price(self):
        res = self.sim.simulate_fill(side="SELL", **self.kwargs)
        self.assertAlmostEqual(res.fill_price, 99.40, places=10)

    def test_slippage_versus_mid_is_a_positive_cost_on_both_sides(self):
        buy = self.sim.simulate_fill(side="BUY", **self.kwargs)
        sell = self.sim.simulate_fill(side="SELL", **self.kwargs)
        # 0.60 per unit x 10,000 units on each side.
        self.assertAlmostEqual(buy.slippage_cost, 6_000.0, places=6)
        self.assertAlmostEqual(sell.slippage_cost, 6_000.0, places=6)

    def test_zero_spread_and_zero_impact_still_never_improves_on_mid(self):
        res = self.sim.simulate_fill(
            side="BUY", order_size=1.0, mid_price=100.0, half_spread=0.0,
            adv=1_000_000.0, volatility=0.0,
        )
        self.assertEqual(res.fill_price, 100.0)

    def test_side_is_case_and_whitespace_tolerant(self):
        res = self.sim.simulate_fill(side=" buy ", **self.kwargs)
        self.assertAlmostEqual(res.fill_price, 100.60, places=10)

    def test_unrecognised_side_is_rejected_not_silently_treated_as_a_sell(self):
        # Regression: an earlier model fell through to the SELL branch for any
        # string that was not exactly "BUY", so a typo produced a reversed trade.
        for bad in ("B", "buys", "long", "", "SEL"):
            with self.subTest(side=bad):
                with self.assertRaises(ValueError):
                    self.sim.simulate_fill(side=bad, **self.kwargs)
        with self.assertRaises(TypeError):
            self.sim.simulate_fill(side=None, **self.kwargs)


class TestSquareRootImpactScaling(unittest.TestCase):
    def setUp(self):
        self.sim = RealisticExecutionSimulator(impact_gamma=0.5)

    def test_quadrupling_size_exactly_doubles_impact(self):
        """
        The defining property of the square-root law, and the one a linear or flat
        slippage model fails: I(4Q) / I(Q) == 2, not 4 and not 1.
        """
        base = dict(mid_price=200.0, half_spread=0.25, adv=4_000_000.0, volatility=0.03)
        small = self.sim.simulate_fill(side="BUY", order_size=10_000.0, **base)
        large = self.sim.simulate_fill(side="BUY", order_size=40_000.0, **base)
        self.assertAlmostEqual(
            large.market_impact_per_unit / small.market_impact_per_unit, 2.0, places=10
        )

    def test_impact_matches_the_closed_form(self):
        res = self.sim.simulate_fill(
            side="BUY", order_size=25_000.0, mid_price=1_500.0, half_spread=0.75,
            adv=1_000_000.0, volatility=0.015,
        )
        expected = 0.5 * 0.015 * math.sqrt(0.025) * 1_500.0
        self.assertAlmostEqual(res.market_impact_per_unit, expected, places=10)

    def test_impact_scales_linearly_in_gamma_and_in_volatility(self):
        base = dict(side="BUY", order_size=1_000.0, mid_price=50.0, half_spread=0.05,
                    adv=100_000.0)
        a = RealisticExecutionSimulator(0.5).simulate_fill(volatility=0.02, **base)
        b = RealisticExecutionSimulator(1.0).simulate_fill(volatility=0.02, **base)
        c = RealisticExecutionSimulator(0.5).simulate_fill(volatility=0.04, **base)
        self.assertAlmostEqual(b.market_impact_per_unit,
                               2.0 * a.market_impact_per_unit, places=12)
        self.assertAlmostEqual(c.market_impact_per_unit,
                               2.0 * a.market_impact_per_unit, places=12)

    def test_extrapolation_beyond_the_calibrated_regime_is_logged(self):
        with self.assertLogs("fill_model", level="WARNING") as captured:
            self.sim.simulate_fill(
                side="BUY", order_size=50_000.0, mid_price=100.0, half_spread=0.5,
                adv=100_000.0, volatility=0.02,
            )
        self.assertIn("square-root", "\n".join(captured.output))
        self.assertGreater(0.5, SQRT_LAW_MAX_VALIDATED_PARTICIPATION)

    def test_impact_large_enough_to_wipe_out_the_price_raises(self):
        # Regression: an earlier model clamped such a sell to a hard-coded 0.01,
        # silently reporting a fill at a price the model never produced.
        with self.assertLogs("fill_model", level="WARNING"):  # keeps stderr clean
            with self.assertRaises(ValueError):
                self.sim.simulate_fill(
                    side="SELL", order_size=1_000_000.0, mid_price=10.0,
                    half_spread=0.5, adv=1_000.0, volatility=0.5,
                )

    def test_negative_gamma_rejected(self):
        with self.assertRaises(ValueError):
            RealisticExecutionSimulator(impact_gamma=-0.1)


class TestDepthLimitedPartialFills(unittest.TestCase):
    def setUp(self):
        self.sim = RealisticExecutionSimulator(impact_gamma=DEFAULT_IMPACT_GAMMA)

    def test_order_larger_than_depth_is_truncated(self):
        res = self.sim.simulate_fill(
            side="BUY", order_size=500.0, mid_price=100.0, half_spread=0.5,
            adv=50_000.0, market_depth_available=200.0,
        )
        self.assertTrue(res.is_partial_fill)
        self.assertEqual(res.filled_qty, 200.0)
        self.assertEqual(res.requested_qty, 500.0)

    def test_order_within_depth_fills_in_full(self):
        res = self.sim.simulate_fill(
            side="BUY", order_size=200.0, mid_price=100.0, half_spread=0.5,
            adv=50_000.0, market_depth_available=200.0,
        )
        self.assertFalse(res.is_partial_fill)
        self.assertEqual(res.filled_qty, 200.0)

    def test_impact_and_fees_are_charged_on_the_filled_quantity_only(self):
        capped = self.sim.simulate_fill(
            side="BUY", order_size=100_000.0, mid_price=100.0, half_spread=0.5,
            adv=1_000_000.0, volatility=0.02, market_depth_available=10_000.0,
        )
        uncapped = self.sim.simulate_fill(
            side="BUY", order_size=10_000.0, mid_price=100.0, half_spread=0.5,
            adv=1_000_000.0, volatility=0.02,
        )
        self.assertEqual(capped.fill_price, uncapped.fill_price)
        self.assertAlmostEqual(capped.slippage_cost, uncapped.slippage_cost, places=6)

    def test_zero_depth_fills_nothing_and_costs_nothing(self):
        # A flat per-order brokerage must not be charged on an order that never traded.
        res = self.sim.simulate_fill(
            side="BUY", order_size=500.0, mid_price=100.0, half_spread=0.5,
            adv=50_000.0, market_depth_available=0.0,
            market_type=MarketType.INDIAN_OPTIONS,
        )
        self.assertTrue(res.is_partial_fill)
        self.assertEqual(res.filled_qty, 0.0)
        self.assertEqual(res.slippage_cost, 0.0)
        self.assertEqual(res.fee_breakdown.total_fees, 0.0)


class TestFillInputValidation(unittest.TestCase):
    def setUp(self):
        self.sim = RealisticExecutionSimulator()
        self.ok = dict(side="BUY", order_size=100.0, mid_price=100.0, half_spread=0.5,
                       adv=50_000.0)

    def _with(self, **overrides):
        kwargs = dict(self.ok)
        kwargs.update(overrides)
        return kwargs

    def test_non_positive_size_price_and_adv_rejected(self):
        for override in (
            {"order_size": 0.0}, {"order_size": -1.0},
            {"mid_price": 0.0}, {"mid_price": -100.0},
            {"adv": 0.0}, {"adv": -1.0},
            {"half_spread": -0.1},
            {"volatility": -0.01},
            {"market_depth_available": -1.0},
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    self.sim.simulate_fill(**self._with(**override))

    def test_half_spread_wider_than_the_mid_is_rejected_as_a_corrupt_quote(self):
        with self.assertRaises(ValueError):
            self.sim.simulate_fill(**self._with(mid_price=1.0, half_spread=1.0))

    def test_nan_and_inf_never_propagate_into_a_fill(self):
        for override in (
            {"mid_price": float("nan")}, {"half_spread": float("inf")},
            {"adv": float("nan")}, {"volatility": float("nan")},
            {"order_size": float("inf")},
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    self.sim.simulate_fill(**self._with(**override))

    def test_zero_adv_is_rejected_rather_than_floored(self):
        # Regression: an earlier model substituted adv=1.0 for any adv <= 1,
        # turning an unknown volume into a 100%-participation impact estimate.
        with self.assertRaises(ValueError):
            self.sim.simulate_fill(**self._with(adv=0.0))


class TestIndianFeeStack(unittest.TestCase):
    """
    Every expected total below is computed by hand from the rate stack recorded on the
    corresponding FeeSchedule, so a stale or mistyped rate fails rather than passes.
    """

    def test_options_sell_on_one_lakh_of_premium(self):
        # brokerage 20 (flat) | STT 0.15% = 150.00 | NSE txn 0.03553% = 35.53
        # SEBI Rs 10/cr = 0.10 | stamp 0 (sell) | GST 18% x (20 + 35.53 + 0.10) = 10.0134
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="SELL"
        )
        self.assertAlmostEqual(fees.brokerage, 20.00, places=6)
        self.assertAlmostEqual(fees.stt, 150.00, places=6)
        self.assertAlmostEqual(fees.exchange_txn_fee, 35.53, places=6)
        self.assertAlmostEqual(fees.sebi_turnover_fee, 0.10, places=6)
        self.assertAlmostEqual(fees.stamp_duty, 0.0, places=10)
        self.assertAlmostEqual(fees.gst, 10.0134, places=6)
        self.assertAlmostEqual(fees.total_fees, 215.6434, places=6)

    def test_options_buy_pays_stamp_duty_and_no_stt(self):
        # brokerage 20 | STT 0 (buy) | txn 35.53 | SEBI 0.10 | stamp 0.003% = 3.00
        # GST 10.0134 -> 68.6434
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="BUY"
        )
        self.assertEqual(fees.stt, 0.0)
        self.assertAlmostEqual(fees.stamp_duty, 3.00, places=6)
        self.assertAlmostEqual(fees.total_fees, 68.6434, places=6)

    def test_options_stt_is_the_post_budget_2026_rate(self):
        # Regression: 0.0625% (pre-Oct-2024) understated options STT by 2.4x, which is
        # enough on its own to flip a high-turnover options backtest to profitable.
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=1_000_000.0, market_type=MarketType.INDIAN_OPTIONS, side="SELL"
        )
        self.assertAlmostEqual(fees.stt / 1_000_000.0, 0.0015, places=12)

    def test_equity_intraday_sell_on_one_lakh(self):
        # brokerage min(0.03% = 30, cap 20) = 20 | STT 0.025% = 25.00
        # txn 0.00307% = 3.07 | SEBI 0.10 | stamp 0 | GST 18% x 23.17 = 4.1706
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_EQUITY_INTRADAY, side="SELL"
        )
        self.assertAlmostEqual(fees.brokerage, 20.00, places=6)
        self.assertAlmostEqual(fees.stt, 25.00, places=6)
        self.assertAlmostEqual(fees.exchange_txn_fee, 3.07, places=6)
        self.assertAlmostEqual(fees.total_fees, 52.3406, places=6)

    def test_equity_intraday_brokerage_cap_binds_only_above_the_threshold(self):
        # 0.03% of 50,000 = 15 < 20, so the cap must NOT bind.
        # STT 0 (buy) | txn 1.535 | SEBI 0.05 | stamp 1.50 | GST 18% x 16.585 = 2.9853
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=50_000.0, market_type=MarketType.INDIAN_EQUITY_INTRADAY, side="BUY"
        )
        self.assertAlmostEqual(fees.brokerage, 15.00, places=6)
        self.assertAlmostEqual(fees.total_fees, 21.0703, places=6)

    def test_futures_sell_on_ten_lakh(self):
        # brokerage min(300, 20) = 20 | STT 0.05% = 500 | txn 0.00183% = 18.30
        # SEBI 1.00 | stamp 0 | GST 18% x 39.30 = 7.074
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=1_000_000.0, market_type=MarketType.INDIAN_FUTURES, side="SELL"
        )
        self.assertAlmostEqual(fees.stt, 500.00, places=6)
        self.assertAlmostEqual(fees.total_fees, 546.374, places=6)

    def test_delivery_charges_stt_on_both_sides(self):
        # Regression: INDIAN_EQUITY_DELIVERY and INDIAN_FUTURES previously fell through
        # to the crypto branch, returning a 0.1% commission and zero statutory charges.
        # brokerage 0 | STT 0.1% = 100 | txn 3.07 | SEBI 0.10 | stamp 0.015% = 15.00
        # GST 18% x 3.17 = 0.5706
        buy = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_EQUITY_DELIVERY, side="BUY"
        )
        sell = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_EQUITY_DELIVERY, side="SELL"
        )
        self.assertAlmostEqual(buy.stt, 100.00, places=6)
        self.assertAlmostEqual(sell.stt, 100.00, places=6)
        self.assertAlmostEqual(buy.stamp_duty, 15.00, places=6)
        self.assertEqual(sell.stamp_duty, 0.0)
        self.assertAlmostEqual(buy.total_fees, 118.7406, places=6)

    def test_gst_base_excludes_stt_and_stamp_duty(self):
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="BUY"
        )
        expected_base = fees.brokerage + fees.exchange_txn_fee + fees.sebi_turnover_fee
        self.assertAlmostEqual(fees.gst, expected_base * 0.18, places=10)

    def test_total_equals_the_sum_of_its_components(self):
        for market in MarketType:
            for side in ("BUY", "SELL"):
                with self.subTest(market=market, side=side):
                    f = RealisticExecutionSimulator.calculate_fees(
                        turnover=250_000.0, market_type=market, side=side
                    )
                    self.assertAlmostEqual(
                        f.total_fees,
                        f.brokerage + f.stt + f.exchange_txn_fee + f.sebi_turnover_fee
                        + f.stamp_duty + f.gst + f.other_regulatory_fees,
                        places=9,
                    )


class TestNonIndianFeeStacks(unittest.TestCase):
    def test_us_equity_charges_section_31_on_sells_only(self):
        # USD 20.60 per USD 1,000,000 of sales.
        sell = RealisticExecutionSimulator.calculate_fees(
            turnover=1_000_000.0, market_type=MarketType.US_EQUITY, side="SELL"
        )
        buy = RealisticExecutionSimulator.calculate_fees(
            turnover=1_000_000.0, market_type=MarketType.US_EQUITY, side="BUY"
        )
        self.assertAlmostEqual(sell.other_regulatory_fees, 20.60, places=6)
        self.assertAlmostEqual(sell.total_fees, 20.60, places=6)
        self.assertEqual(buy.other_regulatory_fees, 0.0)
        self.assertEqual(buy.total_fees, 0.0)
        self.assertEqual(sell.stt, 0.0)  # No Indian levies on a US schedule.

    def test_us_equity_does_not_borrow_the_crypto_commission(self):
        # Regression: US_EQUITY previously fell through to a 0.1% crypto branch.
        sell = RealisticExecutionSimulator.calculate_fees(
            turnover=1_000_000.0, market_type=MarketType.US_EQUITY, side="SELL"
        )
        self.assertEqual(sell.brokerage, 0.0)

    def test_crypto_spot_applies_its_placeholder_taker_fee_both_sides(self):
        for side in ("BUY", "SELL"):
            fees = RealisticExecutionSimulator.calculate_fees(
                turnover=10_000.0, market_type=MarketType.CRYPTO_SPOT, side=side
            )
            self.assertAlmostEqual(fees.brokerage, 10.00, places=6)
            self.assertAlmostEqual(fees.total_fees, 10.00, places=6)


class TestFeeScheduleContract(unittest.TestCase):
    def test_every_market_type_has_an_explicit_schedule(self):
        # No market may silently inherit another market's rates.
        for market in MarketType:
            with self.subTest(market=market):
                self.assertIn(market, DEFAULT_FEE_SCHEDULES)
                self.assertEqual(DEFAULT_FEE_SCHEDULES[market].market_type, market)

    def test_every_schedule_records_its_source(self):
        for market, schedule in DEFAULT_FEE_SCHEDULES.items():
            with self.subTest(market=market):
                self.assertTrue(schedule.source.strip())

    def test_indian_schedules_carry_an_effective_date(self):
        for market in (MarketType.INDIAN_OPTIONS, MarketType.INDIAN_FUTURES,
                       MarketType.INDIAN_EQUITY_INTRADAY,
                       MarketType.INDIAN_EQUITY_DELIVERY):
            with self.subTest(market=market):
                self.assertRegex(
                    DEFAULT_FEE_SCHEDULES[market].effective_from, r"^\d{4}-\d{2}-\d{2}$"
                )

    def test_caller_supplied_schedule_overrides_the_default(self):
        flat = FeeSchedule(
            market_type=MarketType.CRYPTO_SPOT,
            effective_from="2026-01-01",
            source="Test venue, 5 bps taker, no statutory levies.",
            brokerage_rate=0.0005,
        )
        fees = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="SELL",
            fee_schedule=flat,
        )
        self.assertAlmostEqual(fees.total_fees, 50.0, places=6)
        self.assertEqual(fees.stt, 0.0)
        self.assertEqual(fees.schedule_effective_from, "2026-01-01")

    def test_fee_inputs_are_validated(self):
        with self.assertRaises(ValueError):
            RealisticExecutionSimulator.calculate_fees(turnover=-1.0)
        with self.assertRaises(ValueError):
            RealisticExecutionSimulator.calculate_fees(turnover=float("nan"))
        with self.assertRaises(ValueError):
            RealisticExecutionSimulator.calculate_fees(turnover=100.0, side="HOLD")
        with self.assertRaises(ValueError):
            RealisticExecutionSimulator.calculate_fees(
                turnover=100.0, market_type="INDIAN_EQUITY"  # not a MarketType member
            )

    def test_equivalent_market_type_string_is_accepted(self):
        # MarketType is a str enum; accepting its value keeps config-driven callers working.
        by_enum = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="SELL"
        )
        by_str = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type="INDIAN_OPTIONS", side="SELL"
        )
        self.assertEqual(by_enum, by_str)

    def test_to_dict_reports_every_component_and_the_total(self):
        d = RealisticExecutionSimulator.calculate_fees(
            turnover=100_000.0, market_type=MarketType.INDIAN_OPTIONS, side="SELL"
        ).to_dict()
        self.assertEqual(d["stt"], 150.00)
        self.assertEqual(d["total_fees"], 215.64)
        self.assertIn("other_regulatory_fees", d)


class TestEndToEndFill(unittest.TestCase):
    def test_fill_result_fees_are_charged_on_executed_turnover(self):
        sim = RealisticExecutionSimulator(impact_gamma=0.5)
        res = sim.simulate_fill(
            side="SELL", order_size=10_000.0, mid_price=100.0, half_spread=0.5,
            adv=1_000_000.0, volatility=0.02, market_type=MarketType.INDIAN_OPTIONS,
        )
        # Fill 99.40 x 10,000 = 994,000 turnover -> STT 0.15% = 1491.00
        self.assertAlmostEqual(res.fee_breakdown.stt, 1_491.00, places=4)
        self.assertEqual(res.fee_breakdown.schedule_effective_from, "2026-04-01")

    def test_result_types(self):
        sim = RealisticExecutionSimulator()
        res = sim.simulate_fill(
            side="BUY", order_size=10.0, mid_price=100.0, half_spread=0.1, adv=1_000.0
        )
        self.assertIsInstance(res, SimulatedFillResult)
        self.assertIsInstance(res.fee_breakdown, FeeBreakdown)


class TestDeprecatedHelpers(unittest.TestCase):
    def test_simulate_fill_price_warns_and_matches_the_square_root_model(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            price = simulate_fill_price(100.0, 0.5, "buy", 10_000, 1_000_000,
                                        impact_coef=0.5, volatility=0.02)
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertAlmostEqual(price, 100.60, places=10)

    def test_simulate_fill_price_rejects_a_bad_side(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with self.assertRaises(ValueError):
                simulate_fill_price(100.0, 0.5, "b", 100, 10_000)

    def test_estimate_fees_defaults_match_the_options_sell_stack(self):
        # 20 + 150 + 35.53 + 0.10 + 0 + 0.18 x (20 + 35.53 + 0.10) = 215.6434
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            total = estimate_fees(100_000.0)
        self.assertTrue(any(issubclass(w.category, DeprecationWarning) for w in caught))
        self.assertAlmostEqual(total, 215.6434, places=6)

    def test_estimate_fees_rejects_negative_turnover(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            with self.assertRaises(ValueError):
                estimate_fees(-1.0)


if __name__ == "__main__":
    unittest.main()
