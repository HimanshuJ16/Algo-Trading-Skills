import unittest
from macro_calendar_integration import (
    GlobalMacroCalendarEngine, MacroEconomicEvent, MacroCalendarAuditReport
)

class TestGlobalMacroCalendarEngine(unittest.TestCase):

    def setUp(self):
        self.engine = GlobalMacroCalendarEngine(pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0) # 15 min buffers
        # Scheduled Event: FOMC Rate Decision at T = 10,000
        self.fomc = MacroEconomicEvent(
            event_id="EVT_FOMC_01", event_name="FOMC_RATE_DECISION", currency="USD",
            release_timestamp_utc=10000.0, impact_severity="HIGH_IMPACT",
            consensus_forecast=5.25, forecast_std_dev=0.10, actual_release=5.50
        )
        self.engine.add_event(self.fomc)

    def test_blackout_active_during_pre_event_window(self):
        # Current time T = 9,400 (10 mins prior to release T=10,000) -> BLACKOUT ACTIVE!
        report = self.engine.audit_macro_trading_status(current_time_utc=9400.0)

        self.assertTrue(report.is_blackout_active)
        self.assertFalse(report.is_trading_permitted)
        self.assertTrue(report.should_cancel_open_limit_orders)
        self.assertEqual(report.status, "MACRO_BLACKOUT_ACTIVE")

    def test_trading_permitted_outside_blackout_window(self):
        # Current time T = 12,000 (33 mins after release T=10,000) -> CLEAR!
        report = self.engine.audit_macro_trading_status(current_time_utc=12000.0)

        self.assertFalse(report.is_blackout_active)
        self.assertTrue(report.is_trading_permitted)
        self.assertFalse(report.should_cancel_open_limit_orders)
        self.assertEqual(report.status, "MACRO_TRADING_PERMITTED")

    def test_surprise_index_calculation(self):
        # Actual 5.50 vs Forecast 5.25 (StdDev 0.10) -> (5.50 - 5.25) / 0.10 = +2.50
        surprise = self.engine.calculate_surprise_index(self.fomc)
        self.assertEqual(surprise, 2.5)

if __name__ == '__main__':
    unittest.main()
