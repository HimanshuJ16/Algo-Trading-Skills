"""
Unit tests for the strategy underperformance remediation decision tree.

Expected values for the Sharpe standard error are derived independently of the
implementation: from Lo (2002) Table 1 at q = 1, and from hand-evaluated square roots
for the annualized cases. Tests named ``test_regression_*`` each fail against the
pre-fix behaviour recorded in their docstring.
"""
import logging
import math
import unittest
from dataclasses import replace

from strategy_underperformance_remediation_decision_tree import (
    RemediationAction,
    StrategyRemediationReport,
    StrategyUnderperformanceRemediationEngine,
    UnderperformanceTriageMetrics,
    Z_95,
    annualized_sharpe_standard_error,
)


def healthy_metrics(**overrides) -> UnderperformanceTriageMetrics:
    """A payload that clears every node, so one override isolates one branch."""
    base = dict(
        strategy_id="BASE_STRAT",
        live_sharpe=1.4,
        backtest_sharpe=1.8,
        peer_benchmark_sharpe=1.2,
        realized_slippage_bps=2.0,
        expected_alpha_bps=30.0,
        is_data_feed_healthy=True,
        is_alpha_hypothesis_valid=True,
    )
    base.update(overrides)
    return UnderperformanceTriageMetrics(**base)


class TestAnnualizedSharpeStandardError(unittest.TestCase):
    """SE(SR_ann) = sqrt((q + SR^2/2)/T), Lo (2002) FAJ 58(4) Eq. 17-18."""

    def test_reproduces_lo_2002_table_1_at_unit_frequency(self):
        # Lo (2002) Table 1, T = 60: SE = 0.188 at SR = 1.50, 0.303 at SR = 3.00.
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(1.50, 60, periods_per_year=1), 0.188,
            places=3)
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(3.00, 60, periods_per_year=1), 0.303,
            places=3)

    def test_annualized_standard_error_matches_hand_evaluation(self):
        # sqrt((252 + 1.0^2 / 2) / 60) = sqrt(4.2083333...) = 2.0514223...
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(1.0, 60), 2.0514223, places=6)
        # sqrt((252 + 0) / 2520) = sqrt(0.1) = 0.3162278...
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(0.0, 2520), 0.3162278, places=6)

    def test_standard_error_shrinks_with_sample_size(self):
        self.assertLess(
            annualized_sharpe_standard_error(1.0, 1000),
            annualized_sharpe_standard_error(1.0, 100))

    def test_rejects_invalid_arguments(self):
        for kwargs in (
            dict(sharpe_annualized=float("nan"), n_observations=60),
            dict(sharpe_annualized=float("inf"), n_observations=60),
            dict(sharpe_annualized=1.0, n_observations=0),
            dict(sharpe_annualized=1.0, n_observations=-5),
            dict(sharpe_annualized=1.0, n_observations=1.5),
            dict(sharpe_annualized=1.0, n_observations=True),
            dict(sharpe_annualized=1.0, n_observations=60, periods_per_year=0.0),
            dict(sharpe_annualized=1.0, n_observations=60, periods_per_year=-252.0),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    annualized_sharpe_standard_error(**kwargs)


class TestMetricsValidation(unittest.TestCase):

    def test_regression_nan_sharpe_is_rejected_not_routed(self):
        """Pre-fix: a NaN live_sharpe failed every '<' comparison and fell through to
        MAINTAIN_TRADING — a corrupt payload returned 'no remediation required'."""
        for corrupt in (float("nan"), float("inf"), float("-inf")):
            for f in ("live_sharpe", "backtest_sharpe", "peer_benchmark_sharpe",
                      "realized_slippage_bps", "expected_alpha_bps"):
                with self.subTest(field=f, value=corrupt):
                    with self.assertRaises(ValueError):
                        healthy_metrics(**{f: corrupt})

    def test_regression_non_positive_expected_alpha_is_rejected(self):
        """Pre-fix: `expected_alpha_bps > 0` guarded the ratio, so a strategy with zero
        or negative expected alpha skipped Node 2 entirely and was routed to
        RECALIBRATE_MODEL_PARAMETERS however large its slippage was."""
        for alpha in (0.0, -5.0):
            with self.subTest(expected_alpha_bps=alpha):
                with self.assertRaises(ValueError) as ctx:
                    healthy_metrics(expected_alpha_bps=alpha,
                                    realized_slippage_bps=40.0)
                self.assertIn("expected_alpha_bps", str(ctx.exception))

    def test_regression_signed_slippage_convention_is_rejected(self):
        """Pre-fix: a caller reporting cost as a negative number produced a negative
        ratio, which never exceeds the limit, silently disarming Node 2."""
        with self.assertRaises(ValueError) as ctx:
            healthy_metrics(realized_slippage_bps=-25.0)
        self.assertIn("positive cost magnitude", str(ctx.exception))

    def test_regression_truthy_string_flag_is_rejected(self):
        """Pre-fix: is_alpha_hypothesis_valid="False" is truthy, so a JSON payload with
        a stringified boolean cleared Node 1 and kept a dead strategy trading."""
        for f in ("is_alpha_hypothesis_valid", "is_data_feed_healthy"):
            for bad in ("False", "true", 0, 1, None):
                with self.subTest(field=f, value=bad):
                    with self.assertRaises(ValueError):
                        healthy_metrics(**{f: bad})

    def test_rejects_unattributable_strategy_id(self):
        for bad in ("", "   ", None, 123):
            with self.subTest(strategy_id=bad):
                with self.assertRaises(ValueError):
                    healthy_metrics(strategy_id=bad)

    def test_rejects_invalid_observation_count(self):
        for bad in (0, -1, 1.5, True, "60"):
            with self.subTest(live_observation_count=bad):
                with self.assertRaises(ValueError):
                    healthy_metrics(live_observation_count=bad)

    def test_accepts_zero_slippage_and_omitted_observation_count(self):
        m = healthy_metrics(realized_slippage_bps=0.0)
        self.assertEqual(m.realized_slippage_bps, 0.0)
        self.assertIsNone(m.live_observation_count)


class TestEngineConstruction(unittest.TestCase):

    def test_rejects_peer_bar_stricter_than_own_mandate(self):
        with self.assertRaises(ValueError):
            StrategyUnderperformanceRemediationEngine(
                min_healthy_sharpe=0.5, min_peer_sharpe=1.0)

    def test_rejects_invalid_thresholds(self):
        for kwargs in (
            dict(min_healthy_sharpe=float("nan")),
            dict(max_slippage_alpha_ratio=0.0),
            dict(max_slippage_alpha_ratio=-0.5),
            dict(max_slippage_alpha_ratio=float("inf")),
            dict(min_peer_sharpe=float("nan")),
            dict(min_live_observations=0),
            dict(min_live_observations=1.5),
            dict(min_live_observations=True),
            dict(periods_per_year=0.0),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    StrategyUnderperformanceRemediationEngine(**kwargs)

    def test_equal_thresholds_are_permitted(self):
        engine = StrategyUnderperformanceRemediationEngine(
            min_healthy_sharpe=0.75, min_peer_sharpe=0.75)
        self.assertEqual(engine.min_peer_sharpe, 0.75)

    def test_rejects_wrong_payload_type(self):
        engine = StrategyUnderperformanceRemediationEngine()
        with self.assertRaises(ValueError):
            engine.evaluate_remediation({"strategy_id": "X"})


class TestTriageRouting(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyUnderperformanceRemediationEngine(
            min_healthy_sharpe=1.0,
            max_slippage_alpha_ratio=0.50,
            min_peer_sharpe=0.50,
        )

    def test_node_1_decommission_outranks_every_other_signal(self):
        # Perfect execution, healthy peers, Sharpe above mandate — hypothesis is dead.
        report = self.engine.evaluate_remediation(healthy_metrics(
            strategy_id="BROKEN_EDGE_STRAT",
            live_sharpe=2.5,
            is_alpha_hypothesis_valid=False,
        ))
        self.assertEqual(report.recommended_action,
                         RemediationAction.MANDATORY_STRATEGY_DECOMMISSION)
        self.assertTrue(report.is_decommissioned)
        self.assertTrue(report.is_capital_reduced)
        self.assertEqual(report.decisive_node, "NODE_1_HYPOTHESIS_FAILURE")

    def test_node_1_warns_when_judged_on_a_broken_feed(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            is_alpha_hypothesis_valid=False, is_data_feed_healthy=False))
        self.assertTrue(any("data feed is unhealthy" in w for w in report.warnings))

    def test_node_1_warns_when_peer_group_also_impaired(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            is_alpha_hypothesis_valid=False, peer_benchmark_sharpe=0.1))
        self.assertTrue(any("peer group is also impaired" in w
                            for w in report.warnings))

    def test_node_2_fires_on_unhealthy_feed_even_with_zero_slippage(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            strategy_id="STALE_FEED_STRAT",
            realized_slippage_bps=0.0,
            is_data_feed_healthy=False,
        ))
        self.assertEqual(report.recommended_action,
                         RemediationAction.OPTIMIZE_EXECUTION_AND_DATA)
        self.assertFalse(report.is_decommissioned)
        self.assertTrue(report.is_capital_reduced)
        self.assertTrue(any("Data feed unhealthy" in w for w in report.warnings))

    def test_node_2_fires_on_excessive_slippage(self):
        # 15 bps against 20 bps of alpha = 75% > 50%.
        report = self.engine.evaluate_remediation(healthy_metrics(
            strategy_id="HIGH_SLIPPAGE_STRAT",
            live_sharpe=0.4,
            realized_slippage_bps=15.0,
            expected_alpha_bps=20.0,
        ))
        self.assertEqual(report.recommended_action,
                         RemediationAction.OPTIMIZE_EXECUTION_AND_DATA)
        self.assertAlmostEqual(report.slippage_to_alpha_ratio, 0.75)

    def test_slippage_boundary_is_exclusive(self):
        # Exactly at the limit clears Node 2; a hair above fires it.
        at_limit = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.4, realized_slippage_bps=10.0, expected_alpha_bps=20.0))
        self.assertEqual(at_limit.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)

        above = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.4, realized_slippage_bps=10.01, expected_alpha_bps=20.0))
        self.assertEqual(above.recommended_action,
                         RemediationAction.OPTIMIZE_EXECUTION_AND_DATA)

    def test_node_3_joint_impairment_degrades_capital(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            strategy_id="REGIME_STRAT", live_sharpe=0.3, peer_benchmark_sharpe=0.2))
        self.assertEqual(
            report.recommended_action,
            RemediationAction.TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL)
        self.assertTrue(report.is_capital_reduced)
        self.assertFalse(report.is_decommissioned)
        self.assertEqual(report.decisive_node, "NODE_3_REGIME_SHIFT")

    def test_node_4_recalibrates_against_healthy_peers(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            strategy_id="STALE_PARAM_STRAT", live_sharpe=0.6,
            peer_benchmark_sharpe=1.5))
        self.assertEqual(report.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)
        self.assertFalse(report.is_capital_reduced)
        self.assertAlmostEqual(report.sharpe_gap_vs_peer, -0.9)

    def test_peer_threshold_boundary_selects_node_4(self):
        # Peer exactly at the health floor counts as healthy -> idiosyncratic branch.
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=0.50))
        self.assertEqual(report.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)

        below = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=0.4999))
        self.assertEqual(
            below.recommended_action,
            RemediationAction.TEMPORARY_CAPITAL_DEGRADE_RETAIN_SIGNAL)

    def test_mandate_boundary_is_inclusive(self):
        at_mandate = self.engine.evaluate_remediation(
            healthy_metrics(live_sharpe=1.0, peer_benchmark_sharpe=1.2))
        self.assertEqual(at_mandate.recommended_action,
                         RemediationAction.MAINTAIN_TRADING)

        below = self.engine.evaluate_remediation(
            healthy_metrics(live_sharpe=0.9999, peer_benchmark_sharpe=1.2))
        self.assertEqual(below.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)

    def test_node_4_warns_when_strategy_is_not_behind_its_peers(self):
        """A strategy at 0.95 against peers at 0.55 misses its mandate without
        underperforming its cohort — that is not evidence of parameter drift."""
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.95, peer_benchmark_sharpe=0.55))
        self.assertEqual(report.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)
        self.assertGreaterEqual(report.sharpe_gap_vs_peer, 0.0)
        self.assertTrue(any("at or above its peer benchmark" in w
                            for w in report.warnings))

    def test_healthy_strategy_warns_when_peer_group_is_impaired(self):
        report = self.engine.evaluate_remediation(
            healthy_metrics(live_sharpe=1.6, peer_benchmark_sharpe=0.1))
        self.assertEqual(report.recommended_action,
                         RemediationAction.MAINTAIN_TRADING)
        self.assertTrue(any("valid cohort" in w for w in report.warnings))

    def test_recalibration_carries_the_material_change_caveat(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=1.5))
        self.assertTrue(any("material change" in w for w in report.warnings))


class TestSampleSizeGate(unittest.TestCase):

    def setUp(self):
        self.gated = StrategyUnderperformanceRemediationEngine(
            min_live_observations=60)

    def test_short_window_refuses_to_route(self):
        report = self.gated.evaluate_remediation(healthy_metrics(
            strategy_id="YOUNG_STRAT", live_sharpe=0.2, peer_benchmark_sharpe=1.5,
            live_observation_count=20))
        self.assertEqual(
            report.recommended_action,
            RemediationAction.EXTEND_OBSERVATION_INSUFFICIENT_HISTORY)
        self.assertFalse(report.is_capital_reduced)
        self.assertFalse(report.is_decommissioned)
        self.assertEqual(report.decisive_node, "NODE_2A_INSUFFICIENT_HISTORY")

    def test_gate_does_not_shield_a_dead_hypothesis_or_a_broken_feed(self):
        dead = self.gated.evaluate_remediation(healthy_metrics(
            is_alpha_hypothesis_valid=False, live_observation_count=5))
        self.assertEqual(dead.recommended_action,
                         RemediationAction.MANDATORY_STRATEGY_DECOMMISSION)

        broken = self.gated.evaluate_remediation(healthy_metrics(
            is_data_feed_healthy=False, live_observation_count=5))
        self.assertEqual(broken.recommended_action,
                         RemediationAction.OPTIMIZE_EXECUTION_AND_DATA)

    def test_sufficient_window_routes_normally(self):
        report = self.gated.evaluate_remediation(healthy_metrics(
            live_sharpe=0.2, peer_benchmark_sharpe=1.5, live_observation_count=60))
        self.assertEqual(report.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)

    def test_configured_gate_rejects_a_payload_without_a_count(self):
        with self.assertRaises(ValueError):
            self.gated.evaluate_remediation(healthy_metrics(live_sharpe=0.2))

    def test_ungated_engine_still_accepts_a_payload_without_a_count(self):
        ungated = StrategyUnderperformanceRemediationEngine()
        # Base payload has healthy peers at 1.2, so this routes to Node 4.
        report = ungated.evaluate_remediation(healthy_metrics(live_sharpe=0.2))
        self.assertEqual(report.recommended_action,
                         RemediationAction.RECALIBRATE_MODEL_PARAMETERS)
        self.assertIsNone(report.sharpe_standard_error)


class TestSharpeEvidenceAnnotation(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyUnderperformanceRemediationEngine()

    def test_unknown_sample_size_is_flagged_and_never_conclusive(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=1.5))
        self.assertIsNone(report.sharpe_standard_error)
        self.assertFalse(report.sharpe_evidence_conclusive)
        self.assertTrue(any("live_observation_count not supplied" in w
                            for w in report.warnings))

    def test_short_window_routes_but_is_reported_inconclusive(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=1.5, live_observation_count=60))
        # SE = sqrt((252 + 0.18) / 60) = 2.0501...; |0.6 - 1.0| is far inside 1.96 SE.
        self.assertAlmostEqual(report.sharpe_standard_error, 2.0501, places=4)
        self.assertFalse(report.sharpe_evidence_conclusive)
        self.assertTrue(any("Sharpe evidence inconclusive" in w
                            for w in report.warnings))

    def test_long_window_can_be_conclusive(self):
        # T = 2520 (~10y daily), SR = 0.0: SE = 0.31623, 1.96 SE = 0.6198 < 1.0.
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.0, peer_benchmark_sharpe=1.5, live_observation_count=2520))
        self.assertAlmostEqual(report.sharpe_standard_error, 0.3162278, places=6)
        self.assertTrue(report.sharpe_evidence_conclusive)
        self.assertFalse(any("Sharpe evidence inconclusive" in w
                             for w in report.warnings))

    def test_conclusiveness_matches_an_independent_computation(self):
        m = healthy_metrics(live_sharpe=0.2, peer_benchmark_sharpe=1.5,
                            live_observation_count=1000)
        report = self.engine.evaluate_remediation(m)
        expected_se = math.sqrt((252.0 + 0.2 * 0.2 / 2.0) / 1000.0)
        self.assertAlmostEqual(report.sharpe_standard_error, expected_se, places=12)
        self.assertEqual(report.sharpe_evidence_conclusive,
                         abs(0.2 - 1.0) > Z_95 * expected_se)


class TestAuditTrail(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyUnderperformanceRemediationEngine()

    def test_path_records_cleared_nodes_with_the_decisive_node_last(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, peer_benchmark_sharpe=1.5))
        self.assertIn("NODE_1_HYPOTHESIS_VALID", report.triage_path[0])
        self.assertIn("NODE_2_EXECUTION_CLEARED", report.triage_path[1])
        self.assertIn("NODE_4_PARAMETER_DRIFT", report.triage_path[-1])
        self.assertEqual(report.decisive_node, "NODE_4_PARAMETER_DRIFT")

    def test_unreached_nodes_are_absent_from_the_path(self):
        report = self.engine.evaluate_remediation(
            healthy_metrics(is_alpha_hypothesis_valid=False))
        self.assertEqual(len(report.triage_path), 1)
        self.assertNotIn("NODE_2", " ".join(report.triage_path))

    def test_report_carries_context_metrics(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=0.6, backtest_sharpe=2.1, peer_benchmark_sharpe=1.5,
            realized_slippage_bps=3.0, expected_alpha_bps=30.0))
        self.assertAlmostEqual(report.slippage_to_alpha_ratio, 0.10)
        self.assertAlmostEqual(report.sharpe_gap_vs_backtest, -1.5)
        self.assertAlmostEqual(report.sharpe_gap_vs_peer, -0.9)
        self.assertIn("STRAT", report.audit_notes)

    def test_healthy_report_has_no_blocking_caveats(self):
        report = self.engine.evaluate_remediation(healthy_metrics(
            live_sharpe=1.6, peer_benchmark_sharpe=1.2, live_observation_count=5000))
        self.assertEqual(report.recommended_action, RemediationAction.MAINTAIN_TRADING)
        self.assertEqual(report.warnings, ())
        self.assertIsInstance(report, StrategyRemediationReport)

    def test_decommission_logs_at_error_and_healthy_logs_at_info(self):
        with self.assertLogs(
                "strategy_underperformance_remediation_decision_tree",
                level="INFO") as ctx:
            self.engine.evaluate_remediation(
                healthy_metrics(is_alpha_hypothesis_valid=False))
        self.assertTrue(any(r.levelno == logging.ERROR for r in ctx.records))

        with self.assertLogs(
                "strategy_underperformance_remediation_decision_tree",
                level="INFO") as ctx:
            self.engine.evaluate_remediation(healthy_metrics(
                live_sharpe=1.6, peer_benchmark_sharpe=1.2,
                live_observation_count=5000))
        self.assertTrue(all(r.levelno == logging.INFO for r in ctx.records))

    def test_evaluation_is_deterministic_and_does_not_mutate_input(self):
        m = healthy_metrics(live_sharpe=0.6, peer_benchmark_sharpe=1.5)
        snapshot = replace(m)
        first = self.engine.evaluate_remediation(m)
        second = self.engine.evaluate_remediation(m)
        self.assertEqual(first.recommended_action, second.recommended_action)
        self.assertEqual(first.triage_path, second.triage_path)
        self.assertEqual(first.warnings, second.warnings)
        self.assertEqual(m, snapshot)


if __name__ == "__main__":
    unittest.main()
