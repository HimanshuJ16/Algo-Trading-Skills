import unittest

from meta_strategy_signal_arbitration import (
    STATUS_DEADBAND_SUPPRESSED,
    STATUS_NETTED_ORDER,
    STATUS_VETO_RISK_OFF,
    MetaStrategySignalArbitratorEngine,
    StrategyWeightConfig,
    SubStrategySignal,
)


class TestMetaStrategySignalArbitratorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.05, estimated_transaction_cost_bps=10.0
        )
        self.weights = [
            StrategyWeightConfig("TREND_01", weight=0.50),
            StrategyWeightConfig("MEAN_REV_02", weight=0.50),
        ]

    def _opposing_signals(self, symbol="AAPL"):
        """TREND_01 wants +$100k, MEAN_REV_02 wants -$60k on the same symbol."""
        return [
            SubStrategySignal("TREND_01", symbol, raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", symbol, raw_signal=-0.6, conviction_score=1.0,
                              target_notional_usd=-60000.0),
        ]

    # ---------------------------------------------------------------- netting

    def test_internal_order_netting_and_savings(self):
        # Hand-derived: gross = |100000| + |-60000| = 160000; net = +40000.
        # Netted (never routed) = 160000 - 40000 = 120000.
        # Savings at 10 bps = 120000 * 10/10000 = 120.00.
        report = self.engine.arbitrate_strategy_signals("AAPL", self.weights, self._opposing_signals())

        self.assertEqual(report.status, STATUS_NETTED_ORDER)
        self.assertEqual(report.gross_notional_usd, 160000.0)
        self.assertEqual(report.net_executable_notional_usd, 40000.0)
        self.assertEqual(report.internal_netting_savings_usd, 120.0)
        self.assertFalse(report.is_risk_veto_active)
        self.assertEqual(report.total_strategies_count, 2)

    def test_savings_scale_with_configured_cost_bps(self):
        # Same 120000 netted notional at 25 bps = 120000 * 25/10000 = 300.00.
        engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.05, estimated_transaction_cost_bps=25.0
        )
        report = engine.arbitrate_strategy_signals("AAPL", self.weights, self._opposing_signals())
        self.assertEqual(report.internal_netting_savings_usd, 300.0)

    def test_fully_offsetting_requests_net_to_zero_notional(self):
        # +100k against -100k: nothing needs routing at all, and the whole 200000
        # of gross interest is netted internally. 200000 * 10/10000 = 200.00.
        engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.0, estimated_transaction_cost_bps=10.0
        )
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-1.0, conviction_score=0.5,
                              target_notional_usd=-100000.0),
        ]
        report = engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

        self.assertEqual(report.status, STATUS_NETTED_ORDER)
        self.assertEqual(report.net_executable_notional_usd, 0.0)
        self.assertEqual(report.internal_netting_savings_usd, 200.0)

    # -------------------------------------------------------------- consensus

    def test_consensus_signal_is_conviction_and_weight_averaged(self):
        # Hand-derived: (1.0*1.0*0.5 + (-0.6)*1.0*0.5) / (0.5 + 0.5)
        #             = (0.5 - 0.3) / 1.0 = 0.2
        report = self.engine.arbitrate_strategy_signals("AAPL", self.weights, self._opposing_signals())
        self.assertAlmostEqual(report.consensus_signal, 0.2, places=6)

    def test_consensus_normalises_by_total_weight_not_count(self):
        # Weights deliberately do not sum to 1.0.
        # (0.8*0.5*0.6 + (-0.4)*1.0*0.2) / (0.6 + 0.2)
        #   = (0.24 - 0.08) / 0.8 = 0.16 / 0.8 = 0.2
        weights = [
            StrategyWeightConfig("TREND_01", weight=0.60),
            StrategyWeightConfig("MEAN_REV_02", weight=0.20),
        ]
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=0.8, conviction_score=0.5,
                              target_notional_usd=50000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.4, conviction_score=1.0,
                              target_notional_usd=-20000.0),
        ]
        report = self.engine.arbitrate_strategy_signals("AAPL", weights, signals)
        self.assertAlmostEqual(report.consensus_signal, 0.2, places=6)

    # ------------------------------------------------------------- risk vetos

    def test_risk_off_veto_override(self):
        signals = [
            SubStrategySignal("TREND_01", "TSLA", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=50000.0),
            SubStrategySignal("MEAN_REV_02", "TSLA", raw_signal=-1.0, conviction_score=1.0,
                              target_notional_usd=-50000.0, is_risk_veto=True),
        ]
        report = self.engine.arbitrate_strategy_signals("TSLA", self.weights, signals)

        self.assertEqual(report.status, STATUS_VETO_RISK_OFF)
        self.assertTrue(report.is_risk_veto_active)
        self.assertEqual(report.net_executable_notional_usd, 0.0)

    def test_veto_reports_flat_consensus_not_maximum_short(self):
        # Regression: a veto used to report consensus_signal = -1.0. A downstream
        # sizer reading that would open a maximum-conviction SHORT -- the position
        # the veto exists to prevent. A veto means flat.
        signals = [
            SubStrategySignal("TREND_01", "TSLA", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=50000.0, is_risk_veto=True),
        ]
        report = self.engine.arbitrate_strategy_signals("TSLA", self.weights, signals)

        self.assertEqual(report.status, STATUS_VETO_RISK_OFF)
        self.assertEqual(report.consensus_signal, 0.0)

    def test_veto_outranks_unanimous_high_conviction_alpha(self):
        # The vetoing strategy is the smallest allocation and the only dissenter.
        weights = [
            StrategyWeightConfig("TREND_01", weight=0.90),
            StrategyWeightConfig("MEAN_REV_02", weight=0.05),
            StrategyWeightConfig("RISK_GUARD", weight=0.05),
        ]
        signals = [
            SubStrategySignal("TREND_01", "TSLA", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=900000.0),
            SubStrategySignal("MEAN_REV_02", "TSLA", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=50000.0),
            SubStrategySignal("RISK_GUARD", "TSLA", raw_signal=0.0, conviction_score=0.0,
                              target_notional_usd=0.0, is_risk_veto=True),
        ]
        report = self.engine.arbitrate_strategy_signals("TSLA", weights, signals)

        self.assertEqual(report.status, STATUS_VETO_RISK_OFF)
        self.assertEqual(report.net_executable_notional_usd, 0.0)
        self.assertEqual(report.internal_netting_savings_usd, 0.0)

    # --------------------------------------------------------------- deadband

    def test_deadband_suppresses_micro_signal_churn(self):
        # Consensus = (0.02*1.0*0.5 + 0.02*1.0*0.5) / 1.0 = 0.02, versus a
        # previous consensus of 0.0 -> delta 0.02 < 0.05 threshold.
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=0.02, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=0.02, conviction_score=1.0,
                              target_notional_usd=-60000.0),
        ]
        report = self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

        self.assertEqual(report.status, STATUS_DEADBAND_SUPPRESSED)
        self.assertEqual(report.net_executable_notional_usd, 0.0)
        self.assertAlmostEqual(report.consensus_signal, 0.02, places=6)
        self.assertFalse(report.is_risk_veto_active)

    def test_suppressed_pass_books_no_netting_savings(self):
        # Regression: the suppressed branch used to report the full netting savings
        # for an order it never routed, inflating any TCA savings tally that summed
        # this field. Nothing was routed, so netting avoided nothing on this pass.
        engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.05, estimated_transaction_cost_bps=10.0
        )
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-1.0, conviction_score=1.0,
                              target_notional_usd=-60000.0),
        ]
        report = engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

        self.assertEqual(report.status, STATUS_DEADBAND_SUPPRESSED)
        self.assertEqual(report.gross_notional_usd, 160000.0)
        self.assertEqual(report.internal_netting_savings_usd, 0.0)

    def test_deadband_boundary_is_exclusive(self):
        # Consensus 0.2 against a previous 0.0 is a delta of exactly the threshold.
        # Suppression is strictly "below the threshold", so this must still trade.
        engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.20, estimated_transaction_cost_bps=10.0
        )
        report = engine.arbitrate_strategy_signals(
            "AAPL", self.weights, self._opposing_signals(), current_consensus_signal=0.0
        )
        self.assertEqual(report.status, STATUS_NETTED_ORDER)

        # One notch above the delta and it is suppressed.
        engine_wide = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.25, estimated_transaction_cost_bps=10.0
        )
        suppressed = engine_wide.arbitrate_strategy_signals(
            "AAPL", self.weights, self._opposing_signals(), current_consensus_signal=0.0
        )
        self.assertEqual(suppressed.status, STATUS_DEADBAND_SUPPRESSED)

    def test_deadband_measured_against_supplied_previous_consensus(self):
        # Consensus is 0.2; holding 0.19 already means there is nothing to do.
        report = self.engine.arbitrate_strategy_signals(
            "AAPL", self.weights, self._opposing_signals(), current_consensus_signal=0.19
        )
        self.assertEqual(report.status, STATUS_DEADBAND_SUPPRESSED)

    def test_zero_threshold_disables_suppression(self):
        engine = MetaStrategySignalArbitratorEngine(
            deadband_threshold=0.0, estimated_transaction_cost_bps=10.0
        )
        # Consensus equals the previous consensus exactly: delta 0.0, and with a
        # 0.0 threshold "delta < threshold" is false, so the order still goes.
        report = engine.arbitrate_strategy_signals(
            "AAPL", self.weights, self._opposing_signals(), current_consensus_signal=0.2
        )
        self.assertEqual(report.status, STATUS_NETTED_ORDER)

    # ------------------------------------------------------ input validation

    def test_empty_signal_batch_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.arbitrate_strategy_signals("AAPL", self.weights, [])

    def test_signal_for_another_symbol_is_rejected(self):
        # Regression: an MSFT request used to be netted into the AAPL order,
        # producing an order size no strategy asked for on either symbol.
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "MSFT", raw_signal=-1.0, conviction_score=1.0,
                              target_notional_usd=-60000.0),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)
        self.assertIn("MSFT", str(ctx.exception))

    def test_strategy_without_configured_weight_is_rejected(self):
        # Regression: an unrecognised strategy_id used to default to weight 1.0 --
        # double the configured 0.5 allocations -- which flipped the consensus sign.
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_TYPO", "AAPL", raw_signal=-1.0, conviction_score=1.0,
                              target_notional_usd=-60000.0),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)
        self.assertIn("MEAN_REV_TYPO", str(ctx.exception))

    def test_duplicate_strategy_signal_is_rejected(self):
        # Regression: the same strategy submitting twice used to double its notional
        # and its voting weight.
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
        ]
        with self.assertRaises(ValueError):
            self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

    def test_duplicate_weight_config_is_rejected(self):
        weights = [
            StrategyWeightConfig("TREND_01", weight=0.50),
            StrategyWeightConfig("TREND_01", weight=0.30),
            StrategyWeightConfig("MEAN_REV_02", weight=0.50),
        ]
        with self.assertRaises(ValueError):
            self.engine.arbitrate_strategy_signals("AAPL", weights, self._opposing_signals())

    def test_non_positive_weight_is_rejected(self):
        for bad_weight in (0.0, -0.5):
            weights = [
                StrategyWeightConfig("TREND_01", weight=bad_weight),
                StrategyWeightConfig("MEAN_REV_02", weight=0.50),
            ]
            with self.subTest(weight=bad_weight):
                with self.assertRaises(ValueError):
                    self.engine.arbitrate_strategy_signals(
                        "AAPL", weights, self._opposing_signals()
                    )

    def test_non_finite_inputs_never_reach_an_order(self):
        # Regression: NaN propagated silently. "nan < deadband" is False, so the
        # deadband gate let it through and an order was emitted alongside a NaN
        # consensus; an infinite notional produced an infinite order size.
        for label, bad in (("nan", float("nan")), ("inf", float("inf"))):
            with self.subTest(case=f"raw_signal={label}"):
                signals = [
                    SubStrategySignal("TREND_01", "AAPL", raw_signal=bad, conviction_score=1.0,
                                      target_notional_usd=100000.0),
                    SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.6, conviction_score=1.0,
                                      target_notional_usd=-60000.0),
                ]
                with self.assertRaises(ValueError):
                    self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

            with self.subTest(case=f"target_notional_usd={label}"):
                signals = [
                    SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                                      target_notional_usd=bad),
                    SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.6, conviction_score=1.0,
                                      target_notional_usd=-60000.0),
                ]
                with self.assertRaises(ValueError):
                    self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

            with self.subTest(case=f"conviction_score={label}"):
                signals = [
                    SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=bad,
                                      target_notional_usd=100000.0),
                    SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.6, conviction_score=1.0,
                                      target_notional_usd=-60000.0),
                ]
                with self.assertRaises(ValueError):
                    self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

    def test_non_finite_previous_consensus_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.arbitrate_strategy_signals(
                "AAPL", self.weights, self._opposing_signals(),
                current_consensus_signal=float("nan"),
            )

    def test_out_of_range_signal_and_conviction_are_rejected(self):
        # Regression: raw_signal=5.0 with conviction_score=10.0 produced a consensus
        # of 24.7 in a field documented as bounded to [-1.0, +1.0].
        bad_cases = [
            ("raw_signal_high", 5.0, 1.0),
            ("raw_signal_low", -1.5, 1.0),
            ("conviction_high", 1.0, 10.0),
            ("conviction_negative", 1.0, -0.1),
        ]
        for label, raw, conviction in bad_cases:
            signals = [
                SubStrategySignal("TREND_01", "AAPL", raw_signal=raw, conviction_score=conviction,
                                  target_notional_usd=100000.0),
                SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=-0.6, conviction_score=1.0,
                                  target_notional_usd=-60000.0),
            ]
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.engine.arbitrate_strategy_signals("AAPL", self.weights, signals)

    def test_consensus_stays_within_documented_bounds(self):
        # With inputs validated, the weighted average cannot leave [-1.0, +1.0].
        weights = [
            StrategyWeightConfig("TREND_01", weight=0.90),
            StrategyWeightConfig("MEAN_REV_02", weight=0.10),
        ]
        signals = [
            SubStrategySignal("TREND_01", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
            SubStrategySignal("MEAN_REV_02", "AAPL", raw_signal=1.0, conviction_score=1.0,
                              target_notional_usd=100000.0),
        ]
        report = self.engine.arbitrate_strategy_signals("AAPL", weights, signals)
        self.assertLessEqual(report.consensus_signal, 1.0)
        self.assertGreaterEqual(report.consensus_signal, -1.0)
        self.assertAlmostEqual(report.consensus_signal, 1.0, places=6)

    # ---------------------------------------------------- constructor guards

    def test_constructor_rejects_invalid_configuration(self):
        bad_configs = [
            ("negative_deadband", {"deadband_threshold": -0.1}),
            ("nan_deadband", {"deadband_threshold": float("nan")}),
            ("negative_cost", {"estimated_transaction_cost_bps": -10.0}),
            ("inf_cost", {"estimated_transaction_cost_bps": float("inf")}),
        ]
        for label, kwargs in bad_configs:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    MetaStrategySignalArbitratorEngine(**kwargs)


if __name__ == '__main__':
    unittest.main()
