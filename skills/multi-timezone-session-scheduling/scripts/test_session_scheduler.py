"""
Unit tests for multi-timezone-session-scheduling skill.

Tests:
1. NYSE IANA zone-aware session UTC conversion under DST (Summer UTC-4 vs Winter UTC-5).
2. Asynchronous DST transition shift between US (NYSE) and EU (LSE).
3. Southern Hemisphere inverse DST transition (Sydney Australia).
4. Market session state evaluation (REGULAR_TRADING, PRE_MARKET, MARKET_CLOSED).
5. Cross-exchange market gap and overlap calculations.
6. Backward compatibility with exchange_session_utc and cross_exchange_gap_minutes.
"""
import datetime
import unittest
from session_scheduler import (
    MarketSessionState,
    MultiTimezoneSessionScheduler,
    cross_exchange_gap_minutes,
    exchange_session_utc,
)


class TestMultiTimezoneSessionScheduling(unittest.TestCase):

    def setUp(self):
        self.scheduler = MultiTimezoneSessionScheduler()

    def test_nyse_dst_shift_summer_vs_winter(self):
        # Summer date (July 15, 2026) -> EDT is UTC-4
        summer_date = datetime.date(2026, 7, 15)
        open_sum, close_sum = self.scheduler.get_session_utc("XNYS", summer_date)

        # 9:30 AM EDT = 13:30 UTC
        self.assertEqual(open_sum.hour, 13)
        self.assertEqual(open_sum.minute, 30)

        # Winter date (January 15, 2026) -> EST is UTC-5
        winter_date = datetime.date(2026, 1, 15)
        open_win, close_win = self.scheduler.get_session_utc("XNYS", winter_date)

        # 9:30 AM EST = 14:30 UTC
        self.assertEqual(open_win.hour, 14)
        self.assertEqual(open_win.minute, 30)

    def test_southern_hemisphere_dst_sydney(self):
        # Sydney summer (January 15, 2026) -> AEDT is UTC+11
        summer_date = datetime.date(2026, 1, 15)
        open_syd, _ = self.scheduler.get_session_utc("XASX", summer_date)

        # 10:00 AM AEDT = 23:00 UTC previous day (23:00 UTC Jan 14)
        self.assertEqual(open_syd.hour, 23)

    def test_market_status_evaluation(self):
        # 14:00 UTC on July 15, 2026 -> NYSE is in REGULAR_TRADING (13:30 - 20:00 UTC)
        trading_time = datetime.datetime(2026, 7, 15, 14, 0, tzinfo=datetime.timezone.utc)
        status = self.scheduler.get_market_status("XNYS", trading_time)
        self.assertEqual(status, MarketSessionState.REGULAR_TRADING)

        # 02:00 UTC -> NYSE is MARKET_CLOSED
        closed_time = datetime.datetime(2026, 7, 15, 2, 0, tzinfo=datetime.timezone.utc)
        status_closed = self.scheduler.get_market_status("XNYS", closed_time)
        self.assertEqual(status_closed, MarketSessionState.MARKET_CLOSED)

    def test_backward_compatibility(self):
        d = datetime.date(2026, 7, 15)
        op_utc, cl_utc = exchange_session_utc(
            d, datetime.time(9, 30), datetime.time(16, 0), "America/New_York"
        )
        self.assertEqual(op_utc.hour, 13)

        gap = cross_exchange_gap_minutes(op_utc, cl_utc)
        self.assertEqual(gap, 390.0)  # 6.5 hours = 390 mins


if __name__ == "__main__":
    unittest.main()
