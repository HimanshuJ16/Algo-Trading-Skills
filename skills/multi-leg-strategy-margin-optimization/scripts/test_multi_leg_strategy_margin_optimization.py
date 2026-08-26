"""Unit tests for the strategy-based multi-leg option margin estimator.

Expected values are derived by hand from FINRA Rule 4210(f)(2) / the Cboe
strategy-based margin table, not by re-running the module's own arithmetic:

  naked short call  = max(0.20*S - max(0, K-S) + P,  0.10*S + P) * 100 * Q
  naked short put   = max(0.20*S - max(0, S-K) + P,  0.10*K + P) * 100 * Q
  long option       = P * 100 * Q                       (paid for in full)
  spread short leg  = min(naked (E) requirement, maximum potential loss)
"""
import logging
import unittest
from datetime import date

from multi_leg_strategy_margin_optimization import (
    BINDING_MAX_LOSS,
    BINDING_NAKED_REQUIREMENT,
    BINDING_NONE,
    STATUS_NO_OFFSET_MULTI_EXPIRY,
    STATUS_NO_OFFSET_NAKED_BINDS,
    STATUS_NO_OFFSET_UNDEFINED_RISK,
    STATUS_OFFSET_APPLIED,
    MarginInputError,
    MarginOptimizationReport,
    MultiLegStrategyMarginOptimizerEngine,
    MultiLegStrategyPayload,
    OptionLeg,
)

EXP = "2026-09-18"
LATER = "2026-12-18"


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class TestNakedLegRequirements(unittest.TestCase):
    """The (E) formula in isolation, via a single-leg payload."""

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def _requirement(self, leg, spot=150.0, **kwargs):
        payload = MultiLegStrategyPayload("T", spot, [leg], **kwargs)
        return self.engine.optimize_strategy_margin(payload).uncombined_requirement_usd

    def test_short_otm_call_uses_twenty_percent_less_out_of_the_money(self):
        # S=150, K=155, P=2 -> 0.20*150 - 5 + 2 = 27.00 vs floor 0.10*150 + 2 = 17.00
        self.assertEqual(self._requirement(OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)), 2700.0)

    def test_short_deep_otm_call_falls_back_to_ten_percent_floor(self):
        # S=150, K=200, P=0.10 -> 0.20*150 - 50 + 0.1 = -19.90, floor 0.10*150 + 0.1 = 15.10
        self.assertEqual(self._requirement(OptionLeg("CALL", "SELL", 200.0, EXP, 1, 0.10)), 1510.0)

    def test_short_deep_otm_put_floors_on_exercise_price_not_underlying(self):
        # S=150, K=100, P=0.10 -> 0.20*150 - 50 + 0.1 = -19.90.
        # Put floor is 10% of the EXERCISE PRICE: 0.10*100 + 0.1 = 10.10, not
        # 0.10*150 + 0.1 = 15.10. Guards against reusing the call floor.
        self.assertEqual(self._requirement(OptionLeg("PUT", "SELL", 100.0, EXP, 1, 0.10)), 1010.0)

    def test_long_option_is_paid_for_in_full(self):
        self.assertEqual(self._requirement(OptionLeg("CALL", "BUY", 150.0, EXP, 3, 5.0)), 1500.0)

    def test_broad_based_index_percentage_is_configurable(self):
        # 0.15*150 - 5 + 2 = 19.50 vs floor 17.00 -> 1950.
        index_engine = MultiLegStrategyMarginOptimizerEngine(underlying_pct=0.15)
        payload = MultiLegStrategyPayload("SPX", 150.0, [OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)])
        self.assertEqual(
            index_engine.optimize_strategy_margin(payload).uncombined_requirement_usd, 1950.0
        )

    def test_contract_multiplier_scales_linearly(self):
        self.assertEqual(
            self._requirement(OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0), contract_multiplier=10.0),
            270.0,
        )


class TestIronCondor(unittest.TestCase):
    """AAPL iron condor at S=150: long 140P/short 145P, short 155C/long 160C."""

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()
        self.legs = [
            OptionLeg("PUT", "BUY", 140.0, EXP, 1, 0.8),
            OptionLeg("PUT", "SELL", 145.0, EXP, 1, 2.0),
            OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0),
            OptionLeg("CALL", "BUY", 160.0, EXP, 1, 0.8),
        ]
        self.report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload("AAPL", 150.0, self.legs)
        )

    def test_classified_as_iron_condor(self):
        self.assertEqual(self.report.strategy_type, "IRON_CONDOR")
        self.assertEqual(self.report.status, STATUS_OFFSET_APPLIED)
        self.assertEqual(self.report.binding_constraint, BINDING_MAX_LOSS)
        self.assertEqual(self.report.warnings, [])

    def test_uncombined_requirement(self):
        # longs 0.8*100 + 0.8*100 = 160
        # short 145P: max(30 - 5 + 2, 14.5 + 2) = 27.00 -> 2700
        # short 155C: max(30 - 5 + 2, 15.0 + 2) = 27.00 -> 2700
        self.assertEqual(self.report.uncombined_requirement_usd, 5560.0)

    def test_maximum_potential_loss_is_the_wider_wing_only(self):
        # Both wings are $5 wide and only one side can finish in the money, so
        # the netted intrinsic loss at 140/160 is $5 per share, never $10.
        self.assertEqual(self.report.max_potential_loss_usd, 500.0)

    def test_combined_requirement_and_net_capital(self):
        # longs paid in full (160) + min(naked 5400, max loss 500) = 660 gross.
        self.assertEqual(self.report.combined_requirement_usd, 660.0)
        # Net credit = (2.0 + 2.0 - 0.8 - 0.8) * 100 = 240.
        self.assertEqual(self.report.net_premium_usd, 240.0)
        # Deposit = max risk less net credit = 500 - 240 = 260.
        self.assertEqual(self.report.net_capital_required_usd, 260.0)

    def test_savings_are_reported_on_a_consistent_gross_basis(self):
        self.assertEqual(self.report.margin_savings_usd, 5560.0 - 660.0)
        self.assertEqual(self.report.margin_reduction_pct, 88.1)

    def test_returns_a_report_dataclass(self):
        self.assertIsInstance(self.report, MarginOptimizationReport)


class TestVerticalSpreads(unittest.TestCase):

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def test_debit_call_spread_requires_the_net_debit_not_the_strike_width(self):
        # REGRESSION. Long 150C @5 / short 155C @2 can never lose intrinsic
        # value: at every strike price point the netted intrinsic is >= 0, so
        # the FINRA maximum potential loss is 0 and the whole position costs
        # the net debit of $300. A width-based formula reports $500, which is
        # both the wrong max loss and the wrong requirement.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "BUY", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0),
                ],
            )
        )
        self.assertEqual(report.strategy_type, "VERTICAL_SPREAD")
        self.assertEqual(report.max_potential_loss_usd, 0.0)
        self.assertEqual(report.net_premium_usd, -300.0)
        self.assertEqual(report.net_capital_required_usd, 300.0)
        self.assertNotEqual(report.net_capital_required_usd, 500.0)

    def test_credit_call_spread_requires_max_risk_less_net_credit(self):
        # Short 150C @5 / long 155C @2. Max potential loss $500 at K=155,
        # net credit $300 -> deposit $200.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "BUY", 155.0, EXP, 1, 2.0),
                ],
            )
        )
        self.assertEqual(report.max_potential_loss_usd, 500.0)
        self.assertEqual(report.net_premium_usd, 300.0)
        self.assertEqual(report.net_capital_required_usd, 200.0)

    def test_credit_put_spread_max_loss_is_the_strike_width(self):
        # Short 145P @2 / long 140P @0.8: worst netted intrinsic is -$5 at any
        # spot <= 140, so max loss $500, credit $120, deposit $380.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("PUT", "SELL", 145.0, EXP, 1, 2.0),
                    OptionLeg("PUT", "BUY", 140.0, EXP, 1, 0.8),
                ],
            )
        )
        self.assertEqual(report.max_potential_loss_usd, 500.0)
        self.assertEqual(report.net_capital_required_usd, 380.0)

    def test_very_wide_credit_spread_is_capped_by_the_naked_requirement(self):
        # 4210(f)(2)(H) charges the LESSER of the naked (E) requirement and the
        # maximum potential loss. Short 150C @5 / long 300C @0.05 has a
        # $15,000 max loss but only a $3,500 naked requirement, so the naked
        # figure binds and recognising the spread frees nothing.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "BUY", 300.0, EXP, 1, 0.05),
                ],
            )
        )
        self.assertEqual(report.max_potential_loss_usd, 15000.0)
        self.assertEqual(report.binding_constraint, BINDING_NAKED_REQUIREMENT)
        self.assertEqual(report.status, STATUS_NO_OFFSET_NAKED_BINDS)
        self.assertEqual(report.margin_savings_usd, 0.0)


class TestUndefinedRiskIsNotOffset(unittest.TestCase):
    """Structures that look like spreads but carry uncovered short risk."""

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def test_four_naked_shorts_are_not_treated_as_an_iron_condor(self):
        # REGRESSION. Two short puts and two short calls have the leg *shape*
        # of an iron condor. A shape-based classifier charged the wing width
        # less the credit -- here $0 -- for four uncovered short options.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("PUT", "SELL", 140.0, EXP, 1, 0.8),
                    OptionLeg("PUT", "SELL", 145.0, EXP, 1, 2.0),
                    OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0),
                    OptionLeg("CALL", "SELL", 160.0, EXP, 1, 0.8),
                ],
            )
        )
        self.assertEqual(report.strategy_type, "UNDEFINED_RISK_COMBINATION")
        self.assertEqual(report.status, STATUS_NO_OFFSET_UNDEFINED_RISK)
        self.assertIsNone(report.max_potential_loss_usd)
        # Full naked sum: 2*(0.20*150 - 10 + 0.8)*100 floored at
        # (0.10*140 + 0.8)*100 = 1480 for the 140P, plus 2700 for each of the
        # 145P and 155C, plus 1580 for the 160C.
        self.assertEqual(report.uncombined_requirement_usd, 9560.0)
        self.assertEqual(report.combined_requirement_usd, 9560.0)
        self.assertEqual(report.margin_savings_usd, 0.0)
        self.assertTrue(any("unbounded" in w for w in report.warnings))

    def test_undefined_risk_is_also_logged_as_a_warning(self):
        # The warning must reach the log, not just the report field -- an agent
        # reading only the headline number needs it on the operator's console.
        logging.disable(logging.NOTSET)
        self.addCleanup(logging.disable, logging.CRITICAL)
        with self.assertLogs("multi_leg_strategy_margin_optimization", level="WARNING") as caught:
            self.engine.optimize_strategy_margin(
                MultiLegStrategyPayload(
                    "AAPL", 150.0, [OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)]
                )
            )
        self.assertTrue(any("unbounded" in line for line in caught.output))

    def test_ratio_spread_with_unmatched_quantities_gets_no_offset(self):
        # REGRESSION. Long 1 x 150C, short 5 x 155C. Taking the quantity from
        # the first leg priced this as a 1-lot vertical ($500) while four short
        # calls were entirely uncovered.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "BUY", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "SELL", 155.0, EXP, 5, 2.0),
                ],
            )
        )
        self.assertEqual(report.status, STATUS_NO_OFFSET_UNDEFINED_RISK)
        # 500 long + 5 * 2700 naked short.
        self.assertEqual(report.combined_requirement_usd, 14000.0)
        self.assertGreater(report.combined_requirement_usd, 500.0)

    def test_balanced_ratio_backspread_is_bounded_and_offset(self):
        # The mirror case: short 1 x 150C, long 5 x 155C has positive slope at
        # the top, so it IS bounded and must still receive an offset.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "BUY", 155.0, EXP, 5, 2.0),
                ],
            )
        )
        self.assertEqual(report.max_potential_loss_usd, 500.0)
        self.assertEqual(report.binding_constraint, BINDING_MAX_LOSS)


class TestReverseIronCondor(unittest.TestCase):

    def test_long_body_short_wings_costs_the_net_debit(self):
        # REGRESSION. Long 145P/155C, short 140P/160C is a pair of DEBIT
        # spreads: netted intrinsic is never negative, so max loss is 0 and the
        # position costs its $240 net debit, not the $500 wing width.
        engine = MultiLegStrategyMarginOptimizerEngine()
        report = engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("PUT", "SELL", 140.0, EXP, 1, 0.8),
                    OptionLeg("PUT", "BUY", 145.0, EXP, 1, 2.0),
                    OptionLeg("CALL", "BUY", 155.0, EXP, 1, 2.0),
                    OptionLeg("CALL", "SELL", 160.0, EXP, 1, 0.8),
                ],
            )
        )
        self.assertEqual(report.strategy_type, "REVERSE_IRON_CONDOR")
        self.assertEqual(report.max_potential_loss_usd, 0.0)
        self.assertEqual(report.net_premium_usd, -240.0)
        self.assertEqual(report.net_capital_required_usd, 240.0)


class TestIronButterfly(unittest.TestCase):

    def test_iron_butterfly_is_classified_and_priced_on_one_wing(self):
        engine = MultiLegStrategyMarginOptimizerEngine()
        report = engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("PUT", "BUY", 145.0, EXP, 1, 1.0),
                    OptionLeg("PUT", "SELL", 150.0, EXP, 1, 3.0),
                    OptionLeg("CALL", "SELL", 150.0, EXP, 1, 3.0),
                    OptionLeg("CALL", "BUY", 155.0, EXP, 1, 1.0),
                ],
            )
        )
        self.assertEqual(report.strategy_type, "IRON_BUTTERFLY")
        self.assertEqual(report.max_potential_loss_usd, 500.0)
        # Credit 400 -> deposit 100.
        self.assertEqual(report.net_capital_required_usd, 100.0)


class TestExpirationHandling(unittest.TestCase):

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def test_diagonal_gets_no_offset(self):
        # REGRESSION. Without an expiration field, a diagonal was priced as a
        # vertical on strike width alone.
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0),
                    OptionLeg("CALL", "BUY", 160.0, LATER, 1, 3.0),
                ],
            )
        )
        self.assertEqual(report.strategy_type, "MULTI_EXPIRY_COMBINATION")
        self.assertEqual(report.status, STATUS_NO_OFFSET_MULTI_EXPIRY)
        self.assertIsNone(report.max_potential_loss_usd)
        self.assertEqual(report.margin_savings_usd, 0.0)

    def test_short_expiring_after_long_is_flagged_as_not_a_spread(self):
        report = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 155.0, LATER, 1, 3.0),
                    OptionLeg("CALL", "BUY", 160.0, EXP, 1, 1.0),
                ],
            )
        )
        self.assertTrue(any("expire on or before" in w for w in report.warnings))
        self.assertEqual(report.margin_savings_usd, 0.0)

    def test_date_objects_and_iso_strings_are_equivalent(self):
        as_strings = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 150.0, EXP, 1, 5.0),
                    OptionLeg("CALL", "BUY", 155.0, EXP, 1, 2.0),
                ],
            )
        )
        as_dates = self.engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "SELL", 150.0, date(2026, 9, 18), 1, 5.0),
                    OptionLeg("CALL", "BUY", 155.0, date(2026, 9, 18), 1, 2.0),
                ],
            )
        )
        self.assertEqual(
            as_strings.net_capital_required_usd, as_dates.net_capital_required_usd
        )
        self.assertEqual(as_dates.status, STATUS_OFFSET_APPLIED)


class TestInputValidation(unittest.TestCase):
    """Malformed input must raise, never silently understate the requirement."""

    def setUp(self):
        self.engine = MultiLegStrategyMarginOptimizerEngine()

    def _assert_rejects(self, legs, spot=150.0, **kwargs):
        with self.assertRaises(MarginInputError):
            self.engine.optimize_strategy_margin(
                MultiLegStrategyPayload("T", spot, legs, **kwargs)
            )

    def test_unrecognised_option_type_raises_instead_of_returning_zero_margin(self):
        # REGRESSION. A typo previously fell through to `return 0.0`, so a
        # short leg contributed no margin at all.
        self._assert_rejects([OptionLeg("CAL", "SELL", 155.0, EXP, 1, 2.0)])

    def test_unrecognised_action_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SHORT", 155.0, EXP, 1, 2.0)])

    def test_non_positive_quantity_raises(self):
        # Direction belongs in `action`; abs()-ing a signed quantity silently
        # turned a short leg into a long one.
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, -3, 2.0)])
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 0, 2.0)])

    def test_fractional_quantity_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1.5, 2.0)])

    def test_non_finite_premium_raises_instead_of_propagating_nan(self):
        # REGRESSION. NaN previously flowed into the report and the reduction
        # percentage came out as a clean 0.0%, masking the corruption.
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1, float("nan"))])
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1, float("inf"))])

    def test_negative_premium_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1, -2.0)])

    def test_non_positive_strike_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 0.0, EXP, 1, 2.0)])

    def test_malformed_expiration_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, "18-09-2026", 1, 2.0)])
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, None, 1, 2.0)])

    def test_non_positive_underlying_price_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)], spot=0.0)

    def test_non_finite_underlying_price_raises(self):
        self._assert_rejects([OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)], spot=float("nan"))

    def test_empty_legs_raises(self):
        self._assert_rejects([])

    def test_non_positive_contract_multiplier_raises(self):
        self._assert_rejects(
            [OptionLeg("CALL", "SELL", 155.0, EXP, 1, 2.0)], contract_multiplier=0.0
        )

    def test_invalid_engine_percentages_raise(self):
        with self.assertRaises(MarginInputError):
            MultiLegStrategyMarginOptimizerEngine(underlying_pct=0.0)
        with self.assertRaises(MarginInputError):
            MultiLegStrategyMarginOptimizerEngine(underlying_pct=1.5)
        with self.assertRaises(MarginInputError):
            # A floor above the base percentage would bind on every leg.
            MultiLegStrategyMarginOptimizerEngine(underlying_pct=0.10, minimum_pct=0.20)


class TestLongOnlyPositions(unittest.TestCase):

    def test_long_only_combination_has_no_short_requirement_and_no_saving(self):
        engine = MultiLegStrategyMarginOptimizerEngine()
        report = engine.optimize_strategy_margin(
            MultiLegStrategyPayload(
                "AAPL",
                150.0,
                [
                    OptionLeg("CALL", "BUY", 150.0, EXP, 1, 5.0),
                    OptionLeg("PUT", "BUY", 150.0, EXP, 1, 4.0),
                ],
            )
        )
        self.assertEqual(report.binding_constraint, BINDING_NONE)
        self.assertEqual(report.max_potential_loss_usd, 0.0)
        self.assertEqual(report.combined_requirement_usd, 900.0)
        self.assertEqual(report.net_capital_required_usd, 900.0)
        self.assertEqual(report.margin_savings_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
