import unittest
from anomaly_rollback_trigger import (
    Anomaly,
    AnomalySeverity,
    AutomatedRollbackEngine,
    DeploymentHealthMetrics,
    RollbackDecision,
    RollbackEvaluationResult,
    RollbackThresholdConfig,
)


class _FakeClock:
    """Deterministic monotonic clock for cooldown tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _healthy_metrics() -> DeploymentHealthMetrics:
    return DeploymentHealthMetrics(
        latency_ms=12.5,
        http_5xx_error_rate=0.001,
        order_reject_rate=0.005,
    )


def _bad_latency_metrics() -> DeploymentHealthMetrics:
    return DeploymentHealthMetrics(
        latency_ms=150.0,
        http_5xx_error_rate=0.0,
        order_reject_rate=0.0,
    )


def _bad_order_reject_metrics() -> DeploymentHealthMetrics:
    return DeploymentHealthMetrics(
        latency_ms=15.0,
        http_5xx_error_rate=0.0,
        order_reject_rate=0.05,
    )


def _all_bad_metrics() -> DeploymentHealthMetrics:
    return DeploymentHealthMetrics(
        latency_ms=200.0,
        http_5xx_error_rate=0.10,
        order_reject_rate=0.15,
    )


class TestConfigValidation(unittest.TestCase):
    def test_default_policy_values(self):
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
        )
        self.assertEqual(cfg.consecutive_failures_required, 2)
        self.assertEqual(cfg.rollback_cooldown_seconds, 300.0)
        self.assertEqual(cfg.max_rollbacks_per_deployment, 1)

    def test_negative_latency_rejected(self):
        with self.assertRaises(ValueError):
            RollbackThresholdConfig(-1.0, 0.01, 0.02)

    def test_error_rate_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            RollbackThresholdConfig(50.0, 1.5, 0.02)
        with self.assertRaises(ValueError):
            RollbackThresholdConfig(50.0, 0.01, -0.1)

    def test_consecutive_failures_below_one_rejected(self):
        with self.assertRaises(ValueError):
            RollbackThresholdConfig(50.0, 0.01, 0.02, consecutive_failures_required=0)

    def test_negative_cooldown_rejected(self):
        with self.assertRaises(ValueError):
            RollbackThresholdConfig(50.0, 0.01, 0.02, rollback_cooldown_seconds=-5.0)

    def test_zero_max_rollbacks_allowed(self):
        # 0 means detection-only / manual-rollback mode -- valid config.
        cfg = RollbackThresholdConfig(50.0, 0.01, 0.02, max_rollbacks_per_deployment=0)
        self.assertEqual(cfg.max_rollbacks_per_deployment, 0)


class TestMetricsValidation(unittest.TestCase):
    def test_negative_latency_rejected(self):
        with self.assertRaises(ValueError):
            DeploymentHealthMetrics(-1.0, 0.0, 0.0)

    def test_rate_above_one_rejected(self):
        with self.assertRaises(ValueError):
            DeploymentHealthMetrics(10.0, 1.5, 0.0)
        with self.assertRaises(ValueError):
            DeploymentHealthMetrics(10.0, 0.0, 2.0)


class TestAutomatedRollbackEngine(unittest.TestCase):
    def setUp(self):
        self.clock = _FakeClock()
        self.config = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=2,
            rollback_cooldown_seconds=300.0,
            max_rollbacks_per_deployment=1,
        )
        self.engine = AutomatedRollbackEngine(self.config, clock=self.clock)

    def test_healthy_deployment(self):
        result = self.engine.evaluate_metrics(_healthy_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.HEALTHY)
        self.assertEqual(len(result.anomalies_detected), 0)
        self.assertEqual(len(result.anomalies), 0)
        self.assertEqual(result.consecutive_failures, 0)

    def test_latency_anomaly_detected_with_structured_record(self):
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        # Not yet confirmed (needs 2 consecutive).
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.CONFIRMING)
        self.assertEqual(len(result.anomalies), 1)
        self.assertEqual(result.anomalies[0].metric_name, "latency_ms")
        self.assertEqual(result.anomalies[0].severity, AnomalySeverity.WARNING)
        self.assertTrue(any("Latency Spike" in m for m in result.anomalies_detected))

    def test_trading_anomaly_detected_critical_severity(self):
        result = self.engine.evaluate_metrics(_bad_order_reject_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(len(result.anomalies), 1)
        self.assertEqual(result.anomalies[0].metric_name, "order_reject_rate")
        self.assertEqual(result.anomalies[0].severity, AnomalySeverity.CRITICAL)
        self.assertTrue(any("Trading Anomaly" in m for m in result.anomalies_detected))

    def test_multiple_anomalies_in_one_snapshot(self):
        result = self.engine.evaluate_metrics(_all_bad_metrics())
        self.assertEqual(len(result.anomalies), 3)
        severities = {a.severity for a in result.anomalies}
        self.assertEqual(severities, {AnomalySeverity.WARNING, AnomalySeverity.CRITICAL})

    def test_healthy_sample_resets_consecutive_streak(self):
        self.engine.evaluate_metrics(_bad_latency_metrics())  # streak = 1
        result = self.engine.evaluate_metrics(_healthy_metrics())  # reset
        self.assertEqual(result.consecutive_failures, 0)
        self.assertEqual(result.decision, RollbackDecision.HEALTHY)
        # Next bad sample must require a fresh streak.
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertEqual(result.decision, RollbackDecision.CONFIRMING)
        self.assertEqual(result.consecutive_failures, 1)


class TestConsecutiveFailureConfirmation(unittest.TestCase):
    """Flapping prevention: a single transient breach must NOT trigger rollback."""

    def setUp(self):
        self.clock = _FakeClock()
        self.config = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=2,
            rollback_cooldown_seconds=300.0,
            max_rollbacks_per_deployment=1,
        )
        self.engine = AutomatedRollbackEngine(self.config, clock=self.clock)

    def test_single_breach_does_not_rollback(self):
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.CONFIRMING)
        self.assertEqual(result.consecutive_failures, 1)

    def test_two_consecutive_breaches_trigger_rollback(self):
        self.engine.evaluate_metrics(_bad_latency_metrics())
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertTrue(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.ROLLBACK)
        self.assertEqual(result.consecutive_failures, 2)
        self.assertEqual(result.rollbacks_issued, 1)

    def test_interleaved_healthy_resets_streak(self):
        self.engine.evaluate_metrics(_bad_latency_metrics())  # 1
        self.engine.evaluate_metrics(_healthy_metrics())      # reset
        result = self.engine.evaluate_metrics(_bad_latency_metrics())  # 1 again
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.CONFIRMING)

    def test_immediate_rollback_when_required_is_one(self):
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=1,
        )
        engine = AutomatedRollbackEngine(cfg, clock=_FakeClock())
        result = engine.evaluate_metrics(_bad_latency_metrics())
        self.assertTrue(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.ROLLBACK)


class TestRollbackLoopPrevention(unittest.TestCase):
    """Cooldown + max-rollbacks guard against cascading rollback loops."""

    def setUp(self):
        self.clock = _FakeClock()
        self.config = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=2,
            rollback_cooldown_seconds=300.0,
            max_rollbacks_per_deployment=1,
        )
        self.engine = AutomatedRollbackEngine(self.config, clock=self.clock)

    def _confirm_rollback(self):
        self.engine.evaluate_metrics(_bad_latency_metrics())
        self.engine.evaluate_metrics(_bad_latency_metrics())

    def test_second_rollback_within_cooldown_suppressed(self):
        self._confirm_rollback()
        # Re-confirm breaches while still in cooldown.
        self.engine.evaluate_metrics(_bad_latency_metrics())
        self.engine.evaluate_metrics(_bad_latency_metrics())
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.SUPPRESSED)
        self.assertGreater(result.remaining_cooldown_s, 0.0)

    def test_rollback_cap_reached_escalates_to_human(self):
        self._confirm_rollback()
        # Advance past the cooldown so the cap (not cooldown) is the binding guard.
        self.clock.advance(301.0)
        self.engine.evaluate_metrics(_bad_latency_metrics())
        self.engine.evaluate_metrics(_bad_latency_metrics())
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.SUPPRESSED)
        self.assertEqual(result.rollbacks_issued, 1)

    def test_cooldown_expires_allows_new_rollback_when_cap_not_hit(self):
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=2,
            rollback_cooldown_seconds=300.0,
            max_rollbacks_per_deployment=3,
        )
        clock = _FakeClock()
        engine = AutomatedRollbackEngine(cfg, clock=clock)
        # First rollback.
        engine.evaluate_metrics(_bad_latency_metrics())
        first = engine.evaluate_metrics(_bad_latency_metrics())
        self.assertTrue(first.should_rollback)
        # Advance past cooldown, re-confirm -> second rollback allowed.
        clock.advance(301.0)
        engine.evaluate_metrics(_bad_latency_metrics())
        second = engine.evaluate_metrics(_bad_latency_metrics())
        self.assertTrue(second.should_rollback)
        self.assertEqual(second.rollbacks_issued, 2)

    def test_zero_cap_is_detection_only(self):
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=1,
            max_rollbacks_per_deployment=0,
        )
        engine = AutomatedRollbackEngine(cfg, clock=_FakeClock())
        result = engine.evaluate_metrics(_bad_latency_metrics())
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.SUPPRESSED)


class TestMarketOpenVolatilityGuard(unittest.TestCase):
    """A real market event must not roll back a healthy deployment."""

    def setUp(self):
        self.clock = _FakeClock()
        self.config = RollbackThresholdConfig(
            max_latency_ms=50.0,
            max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02,
            consecutive_failures_required=2,
            max_rollbacks_per_deployment=1,
        )
        self.engine = AutomatedRollbackEngine(self.config, clock=self.clock)

    def test_market_open_breach_is_suppressed_and_not_counted(self):
        metrics = DeploymentHealthMetrics(
            latency_ms=150.0,
            http_5xx_error_rate=0.0,
            order_reject_rate=0.10,
            market_open_volatility=True,
        )
        result = self.engine.evaluate_metrics(metrics)
        self.assertFalse(result.should_rollback)
        self.assertEqual(result.decision, RollbackDecision.SUPPRESSED)
        self.assertEqual(len(result.anomalies), 2)
        # Suppressed breaches must not advance the consecutive streak.
        self.assertEqual(result.consecutive_failures, 0)

    def test_persistent_breach_after_volatility_window_still_rolls_back(self):
        volatile = DeploymentHealthMetrics(
            latency_ms=150.0, http_5xx_error_rate=0.0, order_reject_rate=0.0,
            market_open_volatility=True,
        )
        self.engine.evaluate_metrics(volatile)
        # Volatility window passes; breach persists -> now counted.
        self.engine.evaluate_metrics(_bad_latency_metrics())
        result = self.engine.evaluate_metrics(_bad_latency_metrics())
        self.assertTrue(result.should_rollback)


class TestReset(unittest.TestCase):
    def test_reset_clears_per_deployment_state(self):
        clock = _FakeClock()
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0, max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02, consecutive_failures_required=2,
            rollback_cooldown_seconds=300.0, max_rollbacks_per_deployment=1,
        )
        engine = AutomatedRollbackEngine(cfg, clock=clock)
        engine.evaluate_metrics(_bad_latency_metrics())
        engine.evaluate_metrics(_bad_latency_metrics())
        self.assertEqual(engine._rollbacks_issued, 1)
        engine.reset()
        self.assertEqual(engine._consecutive_failures, 0)
        self.assertEqual(engine._rollbacks_issued, 0)
        # After reset a fresh confirmation cycle is required.
        result = engine.evaluate_metrics(_bad_latency_metrics())
        self.assertEqual(result.decision, RollbackDecision.CONFIRMING)


class TestResultStructure(unittest.TestCase):
    def test_anomaly_record_carries_threshold_and_observed_value(self):
        cfg = RollbackThresholdConfig(
            max_latency_ms=50.0, max_http_5xx_error_rate=0.01,
            max_order_reject_rate=0.02, consecutive_failures_required=1,
        )
        engine = AutomatedRollbackEngine(cfg, clock=_FakeClock())
        result = engine.evaluate_metrics(_bad_latency_metrics())
        a = result.anomalies[0]
        self.assertEqual(a.threshold, 50.0)
        self.assertEqual(a.observed_value, 150.0)
        self.assertIsInstance(a, Anomaly)

    def test_result_is_returned_even_when_healthy(self):
        engine = AutomatedRollbackEngine(
            RollbackThresholdConfig(50.0, 0.01, 0.02), clock=_FakeClock(),
        )
        result = engine.evaluate_metrics(_healthy_metrics())
        self.assertIsInstance(result, RollbackEvaluationResult)
        self.assertEqual(result.remaining_cooldown_s, 0.0)


if __name__ == "__main__":
    unittest.main()
