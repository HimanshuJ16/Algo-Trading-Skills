"""
Unit tests for options-margin-span-calculation-global.

Expected values are derived independently of the implementation: Black-Scholes
against a textbook reference and put-call parity, scenario payoffs against
hand-computed intrinsic values, and worst-case losses against strike-width
arithmetic. Several tests are explicit regressions against defects in the
pre-2.0 implementation and are marked as such.
"""
import logging
import math
import unittest

from span_approx import (
    OptionLeg,
    OptionType,
    SPANMarginCalculator,
    build_price_vol_scenarios,
    span_style_margin_estimate,
)

# 30 calendar days, the tenor used across the fixtures below.
T30 = 30.0 / 365.0


def _iron_condor(ttm=T30):
    """Short 95/105 body, long 90/110 wings, 5-point wide, 100 multiplier."""
    return [
        OptionLeg(90.0, OptionType.PUT, 1.0, 1.0, time_to_expiry_years=ttm),
        OptionLeg(95.0, OptionType.PUT, -1.0, 2.5, time_to_expiry_years=ttm),
        OptionLeg(105.0, OptionType.CALL, -1.0, 2.5, time_to_expiry_years=ttm),
        OptionLeg(110.0, OptionType.CALL, 1.0, 1.0, time_to_expiry_years=ttm),
    ]


class TestBlackScholesPricing(unittest.TestCase):
    """The scan is only as good as the revaluation underneath it."""

    def test_matches_textbook_reference_value(self):
        # S=K=100, r=0, sigma=0.20, T=1 gives 100*(N(0.1)-N(-0.1)) = 7.9656,
        # a standard reference value, derived here without reference to the
        # implementation.
        leg = OptionLeg(100.0, OptionType.CALL, 1.0, time_to_expiry_years=1.0)
        price = SPANMarginCalculator._black_scholes(leg, 100.0, 0.20, 1.0)
        self.assertAlmostEqual(price, 7.9656, places=4)

    def test_put_call_parity(self):
        # C - P = S - K*exp(-rT) must hold for any S, K, sigma, T, r.
        call = OptionLeg(90.0, OptionType.CALL, 1.0, time_to_expiry_years=0.75,
                         rate=0.04)
        put = OptionLeg(90.0, OptionType.PUT, 1.0, time_to_expiry_years=0.75,
                        rate=0.04)
        c = SPANMarginCalculator._black_scholes(call, 100.0, 0.30, 0.75)
        p = SPANMarginCalculator._black_scholes(put, 100.0, 0.30, 0.75)
        self.assertAlmostEqual(c - p, 100.0 - 90.0 * math.exp(-0.04 * 0.75),
                               places=9)

    def test_zero_time_to_expiry_falls_back_to_intrinsic(self):
        leg = OptionLeg(95.0, OptionType.CALL, 1.0, time_to_expiry_years=0.0)
        self.assertEqual(
            SPANMarginCalculator._black_scholes(leg, 100.0, 0.20, 0.0), 5.0)


class TestScenarioGrid(unittest.TestCase):

    def setUp(self):
        self.calc = SPANMarginCalculator()

    def test_standard_grid_has_sixteen_span_scenarios(self):
        # 7 price fractions x 2 volatility directions + 2 extreme moves.
        # REGRESSION: the pre-2.0 grid was 7 x 2 = 14 while the docs claimed 16.
        payoffs = self.calc.evaluate_scenario_grid(
            _iron_condor(), spot=100.0, vol=0.20)
        self.assertEqual(len(payoffs), 16)
        self.assertIn("p+3.00PSR_extreme", payoffs)
        self.assertIn("p-3.00PSR_extreme", payoffs)
        self.assertIn("p+1.00PSR_volup", payoffs)
        self.assertIn("p+0.00PSR_voldn", payoffs)

    def test_full_scan_range_scenario_matches_hand_computed_payoff(self):
        # Long 1 call K=100, multiplier 100, valued at intrinsic (no expiry).
        # Base intrinsic at spot 100 is 0. At +1.00 PSR the spot is 106, so the
        # position is worth (106-100)*100 = 600 and the payoff is +600.
        leg = [OptionLeg(100.0, OptionType.CALL, 1.0)]
        payoffs = self.calc.evaluate_scenario_grid(leg, spot=100.0, vol=0.20)
        self.assertAlmostEqual(payoffs["p+1.00PSR_volup"], 600.0, places=6)

    def test_extreme_scenario_carries_the_cover_fraction(self):
        # At +3.00 PSR the spot is 118, the position is worth 1800, and the
        # default cover fraction of 0.30 makes the stored value 540.
        leg = [OptionLeg(100.0, OptionType.CALL, 1.0)]
        payoffs = self.calc.evaluate_scenario_grid(leg, spot=100.0, vol=0.20)
        self.assertAlmostEqual(payoffs["p+3.00PSR_extreme"], 540.0, places=6)

    def test_custom_shock_lists_replace_the_grid_without_extremes(self):
        calc = SPANMarginCalculator(
            price_shocks_pct=[-0.05, 0.05], vol_shocks_pct=[-0.10, 0.10])
        payoffs = calc.evaluate_scenario_grid(
            [OptionLeg(100.0, OptionType.CALL, 1.0)], spot=100.0, vol=0.20)
        self.assertEqual(len(payoffs), 4)
        self.assertFalse(any("extreme" in name for name in payoffs))

    def test_volatility_shock_changes_the_payoff_when_time_value_exists(self):
        # REGRESSION: the pre-2.0 payoff function accepted a scenario vol and
        # ignored it, so every vol-up scenario equalled its vol-down twin.
        payoffs = self.calc.evaluate_scenario_grid(
            [OptionLeg(100.0, OptionType.CALL, 1.0, time_to_expiry_years=T30)],
            spot=100.0, vol=0.20)
        self.assertNotAlmostEqual(
            payoffs["p+0.00PSR_volup"], payoffs["p+0.00PSR_voldn"], places=6)


class TestWorstCaseTerminalLoss(unittest.TestCase):

    def test_bull_put_spread_max_loss_is_width_less_credit(self):
        # Long 90 put / short 95 put at marks 1.00 and 2.50: a 150 net credit
        # on a 5-point width, so the greatest loss from the current mark is
        # 500 - 150 = 350. Derived from strike arithmetic, not from the model.
        legs = [
            OptionLeg(90.0, OptionType.PUT, 1.0, mark_price=1.0),
            OptionLeg(95.0, OptionType.PUT, -1.0, mark_price=2.5),
        ]
        loss, unbounded = SPANMarginCalculator._worst_case_terminal_loss(
            legs, net_option_value=-150.0)
        self.assertFalse(unbounded)
        self.assertAlmostEqual(loss, 350.0, places=6)

    def test_naked_short_call_is_unbounded(self):
        legs = [OptionLeg(105.0, OptionType.CALL, -1.0)]
        loss, unbounded = SPANMarginCalculator._worst_case_terminal_loss(
            legs, net_option_value=-200.0)
        self.assertTrue(unbounded)
        self.assertEqual(loss, math.inf)

    def test_ratio_call_spread_with_net_short_calls_is_unbounded(self):
        # Long 1 x 100 call against short 2 x 105 calls: upside slope is
        # negative, so the loss runs away even though a long leg is present.
        legs = [
            OptionLeg(100.0, OptionType.CALL, 1.0),
            OptionLeg(105.0, OptionType.CALL, -2.0),
        ]
        _, unbounded = SPANMarginCalculator._worst_case_terminal_loss(legs, 0.0)
        self.assertTrue(unbounded)

    def test_naked_short_put_loss_is_bounded_by_the_strike(self):
        # A short 95 put collected at 200 loses at most 95*100 - 200 = 9300.
        legs = [OptionLeg(95.0, OptionType.PUT, -1.0, mark_price=2.0)]
        loss, unbounded = SPANMarginCalculator._worst_case_terminal_loss(
            legs, net_option_value=-200.0)
        self.assertFalse(unbounded)
        self.assertAlmostEqual(loss, 9300.0, places=6)


class TestMarginCalculation(unittest.TestCase):

    def setUp(self):
        self.calc = SPANMarginCalculator()
        # The calculator logs a warning for intrinsic-valued legs and for a
        # zero short option minimum; both are expected in these fixtures.
        logging.disable(logging.WARNING)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_long_only_position_requires_no_margin(self):
        # REGRESSION: the pre-2.0 code charged the full premium (500) as margin
        # on a long call. A long option is paid for in full and cannot be
        # margined -- its worst case is losing the premium already paid.
        res = self.calc.calculate_span_margin(
            [OptionLeg(100.0, OptionType.CALL, 1.0, 5.0,
                       time_to_expiry_years=T30)],
            spot=100.0, vol=0.20)
        self.assertEqual(res.total_required_margin, 0.0)
        self.assertEqual(res.exposure_margin, 0.0)

    def test_iron_condor_requires_positive_margin_capped_at_max_loss(self):
        # REGRESSION: the pre-2.0 code returned 0.0 for a short iron condor,
        # implying a credit spread could be opened with no capital at all.
        res = self.calc.calculate_span_margin(
            _iron_condor(), spot=100.0, vol=0.20)
        self.assertGreater(res.total_required_margin, 0.0)
        self.assertTrue(res.is_defined_risk)
        self.assertFalse(res.has_unbounded_loss)
        # The SPAN component can never exceed what the position can lose.
        self.assertLessEqual(res.span_margin,
                             res.worst_case_terminal_loss + 1e-9)
        # The whole point of the skill: cheaper than margining the shorts naked.
        naive = sum(
            self.calc.calculate_span_margin([leg], spot=100.0, vol=0.20
                                            ).total_required_margin
            for leg in _iron_condor() if leg.quantity < 0
        )
        self.assertLess(res.total_required_margin, naive)

    def test_defined_risk_cap_binds_the_span_component(self):
        # A tight 1-point vertical scanned with a wide price scan range: the raw
        # scan risk exceeds the spread's maximum loss, so the cap must bind.
        legs = [
            OptionLeg(100.0, OptionType.PUT, -1.0, time_to_expiry_years=T30),
            OptionLeg(99.0, OptionType.PUT, 1.0, time_to_expiry_years=T30),
        ]
        res = SPANMarginCalculator(
            price_scan_range_pct=0.30, exposure_margin_pct=0.0
        ).calculate_span_margin(legs, spot=100.0, vol=0.20)
        self.assertTrue(res.margin_capped_at_max_loss)
        # Reported money amounts are rounded to two decimal places.
        self.assertAlmostEqual(res.span_margin, res.worst_case_terminal_loss,
                               places=2)
        self.assertEqual(res.total_required_margin, res.span_margin)

    def test_exposure_overlay_is_charged_outside_the_max_loss_cap(self):
        # A venue charging a notional-based extreme-loss overlay takes it on top
        # of SPAN, so blocked capital can legitimately exceed the position's
        # maximum loss. Capping the total would understate what is actually
        # blocked -- the dangerous direction of error.
        legs = _iron_condor()
        capped = SPANMarginCalculator(
            price_scan_range_pct=0.30, exposure_margin_pct=0.03
        ).calculate_span_margin(legs, spot=100.0, vol=0.20)
        self.assertTrue(capped.margin_capped_at_max_loss)
        self.assertGreater(capped.total_required_margin,
                           capped.worst_case_terminal_loss)
        self.assertAlmostEqual(
            capped.total_required_margin,
            capped.span_margin + capped.exposure_margin, places=2)

    def test_ratio_spread_does_not_get_defined_risk_relief(self):
        # REGRESSION: the pre-2.0 defined-risk test only looked for an opposite
        # -signed leg of the same option type, so ten short puts hedged by one
        # long put were treated as fully hedged and margined at 0.0.
        legs = [
            OptionLeg(95.0, OptionType.PUT, -10.0, 2.0, time_to_expiry_years=T30),
            OptionLeg(90.0, OptionType.PUT, 1.0, 1.0, time_to_expiry_years=T30),
        ]
        res = self.calc.calculate_span_margin(legs, spot=100.0, vol=0.20)
        # Exposure margin is charged on all ten short contracts:
        # 10 * 100 (spot) * 100 (multiplier) * 0.03 = 3000.
        self.assertAlmostEqual(res.exposure_margin, 3000.0, places=2)
        self.assertGreater(res.total_required_margin, 3000.0)

        single = self.calc.calculate_span_margin(
            [OptionLeg(95.0, OptionType.PUT, -1.0, 2.0,
                       time_to_expiry_years=T30)],
            spot=100.0, vol=0.20)
        self.assertGreater(res.total_required_margin,
                           5 * single.total_required_margin)

    def test_naked_short_call_is_flagged_unbounded_and_not_capped(self):
        res = self.calc.calculate_span_margin(
            [OptionLeg(105.0, OptionType.CALL, -1.0, 2.0,
                       time_to_expiry_years=T30)],
            spot=100.0, vol=0.20)
        self.assertTrue(res.has_unbounded_loss)
        self.assertFalse(res.is_defined_risk)
        self.assertFalse(res.margin_capped_at_max_loss)
        self.assertEqual(res.worst_case_terminal_loss, math.inf)
        self.assertGreater(res.total_required_margin, 0.0)

    def test_margin_responds_to_a_volatility_change(self):
        # REGRESSION: the pre-2.0 calculation was completely vol-insensitive,
        # which defeats the purpose of a volatility scan.
        legs = _iron_condor()
        low = self.calc.calculate_span_margin(legs, spot=100.0, vol=0.10)
        high = self.calc.calculate_span_margin(legs, spot=100.0, vol=0.40)
        self.assertNotAlmostEqual(low.total_required_margin,
                                  high.total_required_margin, places=2)

    def test_entry_premium_does_not_affect_the_requirement(self):
        # REGRESSION: the pre-2.0 scan measured profit and loss against the
        # entry premium, so the same live position produced a different margin
        # depending on the price it was filled at.
        cheap = self.calc.calculate_span_margin(
            [OptionLeg(95.0, OptionType.PUT, -1.0, premium=0.5,
                       time_to_expiry_years=T30)], spot=100.0, vol=0.20)
        dear = self.calc.calculate_span_margin(
            [OptionLeg(95.0, OptionType.PUT, -1.0, premium=9.0,
                       time_to_expiry_years=T30)], spot=100.0, vol=0.20)
        self.assertEqual(cheap.total_required_margin, dear.total_required_margin)

    def test_mark_price_overrides_the_model_for_net_option_value(self):
        res = self.calc.calculate_span_margin(
            [OptionLeg(95.0, OptionType.PUT, -2.0, mark_price=3.0,
                       time_to_expiry_years=T30)], spot=100.0, vol=0.20)
        self.assertAlmostEqual(res.net_option_value, -600.0, places=2)

    def test_short_option_minimum_floors_the_risk_requirement(self):
        legs = [OptionLeg(80.0, OptionType.PUT, -1.0, time_to_expiry_years=T30)]
        without = self.calc.calculate_span_margin(legs, spot=100.0, vol=0.20)
        floored = SPANMarginCalculator(
            short_option_minimum_per_contract=1000.0
        ).calculate_span_margin(legs, spot=100.0, vol=0.20)
        self.assertEqual(floored.short_option_minimum, 1000.0)
        self.assertGreater(floored.total_required_margin,
                           without.total_required_margin)

    def test_exposure_margin_can_be_disabled_for_non_indian_venues(self):
        calc = SPANMarginCalculator(exposure_margin_pct=0.0)
        res = calc.calculate_span_margin(
            _iron_condor(), spot=100.0, vol=0.20)
        self.assertEqual(res.exposure_margin, 0.0)
        self.assertGreater(res.total_required_margin, 0.0)

    def test_wider_price_scan_range_never_reduces_scan_risk(self):
        legs = [OptionLeg(95.0, OptionType.PUT, -1.0, time_to_expiry_years=T30)]
        narrow = SPANMarginCalculator(price_scan_range_pct=0.06
                                      ).calculate_span_margin(legs, 100.0, 0.20)
        wide = SPANMarginCalculator(price_scan_range_pct=0.15
                                    ).calculate_span_margin(legs, 100.0, 0.20)
        self.assertGreater(wide.span_scan_risk, narrow.span_scan_risk)

    def test_empty_portfolio_is_a_zero_requirement(self):
        res = self.calc.calculate_span_margin([], spot=100.0, vol=0.20)
        self.assertEqual(res.total_required_margin, 0.0)
        self.assertEqual(res.worst_scenario_name, "NONE")
        self.assertEqual(res.scenario_payoffs, {})

    def test_valuation_mode_is_reported(self):
        self.assertEqual(
            self.calc.calculate_span_margin(
                _iron_condor(), 100.0, 0.20).valuation_mode, "black_scholes")
        self.assertEqual(
            self.calc.calculate_span_margin(
                _iron_condor(ttm=None), 100.0, 0.20).valuation_mode, "intrinsic")
        mixed = _iron_condor()
        mixed[0].time_to_expiry_years = None
        self.assertEqual(
            self.calc.calculate_span_margin(mixed, 100.0, 0.20).valuation_mode,
            "mixed")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.calc = SPANMarginCalculator()
        logging.disable(logging.WARNING)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_non_positive_spot_and_vol_are_rejected(self):
        legs = [OptionLeg(95.0, OptionType.PUT, -1.0)]
        with self.assertRaises(ValueError):
            self.calc.calculate_span_margin(legs, spot=0.0, vol=0.20)
        with self.assertRaises(ValueError):
            self.calc.calculate_span_margin(legs, spot=100.0, vol=0.0)

    def test_nan_spot_is_rejected_rather_than_propagated(self):
        # A NaN spot previously produced a NaN margin that compared False
        # against every limit check downstream.
        with self.assertRaises(ValueError):
            self.calc.calculate_span_margin(
                [OptionLeg(95.0, OptionType.PUT, -1.0)],
                spot=float("nan"), vol=0.20)

    def test_invalid_leg_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            OptionLeg(95.0, OptionType.PUT, 0.0)
        with self.assertRaises(ValueError):
            OptionLeg(-1.0, OptionType.PUT, 1.0)
        with self.assertRaises(ValueError):
            OptionLeg(95.0, OptionType.PUT, 1.0, multiplier=0.0)
        with self.assertRaises(ValueError):
            OptionLeg(95.0, OptionType.PUT, 1.0, time_to_expiry_years=-0.5)
        with self.assertRaises(ValueError):
            OptionLeg(float("inf"), OptionType.PUT, 1.0)

    def test_invalid_calculator_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            SPANMarginCalculator(price_scan_range_pct=0.0)
        with self.assertRaises(ValueError):
            SPANMarginCalculator(extreme_move_cover_fraction=1.5)
        with self.assertRaises(ValueError):
            SPANMarginCalculator(exposure_margin_pct=-0.01)
        with self.assertRaises(ValueError):
            SPANMarginCalculator(price_shocks_pct=[-0.05])
        with self.assertRaises(ValueError):
            SPANMarginCalculator(price_shocks_pct=[], vol_shocks_pct=[])


class TestBackwardCompatibleHelpers(unittest.TestCase):

    def test_span_style_margin_estimate(self):
        payoffs = {"scen1": 100.0, "scen2": -400.0, "scen3": -200.0}
        self.assertEqual(span_style_margin_estimate(payoffs), 400.0)

    def test_span_style_margin_estimate_floors_at_zero(self):
        self.assertEqual(span_style_margin_estimate({"a": 10.0, "b": 5.0}), 0.0)

    def test_span_style_margin_estimate_handles_empty_input(self):
        # Previously raised ValueError from min() on an empty sequence.
        self.assertEqual(span_style_margin_estimate({}), 0.0)

    def test_build_price_vol_scenarios(self):
        grid = build_price_vol_scenarios(100.0, 0.20, [-0.05, 0.05], [-0.1, 0.1])
        self.assertIn("price-5%_vol-10%", grid)
        self.assertEqual(len(grid), 4)
        self.assertAlmostEqual(grid["price-5%_vol-10%"]["spot"], 95.0)
        self.assertAlmostEqual(grid["price-5%_vol-10%"]["vol"], 0.18)


if __name__ == "__main__":
    unittest.main()
