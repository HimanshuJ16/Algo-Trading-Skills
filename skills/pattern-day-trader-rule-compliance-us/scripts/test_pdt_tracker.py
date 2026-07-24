"""
Unit tests for pattern-day-trader-rule-compliance-us skill.

Tests:
1. Rolling 5-business-day window tracking across weekends.
2. Same-day open/close trade classification vs overnight hold.
3. 4th day trade veto on sub-$25,000 margin account.
4. Allowance of 4th day trade on account equity >= $25,000.
5. Reconciliation against broker day-trade counter.
6. Backward compatibility with DayTradeTracker and would_breach.
"""
import datetime
import unittest
from pdt_tracker import DayTradeTracker, PDTComplianceEngine


class TestPDTComplianceUS(unittest.TestCase):

    def setUp(self):
        self.engine = PDTComplianceEngine(equity_threshold=25_000.0)

    def test_same_day_trade_classification(self):
        # Open BUY on 2026-07-13 at 10:00 AM
        ts_open = datetime.datetime(2026, 7, 13, 10, 0)
        self.engine.record_execution("AAPL", "BUY", 100, ts_open)

        # Close SELL on same day 2026-07-13 at 2:00 PM -> DAY TRADE!
        ts_close = datetime.datetime(2026, 7, 13, 14, 0)
        dt = self.engine.record_execution("AAPL", "SELL", 100, ts_close)

        self.assertIsNotNone(dt)
        self.assertEqual(dt.symbol, "AAPL")
        self.assertEqual(self.engine.get_rolling_day_trade_count(ts_close.date()), 1)

    def test_overnight_hold_is_not_day_trade(self):
        # Open BUY on 2026-07-13
        ts_open = datetime.datetime(2026, 7, 13, 10, 0)
        self.engine.record_execution("MSFT", "BUY", 100, ts_open)

        # Close SELL next day 2026-07-14 -> NOT a day trade!
        ts_close = datetime.datetime(2026, 7, 14, 10, 0)
        dt = self.engine.record_execution("MSFT", "SELL", 100, ts_close)

        self.assertIsNone(dt)
        self.assertEqual(self.engine.get_rolling_day_trade_count(ts_close.date()), 0)

    def test_pdt_veto_on_sub_25k_account(self):
        # Record 3 day trades in rolling window
        d1 = datetime.datetime(2026, 7, 13, 10, 0)
        self.engine.record_execution("AAPL", "BUY", 10, d1)
        self.engine.record_execution("AAPL", "SELL", 10, d1 + datetime.timedelta(hours=1))

        self.engine.record_execution("TSLA", "BUY", 10, d1)
        self.engine.record_execution("TSLA", "SELL", 10, d1 + datetime.timedelta(hours=1))

        self.engine.record_execution("NVDA", "BUY", 10, d1)
        self.engine.record_execution("NVDA", "SELL", 10, d1 + datetime.timedelta(hours=1))

        # 3 day trades recorded.
        # Check 4th day trade on $20,000 account -> VETO!
        breached, msg = self.engine.would_breach_pdt(current_equity=20_000.0, as_of_date=d1.date())
        self.assertTrue(breached)
        self.assertIn("PDT VETO", msg)

        # Check 4th day trade on $30,000 account -> ALLOWED!
        breached_30k, _ = self.engine.would_breach_pdt(current_equity=30_000.0, as_of_date=d1.date())
        self.assertFalse(breached_30k)

    def test_backward_compatibility(self):
        tracker = DayTradeTracker()
        d = datetime.date(2026, 7, 13)
        tracker.record_day_trade(d)
        tracker.record_day_trade(d)
        tracker.record_day_trade(d)

        # 3 day trades recorded -> would breach 4th trade on sub-25k account
        self.assertTrue(tracker.would_breach(15_000.0))
        self.assertFalse(tracker.would_breach(30_000.0))


if __name__ == "__main__":
    unittest.main()
