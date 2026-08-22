import unittest

from clock_drift_monitor import (
    CAT_MAX_DIVERGENCE_US,
    ClockDriftMonitor,
    ClockTelemetryError,
    MIFID_HFT_MAX_DIVERGENCE_US,
    MIFID_OTHER_ALGO_MAX_DIVERGENCE_US,
    MonitorStatus,
    PtpState,
    offset_us_from_ptp4l_ns,
)


class FakeClock:
    """Injectable monotonic clock so holdover/staleness tests need no sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class MonitorTestCase(unittest.TestCase):
    def setUp(self):
        self.kill_switch_triggered = False
        self.trigger_reason = None
        self.trigger_count = 0
        self.clock = FakeClock()

    def _kill_switch(self, reason: str) -> None:
        self.kill_switch_triggered = True
        self.trigger_reason = reason
        self.trigger_count += 1

    def make_monitor(self, **kwargs) -> ClockDriftMonitor:
        params = dict(
            kill_switch_callback=self._kill_switch,
            warning_threshold_us=50,
            critical_threshold_us=100,
            monotonic_clock=self.clock,
        )
        params.update(kwargs)
        return ClockDriftMonitor(**params)


class TestThresholdEvaluation(MonitorTestCase):
    def test_healthy_offset(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=12.5, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.HEALTHY)
        self.assertFalse(self.kill_switch_triggered)

    def test_warning_offset_uses_absolute_value(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=-65.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

    def test_critical_drift_breach(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=105.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertTrue(self.kill_switch_triggered)
        self.assertEqual(self.trigger_reason, "DRIFT_BREACH_105.0us")

    def test_negative_drift_breach_also_halts(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=-140.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "DRIFT_BREACH_140.0us")

    def test_exactly_at_critical_threshold_halts(self):
        # RTS 25 states a *maximum* divergence; the monitor halts on the
        # boundary rather than treating exactly-100us as still compliant.
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=100.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertTrue(self.kill_switch_triggered)

    def test_just_below_critical_threshold_is_warning_only(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=99.999, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

    def test_exactly_at_warning_threshold_warns(self):
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=50.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.WARNING)

    def test_default_critical_threshold_is_the_rts25_hft_limit(self):
        monitor = ClockDriftMonitor(kill_switch_callback=self._kill_switch)
        self.assertEqual(monitor.critical_threshold_us, 100.0)
        self.assertEqual(MIFID_HFT_MAX_DIVERGENCE_US, 100.0)

    def test_sourced_regulatory_constants(self):
        # Independently sourced: RTS 25 Annex Table 2 (100us HFT / 1ms other),
        # FINRA Rule 6820 (50ms). Guards against the constants being "tidied"
        # into a single universal number.
        self.assertEqual(MIFID_OTHER_ALGO_MAX_DIVERGENCE_US, 1_000.0)
        self.assertEqual(CAT_MAX_DIVERGENCE_US, 50_000.0)
        self.assertEqual(CAT_MAX_DIVERGENCE_US / MIFID_HFT_MAX_DIVERGENCE_US, 500.0)


class TestStateEvaluation(MonitorTestCase):
    def test_unlocked_state_breach(self):
        # Even at zero offset, an unlocked servo means the offset is meaningless.
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=0.0, ptp_state=PtpState.UNLOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "PTP_UNLOCKED")

    def test_holdover_halts_immediately_under_default_zero_grace(self):
        # Regression: HOLDOVER used to fall through to the offset check and be
        # classified HEALTHY at a small offset, so a lost grandmaster was
        # invisible until the drift itself breached.
        monitor = self.make_monitor()
        status = monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertTrue(self.kill_switch_triggered)
        self.assertTrue(self.trigger_reason.startswith("HOLDOVER_EXPIRED_"))

    def test_holdover_within_grace_is_warning_not_healthy(self):
        monitor = self.make_monitor(holdover_grace_s=30.0)
        status = monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.assertEqual(status, MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

    def test_holdover_halts_when_grace_expires(self):
        monitor = self.make_monitor(holdover_grace_s=30.0)
        monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.clock.advance(29.0)
        self.assertEqual(
            monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER),
            MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

        self.clock.advance(1.5)  # total 30.5s > 30s grace
        status = monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "HOLDOVER_EXPIRED_30.500s")

    def test_relock_clears_holdover_timer(self):
        # A host that flaps in and out of holdover must not accumulate elapsed
        # time across separate holdover episodes.
        monitor = self.make_monitor(holdover_grace_s=30.0)
        monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.clock.advance(25.0)
        monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.LOCKED)
        self.clock.advance(25.0)
        status = monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.HOLDOVER)
        self.assertEqual(status, MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

    def test_offset_breach_during_holdover_still_halts(self):
        monitor = self.make_monitor(holdover_grace_s=3600.0)
        status = monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.HOLDOVER)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "DRIFT_BREACH_150.0us")

    def test_non_enum_state_rejected(self):
        monitor = self.make_monitor()
        with self.assertRaises(ClockTelemetryError):
            monitor.process_telemetry(offset_us=1.0, ptp_state="LOCKED")


class TestTelemetryValidation(MonitorTestCase):
    def test_nan_offset_is_rejected_not_treated_as_healthy(self):
        # Regression: abs(nan) >= 100 and abs(nan) >= 50 are both False, so an
        # unvalidated NaN reading previously returned HEALTHY.
        monitor = self.make_monitor()
        with self.assertRaises(ClockTelemetryError):
            monitor.process_telemetry(offset_us=float("nan"), ptp_state=PtpState.LOCKED)
        self.assertFalse(self.kill_switch_triggered)
        self.assertEqual(monitor.current_status, MonitorStatus.HEALTHY)

    def test_infinite_offset_is_rejected(self):
        monitor = self.make_monitor()
        with self.assertRaises(ClockTelemetryError):
            monitor.process_telemetry(offset_us=float("inf"), ptp_state=PtpState.LOCKED)

    def test_non_numeric_offset_is_rejected(self):
        monitor = self.make_monitor()
        with self.assertRaises(ClockTelemetryError):
            monitor.process_telemetry(offset_us="105", ptp_state=PtpState.LOCKED)


class TestConfigurationValidation(MonitorTestCase):
    def test_warning_above_critical_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(warning_threshold_us=200, critical_threshold_us=100)

    def test_warning_equal_to_critical_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(warning_threshold_us=100, critical_threshold_us=100)

    def test_non_positive_threshold_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(warning_threshold_us=0)

    def test_nan_threshold_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(critical_threshold_us=float("nan"))

    def test_negative_holdover_grace_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(holdover_grace_s=-1.0)

    def test_non_positive_telemetry_age_rejected(self):
        with self.assertRaises(ValueError):
            self.make_monitor(max_telemetry_age_s=0)

    def test_non_callable_kill_switch_rejected(self):
        with self.assertRaises(TypeError):
            self.make_monitor(kill_switch_callback="halt")


class TestLiveness(MonitorTestCase):
    def test_stale_telemetry_halts(self):
        monitor = self.make_monitor(max_telemetry_age_s=5.0)
        monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.LOCKED)
        self.clock.advance(4.0)
        self.assertEqual(monitor.check_liveness(), MonitorStatus.HEALTHY)
        self.assertFalse(self.kill_switch_triggered)

        self.clock.advance(2.0)  # 6s since last reading
        self.assertEqual(monitor.check_liveness(), MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "TELEMETRY_STALE_6.000s")

    def test_no_telemetry_ever_received_halts(self):
        monitor = self.make_monitor(max_telemetry_age_s=5.0)
        self.assertEqual(monitor.check_liveness(), MonitorStatus.CRITICAL)
        self.assertEqual(self.trigger_reason, "TELEMETRY_NEVER_RECEIVED")

    def test_liveness_noop_when_unconfigured(self):
        monitor = self.make_monitor()
        monitor.process_telemetry(offset_us=1.0, ptp_state=PtpState.LOCKED)
        self.clock.advance(10_000.0)
        self.assertEqual(monitor.check_liveness(), MonitorStatus.HEALTHY)
        self.assertFalse(self.kill_switch_triggered)


class TestLatchAndReset(MonitorTestCase):
    def test_halted_state_persists(self):
        monitor = self.make_monitor()
        monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        self.assertTrue(monitor.trading_halted)

        status = monitor.process_telemetry(offset_us=10.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)

    def test_kill_switch_fires_only_once_while_latched(self):
        monitor = self.make_monitor()
        monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        monitor.process_telemetry(offset_us=200.0, ptp_state=PtpState.LOCKED)
        monitor.process_telemetry(offset_us=0.0, ptp_state=PtpState.UNLOCKED)
        self.assertEqual(self.trigger_count, 1)

    def test_reset_clears_latch_and_requires_attribution(self):
        monitor = self.make_monitor()
        monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        with self.assertRaises(ValueError):
            monitor.reset(operator="", reason="fixed")
        with self.assertRaises(ValueError):
            monitor.reset(operator="ops-oncall", reason="")
        self.assertTrue(monitor.trading_halted)

        monitor.reset(operator="ops-oncall", reason="grandmaster restored, records amended")
        self.assertFalse(monitor.trading_halted)
        self.assertEqual(
            monitor.process_telemetry(offset_us=10.0, ptp_state=PtpState.LOCKED),
            MonitorStatus.HEALTHY)

    def test_reset_restarts_staleness_window(self):
        # A loop that calls check_liveness() before its first read after a
        # reset must not be halted again for never having received telemetry.
        monitor = self.make_monitor(max_telemetry_age_s=5.0)
        monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        self.clock.advance(600.0)
        monitor.reset(operator="ops-oncall", reason="remediated")
        self.assertEqual(monitor.check_liveness(), MonitorStatus.HEALTHY)
        self.assertFalse(monitor.trading_halted)

    def test_failing_kill_switch_still_latches_and_propagates(self):
        def exploding_kill_switch(reason: str) -> None:
            raise RuntimeError("IPC socket to trading engine is down")

        monitor = self.make_monitor(kill_switch_callback=exploding_kill_switch)
        with self.assertRaises(RuntimeError):
            monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        # The halt must survive the callback failure rather than the monitor
        # resuming as if nothing happened.
        self.assertTrue(monitor.trading_halted)
        self.assertEqual(monitor.current_status, MonitorStatus.CRITICAL)


class TestUnitConversion(MonitorTestCase):
    def test_ptp4l_nanoseconds_convert_to_microseconds(self):
        self.assertEqual(offset_us_from_ptp4l_ns(120_000), 120.0)
        self.assertEqual(offset_us_from_ptp4l_ns(-1_500), -1.5)

    def test_raw_nanosecond_offset_would_have_read_healthy(self):
        # Demonstrates the 1000x trap: a real 120us breach fed in as raw ptp4l
        # nanoseconds reads HEALTHY; converted, it halts.
        monitor = self.make_monitor()
        self.assertEqual(
            monitor.process_telemetry(offset_us=120_000 / 1000.0 / 1000.0,
                                      ptp_state=PtpState.LOCKED),
            MonitorStatus.HEALTHY)

        monitor = self.make_monitor()
        self.assertEqual(
            monitor.process_telemetry(offset_us=offset_us_from_ptp4l_ns(120_000),
                                      ptp_state=PtpState.LOCKED),
            MonitorStatus.CRITICAL)

    def test_non_finite_nanoseconds_rejected(self):
        with self.assertRaises(ClockTelemetryError):
            offset_us_from_ptp4l_ns(float("nan"))


if __name__ == '__main__':
    unittest.main()
