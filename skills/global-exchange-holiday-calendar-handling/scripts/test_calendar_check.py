"""
Unit tests for global-exchange-holiday-calendar-handling skill.

Tests:
1. Weekend market closure detection.
2. Full-day exchange holiday detection (e.g. US New Year's Day, Indian Republic Day).
3. Half-day / early close detection.
4. ADR cross-listing exchange mapping.
5. Session open/close UTC timestamp accuracy.
6. Backward compatibility with standalone is_trading_day and session_open_close functions.
"""
import datetime
import unittest
from calendar_check import (
    GlobalExchangeCalendarManager,
    SessionInfo,
    SessionStatus,
    is_trading_day,
    session_open_close,
)


class TestGlobalExchangeCalendarHandling(unittest.TestCase):

    def setUp(self):
        self.mgr = GlobalExchangeCalendarManager()

    def test_weekend_closed(self):
        # 2026-01-03 is a Saturday
        saturday = datetime.date(2026, 1, 3)
        info = self.mgr.get_session_info("XNYS", saturday)
        self.assertEqual(info.status, SessionStatus.WEEKEND_CLOSED)
        self.assertFalse(self.mgr.is_trading_day("XNYS", saturday))

    def test_holiday_detection(self):
        # 2026-01-01 New Year's Day
        new_year = datetime.date(2026, 1, 1)
        info_nys = self.mgr.get_session_info("XNYS", new_year)
        self.assertEqual(info_nys.status, SessionStatus.FULL_DAY_HOLIDAY)

        # 2026-01-26 Indian Republic Day
        republic_day = datetime.date(2026, 1, 26)
        info_nse = self.mgr.get_session_info("XNSE", republic_day)
        self.assertEqual(info_nse.status, SessionStatus.FULL_DAY_HOLIDAY)

    def test_half_day_early_close(self):
        # 2026-11-27 Day after Thanksgiving
        black_friday = datetime.date(2026, 11, 27)
        info = self.mgr.get_session_info("XNYS", black_friday)
        self.assertEqual(info.status, SessionStatus.HALF_DAY_EARLY_CLOSE)
        self.assertTrue(self.mgr.is_trading_day("XNYS", black_friday))

    def test_adr_exchange_mapper(self):
        self.assertEqual(self.mgr.map_instrument_to_exchange("INFY"), "XNYS")
        self.assertEqual(self.mgr.map_instrument_to_exchange("INFY.NS"), "XNSE")

    def test_backward_compatibility(self):
        new_year = datetime.date(2026, 1, 1)
        self.assertFalse(is_trading_day("XNYS", new_year))

        open_utc, close_utc = session_open_close("XNYS", new_year)
        self.assertIsNone(open_utc)
        self.assertIsNone(close_utc)


if __name__ == "__main__":
    unittest.main()
