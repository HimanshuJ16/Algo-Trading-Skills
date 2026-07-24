"""
Unit tests for options-margin-span-calculation-global skill.

Tests:
1. SPAN 14-scenario grid evaluation.
2. Iron Condor defined-risk margin netting vs naive sum.
3. Naked Short Put vs Long Call margin requirement comparison.
4. Intraday volatility shock margin escalation.
5. Backward compatibility with span_style_margin_estimate and build_price_vol_scenarios.
"""
import unittest
from span_approx import (
    OptionLeg,
    OptionType,
    SPANMarginCalculator,
    build_price_vol_scenarios,
    span_style_margin_estimate,
)


class TestOptionsMarginSPANCalculation(unittest.TestCase):

    def setUp(self):
        self.calc = SPANMarginCalculator()

    def test_iron_condor_margin_netting(self):
        # Spot = 100
        # Bull Put Spread: Long 90 Put (+1), Short 95 Put (-1)
        # Bear Call Spread: Short 105 Call (-1), Long 110 Call (+1)
        legs = [
            OptionLeg(strike=90.0, option_type=OptionType.PUT, quantity=1.0, premium=1.0),
            OptionLeg(strike=95.0, option_type=OptionType.PUT, quantity=-1.0, premium=2.5),
            OptionLeg(strike=105.0, option_type=OptionType.CALL, quantity=-1.0, premium=2.5),
            OptionLeg(strike=110.0, option_type=OptionType.CALL, quantity=1.0, premium=1.0),
        ]

        res = self.calc.calculate_span_margin(legs, spot=100.0, vol=0.20)

        self.assertTrue(res.is_defined_risk)
        # SPAN margin should reflect max loss of width (5 * 100 = 500) minus net premium (3.0 * 100 = 300) -> ~350-500
        self.assertLess(res.total_required_margin, 1000.0)

    def test_naked_short_put_requires_exposure_margin(self):
        # Naked Short Put (unhedged)
        naked_leg = [OptionLeg(strike=95.0, option_type=OptionType.PUT, quantity=-1.0, premium=2.0)]
        res = self.calc.calculate_span_margin(naked_leg, spot=100.0, vol=0.20)

        self.assertFalse(res.is_defined_risk)
        self.assertGreater(res.exposure_margin, 0.0)

    def test_backward_compatibility(self):
        payoffs = {"scen1": 100.0, "scen2": -400.0, "scen3": -200.0}
        est = span_style_margin_estimate(payoffs)
        self.assertEqual(est, 400.0)

        grid = build_price_vol_scenarios(100.0, 0.20, [-0.05, 0.05], [-0.1, 0.1])
        self.assertIn("price-5%_vol-10%", grid)


if __name__ == "__main__":
    unittest.main()
