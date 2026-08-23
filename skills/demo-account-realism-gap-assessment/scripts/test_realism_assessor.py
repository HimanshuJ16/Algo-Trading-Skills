"""
Unit tests for demo-account-realism-gap-assessment skill.

Expected realism scores are derived by hand from the documented weighting
(30% latency + 40% slippage + 30% fill rate) rather than by re-running the
implementation's own expression, so an arithmetic regression is detectable.
"""
import logging
import unittest

from realism_assessor import DemoRealismAssessor, ExecutionLog

logging.getLogger("realism_assessor").setLevel(logging.CRITICAL)


def demo_log(arrival=150.0, fill=150.0, req=100, filled=100,
             t0=1000.0, t1=1000.01, side="BUY", symbol="AAPL"):
    return ExecutionLog("DEMO", symbol, arrival, fill, req, filled, t0, t1, side)


def live_log(arrival=150.0, fill=150.075, req=100, filled=90,
             t0=1000.0, t1=1000.05, side="BUY", symbol="AAPL"):
    return ExecutionLog("LIVE", symbol, arrival, fill, req, filled, t0, t1, side)


class TestDemoRealismAssessor(unittest.TestCase):

    def setUp(self):
        # Demo: 10ms latency, 0 bps slippage, 100% fill rate.
        self.demo_logs = [demo_log(t0=1000.0, t1=1000.01), demo_log(t0=2000.0, t1=2000.01)]
        # Live: 50ms latency, +5 bps adverse slippage, 90% fill rate.
        self.live_logs = [live_log(t0=1000.0, t1=1000.05), live_log(t0=2000.0, t1=2000.05)]

    def test_realism_assessment_score_calculation(self):
        result = DemoRealismAssessor(self.demo_logs, self.live_logs).assess_realism(2.5)

        # lat = 10/50 = 0.2 ; slip = exp(-(5-0)/10) = 0.60653066 ; fill = 0.9/1.0 = 0.9
        # R = 0.30(0.2) + 0.40(0.60653066) + 0.30(0.9) = 0.57261226
        self.assertAlmostEqual(result.realism_score, 0.57261226, places=7)
        self.assertAlmostEqual(result.adjusted_sharpe_ratio, 2.5 * 0.57261226, places=7)
        self.assertAlmostEqual(result.mean_demo_latency_ms, 10.0, places=1)
        self.assertAlmostEqual(result.mean_live_latency_ms, 50.0, places=1)
        self.assertAlmostEqual(result.mean_live_slippage_bps, 5.0, places=6)

    def test_perfect_parity_high_score(self):
        matched_live = [
            live_log(fill=150.0, filled=100, t0=1000.0, t1=1000.01),
            live_log(fill=150.0, filled=100, t0=2000.0, t1=2000.01),
        ]
        result = DemoRealismAssessor(self.demo_logs, matched_live).assess_realism(2.0)

        self.assertAlmostEqual(result.realism_score, 1.0, places=7)
        self.assertAlmostEqual(result.adjusted_sharpe_ratio, 2.0, places=7)
        self.assertTrue(result.meets_promotion_threshold)

    # ------------------------------------------------------------------
    # Sharpe discount direction (regression: a haircut improved a losing strategy)
    # ------------------------------------------------------------------

    def test_negative_demo_sharpe_is_not_improved_by_the_discount(self):
        result = DemoRealismAssessor(self.demo_logs, self.live_logs).assess_realism(-1.0)

        # Previously -1.0 * 0.5726 = -0.5726, i.e. the discount made it look better.
        self.assertEqual(result.adjusted_sharpe_ratio, -1.0)
        self.assertTrue(any("not positive" in w for w in result.warnings))

    def test_zero_demo_sharpe_is_unchanged(self):
        result = DemoRealismAssessor(self.demo_logs, self.live_logs).assess_realism(0.0)
        self.assertEqual(result.adjusted_sharpe_ratio, 0.0)

    def test_sharpe_argument_is_required_and_must_be_finite(self):
        assessor = DemoRealismAssessor(self.demo_logs, self.live_logs)
        with self.assertRaises(TypeError):
            assessor.assess_realism()          # no fabricated default
        with self.assertRaises(ValueError):
            assessor.assess_realism(float("nan"))
        with self.assertRaises(ValueError):
            assessor.assess_realism(float("inf"))

    # ------------------------------------------------------------------
    # Signed slippage (regression: abs() let price improvement cancel adverse cost)
    # ------------------------------------------------------------------

    def test_demo_price_improvement_widens_the_gap_instead_of_cancelling_it(self):
        # Demo fills 7.5c BETTER than arrival on a BUY => -5 bps (price improvement).
        improved_demo = [demo_log(fill=149.925, t0=1000.0, t1=1000.05),
                         demo_log(fill=149.925, t0=2000.0, t1=2000.05)]
        result = DemoRealismAssessor(improved_demo, self.live_logs).assess_realism(2.0)

        self.assertAlmostEqual(result.mean_demo_slippage_bps, -5.0, places=6)
        self.assertAlmostEqual(result.mean_live_slippage_bps, 5.0, places=6)
        # lat = 50/50 = 1.0 ; slip = exp(-(5-(-5))/10) = exp(-1) = 0.36787944 ; fill = 0.9
        # R = 0.30(1.0) + 0.40(0.36787944) + 0.30(0.9) = 0.71715178
        self.assertAlmostEqual(result.realism_score, 0.71715178, places=7)
        self.assertFalse(result.meets_promotion_threshold)

    def test_sell_side_slippage_is_signed_by_direction(self):
        # On a SELL, filling BELOW arrival is the adverse case.
        sell_live = [live_log(fill=149.925, side="SELL", t0=1000.0, t1=1000.05),
                     live_log(fill=149.925, side="SELL", t0=2000.0, t1=2000.05)]
        sell_demo = [demo_log(side="SELL", t0=1000.0, t1=1000.01),
                     demo_log(side="SELL", t0=2000.0, t1=2000.01)]
        result = DemoRealismAssessor(sell_demo, sell_live).assess_realism(2.0)

        self.assertAlmostEqual(result.mean_live_slippage_bps, 5.0, places=6)

    def test_demo_more_adverse_than_live_is_not_penalised(self):
        # A conservative demo (worse slippage than live) should not be punished.
        harsh_demo = [demo_log(fill=150.15, t0=1000.0, t1=1000.05),
                      demo_log(fill=150.15, t0=2000.0, t1=2000.05)]
        result = DemoRealismAssessor(harsh_demo, self.live_logs).assess_realism(2.0)

        # slip term saturates at 1.0; lat = 1.0; fill = 0.9 => R = 0.3 + 0.4 + 0.27
        self.assertAlmostEqual(result.realism_score, 0.97, places=7)

    # ------------------------------------------------------------------
    # Fail-closed validation (regression: degenerate data scored as parity)
    # ------------------------------------------------------------------

    def test_transposed_demo_and_live_logs_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            DemoRealismAssessor(self.live_logs, self.demo_logs)
        self.assertIn("transposed", str(ctx.exception))

    def test_non_finite_and_non_positive_prices_are_rejected(self):
        for bad in (float("nan"), float("inf"), 0.0, -1.0):
            with self.assertRaises(ValueError):
                DemoRealismAssessor(self.demo_logs, [live_log(arrival=bad)])

    def test_zero_live_latency_is_rejected_not_scored_as_perfect(self):
        with self.assertRaises(ValueError) as ctx:
            DemoRealismAssessor(
                self.demo_logs, [live_log(t0=1000.0, t1=1000.0)]
            ).assess_realism(2.0)
        self.assertIn("instantaneous", str(ctx.exception))

    def test_fill_time_before_submission_is_rejected(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [live_log(t0=1000.0, t1=999.0)])

    def test_overfill_and_negative_fill_are_rejected(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [live_log(req=100, filled=101)])
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [live_log(req=100, filled=-1)])

    def test_invalid_side_is_rejected(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [live_log(side="LONG")])

    def test_boolean_is_not_accepted_as_a_numeric_field(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [live_log(arrival=True)])

    def test_empty_log_lists_are_rejected(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor([], self.live_logs)
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, [])

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, self.live_logs, slippage_decay_bps=0.0)
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, self.live_logs, promotion_threshold=1.5)
        with self.assertRaises(ValueError):
            DemoRealismAssessor(self.demo_logs, self.live_logs, min_samples=0)

    # ------------------------------------------------------------------
    # Sample sufficiency and promotion threshold
    # ------------------------------------------------------------------

    def test_small_samples_are_flagged_as_insufficient(self):
        result = DemoRealismAssessor(self.demo_logs, self.live_logs).assess_realism(2.0)

        self.assertEqual((result.n_demo, result.n_live), (2, 2))
        self.assertFalse(result.is_sample_sufficient)
        self.assertTrue(any("Sample size below" in w for w in result.warnings))

    def test_sufficient_sample_clears_the_flag(self):
        result = DemoRealismAssessor(
            self.demo_logs * 15, self.live_logs * 15, min_samples=30
        ).assess_realism(2.0)
        self.assertTrue(result.is_sample_sufficient)
        self.assertEqual(result.warnings, ())

    def test_mismatched_symbol_sets_are_flagged(self):
        other_symbol_live = [live_log(symbol="TSLA", t0=1000.0, t1=1000.05),
                             live_log(symbol="TSLA", t0=2000.0, t1=2000.05)]
        result = DemoRealismAssessor(self.demo_logs, other_symbol_live).assess_realism(2.0)
        self.assertTrue(any("Symbol sets differ" in w for w in result.warnings))

    def test_matched_symbol_sets_are_not_flagged(self):
        result = DemoRealismAssessor(
            self.demo_logs * 15, self.live_logs * 15
        ).assess_realism(2.0)
        self.assertFalse(any("Symbol sets differ" in w for w in result.warnings))

    def test_promotion_threshold_is_inclusive_at_the_boundary(self):
        exact = 0.57261226
        result = DemoRealismAssessor(
            self.demo_logs, self.live_logs, promotion_threshold=exact
        ).assess_realism(2.0)
        self.assertTrue(result.meets_promotion_threshold)

    def test_slippage_decay_is_configurable(self):
        # Doubling the decay scale halves the exponent: exp(-5/20) = 0.77880078
        result = DemoRealismAssessor(
            self.demo_logs, self.live_logs, slippage_decay_bps=20.0
        ).assess_realism(2.0)
        # R = 0.30(0.2) + 0.40(0.77880078) + 0.30(0.9) = 0.64152031
        self.assertAlmostEqual(result.realism_score, 0.64152031, places=7)


if __name__ == "__main__":
    unittest.main()
