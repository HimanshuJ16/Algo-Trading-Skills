"""
Unit tests for the rebalancing frequency optimizer.

Expected values are derived by hand from the formulas in ``references/standards.md``,
never by re-running the implementation's own arithmetic. The destination test
reproduces the worked "200/175" example published in Vanguard's December 2024
target-date rebalancing paper (62% equity breach -> rebalanced to 61.75%).
"""
import unittest

from rebalancing_frequency_optimization_cost_vs_drift import (
    Config, Engine, RebalancingFrequencyOptimizerEngine,
    AssetWeight, RebalanceOptimizationReport,
)


class TestConfigValidation(unittest.TestCase):

    def test_defaults_are_internally_consistent(self):
        RebalancingFrequencyOptimizerEngine(Config())  # must not raise

    def test_destination_at_or_beyond_band_edge_rejected(self):
        # A destination >= the threshold means a breach implies a zero-size trade.
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(
                Config(max_drift_threshold_pct=0.02, destination_drift_pct=0.02))
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(
                Config(max_drift_threshold_pct=0.02, destination_drift_pct=0.03))

    def test_negative_destination_rejected(self):
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(Config(destination_drift_pct=-0.01))

    def test_non_positive_horizon_rejected(self):
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(Config(drift_horizon_periods=0.0))

    def test_min_trade_threshold_above_band_rejected(self):
        # A min-trade gate wider than the band makes the net-benefit rule unreachable.
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(
                Config(max_drift_threshold_pct=0.02, min_trade_threshold_pct=0.05))

    def test_negative_lambda_rejected(self):
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(Config(drift_penalty_lambda=-1.0))

    def test_non_finite_lambda_rejected(self):
        with self.assertRaises(ValueError):
            RebalancingFrequencyOptimizerEngine(Config(drift_penalty_lambda=float("nan")))


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=100.0, max_drift_threshold_pct=0.05))

    def test_nan_weight_raises_rather_than_silently_reporting_no_rebalance(self):
        # Regression: NaN comparisons are all False, so an unvalidated NaN drift used to
        # slide through every threshold test and return NO_REBALANCE_WITHIN_BAND with
        # NaN costs. It must raise instead.
        assets = [
            AssetWeight("SPY", 0.50, float("nan"), 600000.0),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_infinite_asset_value_raises(self):
        assets = [
            AssetWeight("SPY", 0.50, 0.60, float("inf")),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_zero_portfolio_value_raises_rather_than_falling_back_to_one_dollar(self):
        # Regression: the engine used to substitute a $1.00 portfolio value and emit a
        # report of nonsense trade sizes.
        assets = [
            AssetWeight("SPY", 0.50, 0.50, 0.0),
            AssetWeight("TLT", 0.50, 0.50, 0.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_target_weights_not_summing_to_one_raises(self):
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 600000.0),
            AssetWeight("TLT", 0.30, 0.40, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_current_weights_not_summing_to_one_raises(self):
        # Buys and sells would not net to zero.
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 600000.0),
            AssetWeight("TLT", 0.50, 0.45, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_weight_disagreeing_with_asset_value_raises(self):
        # Drift is measured from the weight but trades are sized from the value; an
        # inconsistent snapshot silently produces wrongly sized orders.
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 400000.0),
            AssetWeight("TLT", 0.50, 0.40, 600000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_duplicate_symbol_raises(self):
        assets = [
            AssetWeight("SPY", 0.25, 0.30, 300000.0),
            AssetWeight("SPY", 0.25, 0.20, 200000.0),
            AssetWeight("TLT", 0.50, 0.50, 500000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_negative_cost_bps_raises(self):
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 600000.0, fee_rate_bps=-1.0),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)

    def test_empty_symbol_raises(self):
        assets = [
            AssetWeight("", 0.50, 0.60, 600000.0),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.optimize_rebalancing(assets)


class TestDecisionRules(unittest.TestCase):

    def setUp(self):
        self.config = Config(enabled=True, drift_penalty_lambda=100.0,
                             max_drift_threshold_pct=0.05)
        self.legacy_engine = Engine(self.config)
        self.engine = RebalancingFrequencyOptimizerEngine(self.config)

    def test_legacy_init_and_run(self):
        self.assertTrue(self.legacy_engine.config.enabled)
        self.assertTrue(self.legacy_engine.run())

    def test_disabled_engine_short_circuits(self):
        engine = RebalancingFrequencyOptimizerEngine(Config(enabled=False))
        report = engine.optimize_rebalancing(
            [AssetWeight("SPY", 0.50, 0.60, 600000.0),
             AssetWeight("TLT", 0.50, 0.40, 400000.0)])
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertFalse(report.rebalance_recommended)

    def test_no_assets(self):
        report = self.engine.optimize_rebalancing([])
        self.assertEqual(report.status, "NO_ASSETS")
        self.assertEqual(report.proposed_trades, [])

    def test_no_rebalance_within_band(self):
        # 0.5% drift: below the 5% band edge and below the 1% min-trade gate, so the
        # net-benefit rule cannot fire even though drift cost far exceeds trade cost.
        assets = [
            AssetWeight("SPY", 0.50, 0.505, 505000.0),
            AssetWeight("TLT", 0.50, 0.495, 495000.0),
        ]
        report = self.engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "NO_REBALANCE_WITHIN_BAND")
        self.assertFalse(report.rebalance_recommended)
        self.assertEqual(len(report.proposed_trades), 0)
        # drift cost = 100 * 1 * (0.005^2 + 0.005^2) * 1e6 = 5,000
        self.assertAlmostEqual(report.total_drift_cost_usd, 5000.0, places=2)
        # The costed alternative is still reported: 2 legs * 0.5% * $1m * 10bps = $10
        self.assertAlmostEqual(report.total_transaction_cost_usd, 10.0, places=2)
        self.assertGreater(report.net_economic_benefit_usd, 0.0)

    def test_rebalance_triggered_max_drift_breached(self):
        # 10% drift on a 50/50 target, V = $1,000,000, 5 + 5 = 10 bps per leg.
        #   drift cost = 100 * 1 * (0.10^2 + 0.10^2) * 1e6      = $2,000,000
        #   tx cost    = 2 * (0.10 * 1e6) * 10/10_000           = $200
        #   net        =                                          $1,999,800
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 600000.0),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        report = self.engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")
        self.assertTrue(report.rebalance_recommended)
        self.assertEqual(len(report.proposed_trades), 2)
        self.assertAlmostEqual(report.total_drift_cost_usd, 2_000_000.0, places=2)
        self.assertAlmostEqual(report.total_transaction_cost_usd, 200.0, places=2)
        self.assertAlmostEqual(report.net_economic_benefit_usd, 1_999_800.0, places=2)

        spy = next(t for t in report.proposed_trades if t.symbol == "SPY")
        tlt = next(t for t in report.proposed_trades if t.symbol == "TLT")
        self.assertEqual(spy.action, "SELL")
        self.assertEqual(tlt.action, "BUY")
        self.assertAlmostEqual(spy.trade_amount_usd, 100000.0, places=2)
        self.assertAlmostEqual(tlt.trade_amount_usd, 100000.0, places=2)
        # Full rebalance to target: no residual drift, destination is the target itself.
        self.assertAlmostEqual(spy.residual_drift_pct, 0.0, places=6)
        self.assertAlmostEqual(report.destination_drift_pct, 0.0, places=6)

    def test_band_edge_is_inclusive(self):
        # Drift of exactly 5.00% is a breach: the band edge is >=, not >.
        assets = [
            AssetWeight("SPY", 0.50, 0.55, 550000.0),
            AssetWeight("TLT", 0.50, 0.45, 450000.0),
        ]
        report = self.engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")

    def test_net_benefit_rule_fires_between_gate_and_band(self):
        # 2% drift: inside the 5% band, above the 1% gate, and drift cost dominates.
        #   drift cost = 100 * (0.02^2 * 2) * 1e6 = $80,000
        #   tx cost    = 2 * 0.02 * 1e6 * 0.001   = $40
        assets = [
            AssetWeight("SPY", 0.50, 0.52, 520000.0),
            AssetWeight("TLT", 0.50, 0.48, 480000.0),
        ]
        report = self.engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_NET_BENEFIT")
        self.assertTrue(report.rebalance_recommended)
        self.assertAlmostEqual(report.total_drift_cost_usd, 80000.0, places=2)
        self.assertAlmostEqual(report.total_transaction_cost_usd, 40.0, places=2)

    def test_net_benefit_rule_blocked_when_transaction_cost_dominates(self):
        # Same 2% drift but lambda = 0.01 and 500 bps of round-trip friction.
        #   drift cost = 0.01 * (0.02^2 * 2) * 1e6          = $8
        #   tx cost    = 2 * (0.02 * 1e6) * 500/10_000      = $2,000
        engine = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=0.01, max_drift_threshold_pct=0.05))
        assets = [
            AssetWeight("SPY", 0.50, 0.52, 520000.0, 250.0, 250.0),
            AssetWeight("TLT", 0.50, 0.48, 480000.0, 250.0, 250.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "NO_REBALANCE_WITHIN_BAND")
        self.assertAlmostEqual(report.total_drift_cost_usd, 8.0, places=2)
        self.assertAlmostEqual(report.total_transaction_cost_usd, 2000.0, places=2)
        self.assertLess(report.net_economic_benefit_usd, 0.0)

    def test_drift_horizon_scales_the_penalty_not_the_trade_cost(self):
        assets = [
            AssetWeight("SPY", 0.50, 0.52, 520000.0),
            AssetWeight("TLT", 0.50, 0.48, 480000.0),
        ]
        one = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=100.0)).optimize_rebalancing(assets)
        two = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=100.0,
                   drift_horizon_periods=2.0)).optimize_rebalancing(assets)
        self.assertAlmostEqual(two.total_drift_cost_usd,
                               2.0 * one.total_drift_cost_usd, places=2)
        self.assertAlmostEqual(two.total_transaction_cost_usd,
                               one.total_transaction_cost_usd, places=2)


class TestDestinationBoundary(unittest.TestCase):
    """Trade-to-boundary policy (Leland 1999; Vanguard 200/175)."""

    def test_reproduces_vanguard_200_175_worked_example(self):
        # Vanguard, "The rebalancing edge" (Dec 2024): a 60/40 portfolio breaching a
        # 200 bps threshold at 62% equity is rebalanced to the 175 bps destination,
        # i.e. to 61.75% equity -- not back to 60%.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.02,
            min_trade_threshold_pct=0.01,
            destination_drift_pct=0.0175,
        ))
        assets = [
            AssetWeight("EQUITY", 0.60, 0.62, 620000.0),
            AssetWeight("BOND", 0.40, 0.38, 380000.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")

        eq = next(t for t in report.proposed_trades if t.symbol == "EQUITY")
        bd = next(t for t in report.proposed_trades if t.symbol == "BOND")

        # Post-trade equity weight = 0.60 + 0.0175 = 0.6175 exactly.
        self.assertAlmostEqual(0.60 + eq.residual_drift_pct / 100.0, 0.6175, places=6)
        self.assertAlmostEqual(0.40 + bd.residual_drift_pct / 100.0, 0.3825, places=6)
        self.assertAlmostEqual(report.destination_drift_pct, 1.75, places=6)

        # Traded weight per leg = 200 - 175 = 25 bps -> $2,500 on $1m.
        self.assertEqual(eq.action, "SELL")
        self.assertEqual(bd.action, "BUY")
        self.assertAlmostEqual(eq.trade_amount_usd, 2500.0, places=2)
        self.assertAlmostEqual(bd.trade_amount_usd, 2500.0, places=2)

        # Transaction cost is priced on the trade actually placed, not on raw drift:
        # 2 * 2500 * 10/10_000 = $5, versus $40 for a full rebalance to target.
        self.assertAlmostEqual(report.total_transaction_cost_usd, 5.0, places=2)

    def test_full_rebalance_of_same_book_costs_eight_times_as_much(self):
        assets = [
            AssetWeight("EQUITY", 0.60, 0.62, 620000.0),
            AssetWeight("BOND", 0.40, 0.38, 380000.0),
        ]
        base = Config(drift_penalty_lambda=100.0, max_drift_threshold_pct=0.02,
                      min_trade_threshold_pct=0.01)
        to_target = RebalancingFrequencyOptimizerEngine(base).optimize_rebalancing(assets)
        to_edge = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=100.0, max_drift_threshold_pct=0.02,
                   min_trade_threshold_pct=0.01,
                   destination_drift_pct=0.0175)).optimize_rebalancing(assets)
        # 200 bps traded vs 25 bps traded.
        self.assertAlmostEqual(to_target.total_transaction_cost_usd, 40.0, places=2)
        self.assertAlmostEqual(
            to_target.total_transaction_cost_usd,
            8.0 * to_edge.total_transaction_cost_usd, places=2)

    def test_uniform_shrink_keeps_post_trade_weights_summing_to_one(self):
        # Three assets with asymmetric drifts. Clamping each leg independently to the
        # destination would leave residual drifts summing to +0.01 and post-trade
        # weights summing to 1.01; the uniform shrink keeps the budget identity.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            destination_drift_pct=0.03,
        ))
        assets = [
            AssetWeight("A", 0.50, 0.56, 560000.0),
            AssetWeight("B", 0.30, 0.28, 280000.0),
            AssetWeight("C", 0.20, 0.16, 160000.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")

        residuals = {t.symbol: t.residual_drift_pct / 100.0 for t in report.proposed_trades}
        # shrink = 0.03 / 0.06 = 0.5 -> residuals +0.03, -0.01, -0.02
        self.assertAlmostEqual(residuals["A"], 0.03, places=6)
        self.assertAlmostEqual(residuals["B"], -0.01, places=6)
        self.assertAlmostEqual(residuals["C"], -0.02, places=6)
        self.assertAlmostEqual(sum(residuals.values()), 0.0, places=9)

        post_trade = {"A": 0.50 + residuals["A"], "B": 0.30 + residuals["B"],
                      "C": 0.20 + residuals["C"]}
        self.assertAlmostEqual(sum(post_trade.values()), 1.0, places=9)

        # Sells and buys still net to zero notionally.
        sells = sum(t.trade_amount_usd for t in report.proposed_trades if t.action == "SELL")
        buys = sum(t.trade_amount_usd for t in report.proposed_trades if t.action == "BUY")
        self.assertAlmostEqual(sells, buys, places=2)
        self.assertAlmostEqual(sells, 30000.0, places=2)


class TestMinimumLegFilter(unittest.TestCase):

    def test_negligible_leg_is_suppressed_and_reported(self):
        # A 5% breach on one sleeve must not drag a 2 bps leg along with it -- the
        # proportional cost model underprices such an order.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            min_leg_trade_pct=0.005,
        ))
        assets = [
            AssetWeight("A", 0.50, 0.56, 560000.0),
            AssetWeight("B", 0.30, 0.2998, 299800.0),
            AssetWeight("C", 0.20, 0.1402, 140200.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_TRIGGERED_MAX_DRIFT")
        self.assertEqual(report.suppressed_legs, ["B"])
        self.assertEqual({t.symbol for t in report.proposed_trades}, {"A", "C"})

        # Suppression leaves a residual imbalance the caller must settle in cash:
        # $60,000 sold vs $59,800 bought.
        sells = sum(t.trade_amount_usd for t in report.proposed_trades if t.action == "SELL")
        buys = sum(t.trade_amount_usd for t in report.proposed_trades if t.action == "BUY")
        self.assertAlmostEqual(sells, 60000.0, places=2)
        self.assertAlmostEqual(buys, 59800.0, places=2)
        self.assertAlmostEqual(sells - buys, 200.0, places=2)

        # The suppressed leg is not charged for either.
        # (0.06 + 0.0598) * 1e6 * 10/10_000 = $119.80
        self.assertAlmostEqual(report.total_transaction_cost_usd, 119.80, places=2)

    def test_zero_drift_leg_is_not_reported_as_suppressed(self):
        engine = RebalancingFrequencyOptimizerEngine(
            Config(drift_penalty_lambda=100.0, max_drift_threshold_pct=0.05))
        assets = [
            AssetWeight("A", 0.40, 0.46, 460000.0),
            AssetWeight("B", 0.40, 0.34, 340000.0),
            AssetWeight("C", 0.20, 0.20, 200000.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.suppressed_legs, [])
        self.assertEqual({t.symbol for t in report.proposed_trades}, {"A", "B"})

    def test_min_leg_notional_filter(self):
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            min_leg_trade_pct=0.0,
            min_leg_trade_usd=1000.0,
        ))
        assets = [
            AssetWeight("A", 0.50, 0.56, 560000.0),
            AssetWeight("B", 0.30, 0.2998, 299800.0),
            AssetWeight("C", 0.20, 0.1402, 140200.0),
        ]
        report = engine.optimize_rebalancing(assets)
        # B trades $200 < $1,000 minimum.
        self.assertEqual(report.suppressed_legs, ["B"])


class TestBlockedTriggers(unittest.TestCase):
    """A trigger must never fire alongside an empty trade list."""

    def test_mandate_breach_with_every_leg_suppressed_is_escalated_not_hidden(self):
        # 10% drift breaches a 5% band, but an absurd minimum-leg threshold filters out
        # every leg. Reporting rebalance_recommended=True with zero trades would tell
        # the caller a breach is being remediated when nothing will trade; reporting
        # NO_REBALANCE_WITHIN_BAND would hide the breach entirely.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            min_leg_trade_pct=0.99,
        ))
        assets = [
            AssetWeight("SPY", 0.50, 0.60, 600000.0),
            AssetWeight("TLT", 0.50, 0.40, 400000.0),
        ]
        with self.assertLogs(
            "rebalancing_frequency_optimization_cost_vs_drift", level="WARNING"
        ) as captured:
            report = engine.optimize_rebalancing(assets)

        self.assertEqual(report.status, "REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES")
        self.assertFalse(report.rebalance_recommended)
        self.assertEqual(report.proposed_trades, [])
        self.assertEqual(sorted(report.suppressed_legs), ["SPY", "TLT"])
        # The band breach is still visible in the report and escalated in the log.
        self.assertAlmostEqual(report.max_single_drift_pct, 10.0, places=4)
        self.assertIn("MANDATE BREACH UNREMEDIATED", "\n".join(captured.output))

    def test_drift_already_inside_destination_yields_no_trades_and_no_recommendation(self):
        # Net benefit is positive at 2% drift, but the destination boundary is 3%: the
        # book is already inside where a rebalance would leave it, so there is nothing
        # to trade and nothing to recommend.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            min_trade_threshold_pct=0.01,
            destination_drift_pct=0.03,
        ))
        assets = [
            AssetWeight("SPY", 0.50, 0.52, 520000.0),
            AssetWeight("TLT", 0.50, 0.48, 480000.0),
        ]
        report = engine.optimize_rebalancing(assets)
        self.assertEqual(report.status, "REBALANCE_BLOCKED_NO_ELIGIBLE_TRADES")
        self.assertFalse(report.rebalance_recommended)
        self.assertEqual(report.proposed_trades, [])
        self.assertAlmostEqual(report.total_transaction_cost_usd, 0.0, places=6)
        self.assertNotIn("MANDATE BREACH", report.audit_notes)

    def test_recommended_implies_non_empty_trades_across_a_drift_sweep(self):
        # Invariant: rebalance_recommended is true only when there is something to place.
        engine = RebalancingFrequencyOptimizerEngine(Config(
            drift_penalty_lambda=100.0,
            max_drift_threshold_pct=0.05,
            destination_drift_pct=0.02,
        ))
        for step in range(0, 41):
            d = step / 1000.0  # 0% .. 4% drift
            assets = [
                AssetWeight("SPY", 0.50, 0.50 + d, (0.50 + d) * 1_000_000.0),
                AssetWeight("TLT", 0.50, 0.50 - d, (0.50 - d) * 1_000_000.0),
            ]
            report = engine.optimize_rebalancing(assets)
            if report.rebalance_recommended:
                self.assertTrue(report.proposed_trades,
                                f"recommended with no trades at drift {d}")
            else:
                self.assertEqual(report.proposed_trades, [],
                                 f"trades emitted without a recommendation at drift {d}")


class TestReportShape(unittest.TestCase):

    def test_report_is_a_dataclass_with_independent_suppressed_lists(self):
        engine = RebalancingFrequencyOptimizerEngine(Config())
        a = engine.optimize_rebalancing([])
        b = engine.optimize_rebalancing([])
        self.assertIsInstance(a, RebalanceOptimizationReport)
        a.suppressed_legs.append("X")
        self.assertEqual(b.suppressed_legs, [])


if __name__ == '__main__':
    unittest.main()
