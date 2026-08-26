import logging
import unittest
from message_rate_limit_vs_latency_tradeoff_tuning import (
    MessageRateLatencyTunerEngine, TuningConfig, MarketState, TuningReport,
    STATUS_DIRECT_PASS, STATUS_TUNING_APPLIED, STATUS_TARGET_UNREACHABLE
)

# Silence audit logging during assertions; behaviour is asserted on the report, not the log.
logging.getLogger("message_rate_limit_vs_latency_tradeoff_tuning").addHandler(logging.NullHandler())
logging.getLogger("message_rate_limit_vs_latency_tradeoff_tuning").propagate = False


class TestMessageRateLatencyTunerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MessageRateLatencyTunerEngine()

    def test_rate_limit_tuning_applied(self):
        # Exchange session limit = 500 MPS, Target Safety = 80% (400 MPS Target)
        # 100 ticks/sec * 5 quoting pairs = 500 MPS Unthrottled (> 400 MPS Target)
        # Optimal Reprice Delay = (5 / 400) * 1000 = 12.5 ms -> RATE_LIMIT_TUNING_APPLIED!
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0, min_reprice_delay_ms=1.0)
        state = MarketState(ticks_per_sec=100.0, price_volatility_bps=5.0, active_quoting_pairs=5)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, STATUS_TUNING_APPLIED)
        self.assertTrue(report.is_tuning_required)
        self.assertTrue(report.is_target_achievable)
        self.assertEqual(report.unthrottled_message_rate_mps, 500.0)
        self.assertEqual(report.target_safety_limit_mps, 400.0)
        self.assertEqual(report.recommended_reprice_delay_ms, 12.5)
        self.assertEqual(report.projected_message_rate_mps, 400.0)

    def test_direct_pass_no_tuning_required(self):
        # Unthrottled = 10 ticks/sec * 2 pairs = 20 MPS <= 400 MPS Target -> DIRECT_PASS!
        cfg = TuningConfig("AAPL", exchange_max_mps=500.0, target_safety_buffer_pct=80.0, min_reprice_delay_ms=1.0)
        state = MarketState(ticks_per_sec=10.0, price_volatility_bps=2.0, active_quoting_pairs=2)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, STATUS_DIRECT_PASS)
        self.assertFalse(report.is_tuning_required)
        self.assertEqual(report.recommended_reprice_delay_ms, 1.0)

    def test_reprice_delay_rounds_up_so_projected_rate_never_exceeds_target(self):
        # Regression: rounding the delay to NEAREST 2dp pushed the realised rate back
        # above the safety limit. Target = 375 * 0.80 = 300.0 MPS exactly.
        # Required delay = 1000 * 1 / 300 = 3.3333...ms.
        #   Round-to-nearest -> 3.33ms -> 1000/3.33  = 300.30 MPS  (BREACHES the 300 target)
        #   Round-up         -> 3.34ms -> 1000/3.34  = 299.40 MPS  (compliant)
        cfg = TuningConfig("CL", exchange_max_mps=375.0, target_safety_buffer_pct=80.0)
        state = MarketState(ticks_per_sec=400.0, price_volatility_bps=3.0, active_quoting_pairs=1)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.target_safety_limit_mps, 300.0)
        self.assertEqual(report.recommended_reprice_delay_ms, 3.34)
        self.assertEqual(report.projected_message_rate_mps, 299.4)
        self.assertLessEqual(report.projected_message_rate_mps, report.target_safety_limit_mps)
        self.assertTrue(report.is_target_achievable)

    def test_target_unreachable_when_delay_ceiling_binds(self):
        # Regression: when the required delay exceeds max_reprice_delay_ms the delay was
        # silently capped and the report still said RATE_LIMIT_TUNING_APPLIED, while the
        # projected rate (2000 MPS) was 4x the exchange ceiling (500 MPS) -> session kill.
        # Required delay = (1000 pairs / 400 MPS) * 1000 = 2500ms, capped at 500ms.
        # Rate at the cap = 1000 / 0.5s = 2000 MPS.
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0,
                           max_reprice_delay_ms=500.0)
        state = MarketState(ticks_per_sec=100.0, price_volatility_bps=5.0, active_quoting_pairs=1000)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, STATUS_TARGET_UNREACHABLE)
        self.assertFalse(report.is_target_achievable)
        self.assertTrue(report.is_tuning_required)
        self.assertEqual(report.recommended_reprice_delay_ms, 500.0)
        self.assertEqual(report.projected_message_rate_mps, 2000.0)
        self.assertGreater(report.projected_message_rate_mps, report.target_safety_limit_mps)
        self.assertIn("DO NOT DEPLOY", report.audit_notes)

    def test_baseline_session_flow_consumes_message_budget(self):
        # MiFID II RTS 6 Art. 15(2): every order sent to the venue counts toward the
        # firm's message limit, so co-resident flow on the same session must be deducted.
        # Quote traffic alone = 50 ticks * 2 pairs = 100 MPS, which is under the 400 MPS
        # target -- but with 380 MPS of hedge/cancel flow the session needs throttling.
        # Remaining budget = 400 - 380 = 20 MPS -> delay = (2 / 20) * 1000 = 100ms.
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0)
        shared = MarketState(ticks_per_sec=50.0, price_volatility_bps=4.0,
                             active_quoting_pairs=2, baseline_session_mps=380.0)
        dedicated = MarketState(ticks_per_sec=50.0, price_volatility_bps=4.0,
                                active_quoting_pairs=2)

        shared_report = self.engine.tune_quote_reprice_parameters(cfg, shared)
        dedicated_report = self.engine.tune_quote_reprice_parameters(cfg, dedicated)

        # Same quote traffic, opposite verdicts -- driven purely by the shared session flow.
        self.assertEqual(dedicated_report.status, STATUS_DIRECT_PASS)
        self.assertEqual(shared_report.status, STATUS_TUNING_APPLIED)
        self.assertEqual(shared_report.recommended_reprice_delay_ms, 100.0)
        # 2 pairs / 0.1s = 20 MPS of quotes + 380 MPS baseline = 400 MPS total.
        self.assertEqual(shared_report.projected_message_rate_mps, 400.0)
        self.assertEqual(shared_report.baseline_session_mps, 380.0)
        self.assertTrue(shared_report.is_target_achievable)

    def test_baseline_flow_alone_exceeding_budget_is_unreachable(self):
        # 450 MPS of other flow already exceeds the 400 MPS target: no repricing delay
        # can rescue the session. Must report UNREACHABLE, not divide by a <= 0 budget.
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0,
                           max_reprice_delay_ms=500.0)
        state = MarketState(ticks_per_sec=10.0, price_volatility_bps=4.0,
                            active_quoting_pairs=2, baseline_session_mps=450.0)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, STATUS_TARGET_UNREACHABLE)
        self.assertFalse(report.is_target_achievable)
        # Slowest permitted quoting: 2 pairs / 0.5s = 4 MPS, plus 450 MPS baseline.
        self.assertEqual(report.recommended_reprice_delay_ms, 500.0)
        self.assertEqual(report.projected_message_rate_mps, 454.0)

    def test_throttle_cap_does_not_manufacture_ticks(self):
        # A 1ms floor permits 2000 MPS, but only 20 MPS of ticks actually arrive.
        # The projection must report arrivals, not throttle capacity.
        cfg = TuningConfig("AAPL", exchange_max_mps=500.0, min_reprice_delay_ms=1.0)
        state = MarketState(ticks_per_sec=10.0, price_volatility_bps=2.0, active_quoting_pairs=2)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.projected_message_rate_mps, 20.0)

    def test_zero_tick_rate_is_a_direct_pass(self):
        # A dormant symbol emits no reprice traffic; it must not be throttled or rejected.
        cfg = TuningConfig("ILLIQ", exchange_max_mps=500.0)
        state = MarketState(ticks_per_sec=0.0, price_volatility_bps=0.0, active_quoting_pairs=2)

        report = self.engine.tune_quote_reprice_parameters(cfg, state)

        self.assertEqual(report.status, STATUS_DIRECT_PASS)
        self.assertEqual(report.unthrottled_message_rate_mps, 0.0)
        self.assertEqual(report.projected_message_rate_mps, 0.0)
        self.assertEqual(report.adverse_selection_exposure_score, 0.0)

    def test_adverse_selection_exposure_score_is_delay_times_volatility(self):
        # Independently derived: delay 12.5ms * 5.0 bps = 62.5 (units: bps-milliseconds).
        cfg = TuningConfig("ES", exchange_max_mps=500.0, target_safety_buffer_pct=80.0)
        report = self.engine.tune_quote_reprice_parameters(
            cfg, MarketState(ticks_per_sec=100.0, price_volatility_bps=5.0, active_quoting_pairs=5))
        self.assertEqual(report.recommended_reprice_delay_ms, 12.5)
        self.assertEqual(report.adverse_selection_exposure_score, 62.5)

        # The score must rise with volatility at an unchanged delay: 12.5 * 20.0 = 250.0.
        volatile = self.engine.tune_quote_reprice_parameters(
            cfg, MarketState(ticks_per_sec=100.0, price_volatility_bps=20.0, active_quoting_pairs=5))
        self.assertEqual(volatile.recommended_reprice_delay_ms, 12.5)
        self.assertEqual(volatile.adverse_selection_exposure_score, 250.0)
        self.assertGreater(volatile.adverse_selection_exposure_score,
                           report.adverse_selection_exposure_score)

    def test_invalid_config_is_rejected_at_construction(self):
        # A malformed budget must raise, never yield a report that approves an unsafe rate.
        # Pre-fix, buffer_pct=0.0 raised ZeroDivisionError and buffer_pct=-50.0 produced a
        # negative target with a 2000 MPS "tuned" projection reported as compliant.
        invalid_cases = {
            "zero buffer pct": dict(target_safety_buffer_pct=0.0),
            "negative buffer pct": dict(target_safety_buffer_pct=-50.0),
            "buffer pct above 100": dict(target_safety_buffer_pct=150.0),
            "nan buffer pct": dict(target_safety_buffer_pct=float("nan")),
            "zero exchange max": dict(exchange_max_mps=0.0),
            "negative exchange max": dict(exchange_max_mps=-500.0),
            "infinite exchange max": dict(exchange_max_mps=float("inf")),
            "zero min delay": dict(min_reprice_delay_ms=0.0),
            "negative min delay": dict(min_reprice_delay_ms=-1.0),
            "max delay below min delay": dict(min_reprice_delay_ms=10.0, max_reprice_delay_ms=5.0),
            "negative price threshold": dict(price_threshold_bps=-2.0),
        }
        for label, kwargs in invalid_cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    TuningConfig("ES", **kwargs)

        with self.subTest(case="empty symbol"):
            with self.assertRaises(ValueError):
                TuningConfig("   ")

    def test_invalid_market_state_is_rejected_at_construction(self):
        # Pre-fix, a NaN tick rate silently returned DIRECT_PASS_NO_TUNING_REQUIRED,
        # approving unthrottled quoting off corrupt market data.
        invalid_cases = {
            "nan tick rate": dict(ticks_per_sec=float("nan")),
            "infinite tick rate": dict(ticks_per_sec=float("inf")),
            "negative tick rate": dict(ticks_per_sec=-10.0),
            "nan volatility": dict(price_volatility_bps=float("nan")),
            "negative volatility": dict(price_volatility_bps=-1.0),
            "zero quoting pairs": dict(active_quoting_pairs=0),
            "negative quoting pairs": dict(active_quoting_pairs=-2),
            "fractional quoting pairs": dict(active_quoting_pairs=2.5),
            "negative baseline mps": dict(baseline_session_mps=-1.0),
            "nan baseline mps": dict(baseline_session_mps=float("nan")),
        }
        base = dict(ticks_per_sec=10.0, price_volatility_bps=2.0, active_quoting_pairs=2)
        for label, override in invalid_cases.items():
            with self.subTest(case=label):
                kwargs = dict(base)
                kwargs.update(override)
                with self.assertRaises(ValueError):
                    MarketState(**kwargs)

    def test_projected_rate_never_exceeds_target_when_reported_achievable(self):
        # Core invariant sweep: whenever the engine reports the target as achievable,
        # the projected session rate must actually sit at or below the safety limit,
        # and the recommended delay must respect the configured bounds.
        for exchange_max in (100.0, 375.0, 500.0, 1000.0):
            for buffer_pct in (50.0, 80.0, 99.0, 100.0):
                for pairs in (1, 2, 5, 50):
                    for ticks in (0.0, 7.0, 100.0, 5000.0):
                        for baseline in (0.0, 13.0, 100.0):
                            cfg = TuningConfig("SWEEP", exchange_max_mps=exchange_max,
                                               target_safety_buffer_pct=buffer_pct,
                                               min_reprice_delay_ms=1.0,
                                               max_reprice_delay_ms=500.0)
                            state = MarketState(ticks_per_sec=ticks, price_volatility_bps=3.0,
                                                active_quoting_pairs=pairs,
                                                baseline_session_mps=baseline)
                            report = self.engine.tune_quote_reprice_parameters(cfg, state)
                            with self.subTest(exchange_max=exchange_max, buffer_pct=buffer_pct,
                                              pairs=pairs, ticks=ticks, baseline=baseline):
                                self.assertGreaterEqual(report.recommended_reprice_delay_ms,
                                                        cfg.min_reprice_delay_ms)
                                self.assertLessEqual(report.recommended_reprice_delay_ms,
                                                     cfg.max_reprice_delay_ms)
                                self.assertLessEqual(report.target_safety_limit_mps,
                                                     cfg.exchange_max_mps)
                                if report.is_target_achievable:
                                    self.assertLessEqual(report.projected_message_rate_mps,
                                                         report.target_safety_limit_mps)
                                    self.assertNotEqual(report.status, STATUS_TARGET_UNREACHABLE)
                                else:
                                    self.assertEqual(report.status, STATUS_TARGET_UNREACHABLE)
                                    self.assertGreater(report.projected_message_rate_mps,
                                                       report.target_safety_limit_mps)

    def test_report_is_returned_type(self):
        cfg = TuningConfig("ES")
        report = self.engine.tune_quote_reprice_parameters(
            cfg, MarketState(ticks_per_sec=10.0, price_volatility_bps=2.0, active_quoting_pairs=2))
        self.assertIsInstance(report, TuningReport)


if __name__ == '__main__':
    unittest.main()
