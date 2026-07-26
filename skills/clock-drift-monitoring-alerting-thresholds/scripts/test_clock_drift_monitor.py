import unittest
from clock_drift_monitor import ClockDriftMonitor, PtpState, MonitorStatus

class TestClockDriftMonitor(unittest.TestCase):

    def setUp(self):
        self.kill_switch_triggered = False
        self.trigger_reason = None
        
        def mock_kill_switch(reason: str):
            self.kill_switch_triggered = True
            self.trigger_reason = reason
            
        self.monitor = ClockDriftMonitor(
            kill_switch_callback=mock_kill_switch,
            warning_threshold_us=50,
            critical_threshold_us=100
        )

    def test_healthy_offset(self):
        status = self.monitor.process_telemetry(offset_us=12.5, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.HEALTHY)
        self.assertFalse(self.kill_switch_triggered)

    def test_warning_offset(self):
        # Negative drift, should use absolute value
        status = self.monitor.process_telemetry(offset_us=-65.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.WARNING)
        self.assertFalse(self.kill_switch_triggered)

    def test_critical_drift_breach(self):
        status = self.monitor.process_telemetry(offset_us=105.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertTrue(self.kill_switch_triggered)
        self.assertEqual(self.trigger_reason, "DRIFT_BREACH_105.0us")

    def test_unlocked_state_breach(self):
        # Even if offset is 0, if state is UNLOCKED, it's critical
        status = self.monitor.process_telemetry(offset_us=0.0, ptp_state=PtpState.UNLOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)
        self.assertTrue(self.kill_switch_triggered)
        self.assertEqual(self.trigger_reason, "PTP_UNLOCKED")

    def test_halted_state_persists(self):
        # Trigger kill switch
        self.monitor.process_telemetry(offset_us=150.0, ptp_state=PtpState.LOCKED)
        self.assertTrue(self.monitor.trading_halted)
        
        # Subsequent healthy telemetry should be ignored and still return CRITICAL
        status = self.monitor.process_telemetry(offset_us=10.0, ptp_state=PtpState.LOCKED)
        self.assertEqual(status, MonitorStatus.CRITICAL)

if __name__ == '__main__':
    unittest.main()
