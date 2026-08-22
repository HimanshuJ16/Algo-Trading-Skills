import unittest

from cftc_commodity_pool_operator_registration import (
    CftcCpoComplianceEngine,
    ComplianceDecision,
    PortfolioState,
    ProposedTrade,
)


class TestCftcCpoComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CftcCpoComplianceEngine()

    @staticmethod
    def _flat_pool(liquidation_value=1_000_000):
        return PortfolioState(
            liquidation_value=liquidation_value,
            current_commodity_initial_margin=0,
            current_commodity_notional=0,
        )

    def test_non_commodity_trade_always_passes(self):
        trade = ProposedTrade(
            is_commodity_interest=False, required_initial_margin=500_000, notional_value=2_000_000)
        self.assertTrue(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_passes_margin_fails_notional(self):
        # 20k margin = 2.0% of 1M (test A passes); 1.5M notional = 150% (test B fails).
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=20_000, notional_value=1_500_000)
        self.assertTrue(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_fails_margin_passes_notional(self):
        # 60k margin = 6.0% (test A fails); 500k notional = 50% (test B passes).
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=60_000, notional_value=500_000)
        self.assertTrue(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_fails_both_tests(self):
        # 6.0% margin and 110% notional: both 4.13(a)(3)(ii) tests fail.
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=60_000, notional_value=1_100_000)
        self.assertFalse(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_cumulative_portfolio_violation(self):
        # Existing 45k + 10k = 55k margin (5.5%); existing 900k + 150k = 1.05M notional (105%).
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=45_000,
            current_commodity_notional=900_000,
        )
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=10_000, notional_value=150_000)
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

    # --- Threshold boundary behaviour ---------------------------------------

    def test_margin_exactly_at_five_percent_passes(self):
        # 50,000 / 1,000,000 == 5.00% exactly; the rule says "will not exceed".
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=50_000, notional_value=5_000_000)
        decision = self.engine.evaluate_trade(self._flat_pool(), trade)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.passes_margin_test)
        self.assertFalse(decision.passes_notional_test)

    def test_notional_exactly_at_one_hundred_percent_passes(self):
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=400_000, notional_value=1_000_000)
        decision = self.engine.evaluate_trade(self._flat_pool(), trade)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.passes_margin_test)
        self.assertTrue(decision.passes_notional_test)

    def test_one_unit_over_both_thresholds_is_blocked(self):
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=50_001, notional_value=1_000_001)
        self.assertFalse(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_non_round_liquidation_value_at_exact_threshold(self):
        # 5% of 3,333,333.33 evaluated without an intermediate division.
        lv = 3_333_333.33
        trade = ProposedTrade(
            is_commodity_interest=True,
            required_initial_margin=0.05 * lv,
            notional_value=10 * lv,
        )
        self.assertTrue(self.engine.check_trade_compliance(self._flat_pool(lv), trade))

    # --- Risk-reducing trades ------------------------------------------------

    def test_risk_reducing_trade_allowed_while_pool_is_in_breach(self):
        # Pool is already outside both tests (10% margin, 300% notional) and stays
        # outside after the unwind (8% / 200%). Blocking the unwind would trap the
        # pool in the state that requires CPO registration.
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=100_000,
            current_commodity_notional=3_000_000,
        )
        unwind = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=-20_000, notional_value=-1_000_000)
        decision = self.engine.evaluate_trade(portfolio, unwind)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.passes_margin_test)
        self.assertFalse(decision.passes_notional_test)
        self.assertEqual(decision.projected_commodity_initial_margin, 80_000)
        self.assertEqual(decision.projected_commodity_notional, 2_000_000)

    def test_risk_reducing_trade_allowed_with_non_positive_liquidation_value(self):
        portfolio = PortfolioState(
            liquidation_value=0.0,
            current_commodity_initial_margin=10_000,
            current_commodity_notional=100_000,
        )
        unwind = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=-10_000, notional_value=-100_000)
        self.assertTrue(self.engine.check_trade_compliance(portfolio, unwind))

    def test_partial_reduction_that_still_leaves_one_test_failing_is_allowed(self):
        # Reduces notional only; margin unchanged. Still allowed because neither
        # aggregate increases.
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=80_000,
            current_commodity_notional=3_000_000,
        )
        unwind = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=0.0, notional_value=-100_000)
        self.assertTrue(self.engine.check_trade_compliance(portfolio, unwind))

    def test_over_release_of_exposure_is_rejected_as_inconsistent(self):
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=10_000,
            current_commodity_notional=100_000,
        )
        bad = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=-25_000, notional_value=-50_000)
        with self.assertRaises(ValueError):
            self.engine.evaluate_trade(portfolio, bad)
        self.assertFalse(self.engine.check_trade_compliance(portfolio, bad))

    # --- Liquidation value and input validation ------------------------------

    def test_zero_liquidation_value_blocks_new_exposure(self):
        portfolio = self._flat_pool(0.0)
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=1.0, notional_value=1.0)
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

    def test_negative_liquidation_value_blocks_new_exposure(self):
        portfolio = self._flat_pool(-500_000)
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=1.0, notional_value=1.0)
        decision = self.engine.evaluate_trade(portfolio, trade)
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.passes_margin_test)
        self.assertFalse(decision.passes_notional_test)

    def test_nan_liquidation_value_fails_closed(self):
        portfolio = self._flat_pool(float("nan"))
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=1.0, notional_value=1.0)
        with self.assertRaises(ValueError):
            self.engine.evaluate_trade(portfolio, trade)
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

    def test_infinite_trade_margin_fails_closed(self):
        trade = ProposedTrade(
            is_commodity_interest=True,
            required_initial_margin=float("inf"),
            notional_value=1.0,
        )
        self.assertFalse(self.engine.check_trade_compliance(self._flat_pool(), trade))

    def test_negative_portfolio_aggregate_is_rejected(self):
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=-1.0,
            current_commodity_notional=0.0,
        )
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=1.0, notional_value=1.0)
        with self.assertRaises(ValueError):
            self.engine.evaluate_trade(portfolio, trade)

    def test_invalid_thresholds_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            CftcCpoComplianceEngine(margin_threshold=-0.05)
        with self.assertRaises(ValueError):
            CftcCpoComplianceEngine(notional_threshold=float("nan"))

    def test_none_input_fails_closed(self):
        portfolio = PortfolioState(
            liquidation_value=None,
            current_commodity_initial_margin=0.0,
            current_commodity_notional=0.0,
        )
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=1.0, notional_value=1.0)
        with self.assertRaises(ValueError):
            self.engine.evaluate_trade(portfolio, trade)
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

    def test_looser_than_statutory_thresholds_are_warned(self):
        with self.assertLogs(
                "cftc_commodity_pool_operator_registration", level="WARNING") as captured:
            CftcCpoComplianceEngine(margin_threshold=0.10)
        self.assertIn("4.13(a)(3)(ii)", "".join(captured.output))

    def test_tighter_internal_buffer_is_honoured(self):
        strict = CftcCpoComplianceEngine(margin_threshold=0.03, notional_threshold=0.80)
        # 4% margin and 90% notional: inside the statutory limits, outside the buffer.
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=40_000, notional_value=900_000)
        self.assertTrue(self.engine.check_trade_compliance(self._flat_pool(), trade))
        self.assertFalse(strict.check_trade_compliance(self._flat_pool(), trade))

    # --- Decision auditability ----------------------------------------------

    def test_decision_records_ratios_and_reason(self):
        trade = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=60_000, notional_value=1_100_000)
        decision = self.engine.evaluate_trade(self._flat_pool(), trade)
        self.assertIsInstance(decision, ComplianceDecision)
        self.assertFalse(decision.allowed)
        self.assertAlmostEqual(decision.margin_ratio, 0.06)
        self.assertAlmostEqual(decision.notional_ratio, 1.10)
        self.assertIn("4.13(a)(3)(ii)", decision.reason)

    def test_gross_convention_short_notional_adds_exposure(self):
        # Direction is not encoded in the sign: a short future opened alongside an
        # existing long adds gross notional rather than netting it away.
        portfolio = PortfolioState(
            liquidation_value=1_000_000,
            current_commodity_initial_margin=60_000,
            current_commodity_notional=900_000,
        )
        short_leg = ProposedTrade(
            is_commodity_interest=True, required_initial_margin=10_000, notional_value=900_000)
        decision = self.engine.evaluate_trade(portfolio, short_leg)
        self.assertEqual(decision.projected_commodity_notional, 1_800_000)
        self.assertFalse(decision.allowed)


if __name__ == '__main__':
    unittest.main()
