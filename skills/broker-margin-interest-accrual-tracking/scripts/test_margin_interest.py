"""
Unit tests for broker-margin-interest-accrual-tracking skill.
"""
import datetime
import unittest
from margin_interest import MarginInterestTracker


class TestMarginInterestTracker(unittest.TestCase):

    def setUp(self):
        self.tracker = MarginInterestTracker(day_count_convention=365)

    def test_tiered_apr_lookup(self):
        # 50k debit balance -> Tier 1 (6.50% APR)
        self.assertAlmostEqual(self.tracker.get_effective_apr(50000.0), 0.065)
        # 250k debit balance -> Tier 2 (5.80% APR)
        self.assertAlmostEqual(self.tracker.get_effective_apr(250000.0), 0.058)

    def test_daily_accrual_and_pnl_deduction(self):
        # Start on Monday (2026-06-01)
        monday = datetime.date(2026, 6, 1)
        summary = self.tracker.calculate_interest_accrual(
            start_date=monday,
            holding_days=5,  # Mon, Tue, Wed, Thu, Fri
            daily_debit_balance_usd=100000.0,
            gross_pnl_usd=5000.0,
        )

        # Daily rate = 0.058 / 365 = 0.0001589
        # Expected 5-day interest ~ 100,000 * (0.058 / 365) * 5 = $79.45
        self.assertAlmostEqual(summary.total_interest_accrued_usd, 79.45, delta=1.0)
        self.assertAlmostEqual(summary.adjusted_net_pnl_usd, 5000.0 - summary.total_interest_accrued_usd, places=2)

    def test_weekend_compounding_accrual(self):
        # Start on Friday (2026-06-05) -> Friday accrues 3 days (Fri, Sat, Sun)
        friday = datetime.date(2026, 6, 5)
        summary = self.tracker.calculate_interest_accrual(
            start_date=friday,
            holding_days=3,
            daily_debit_balance_usd=100000.0,
            gross_pnl_usd=1000.0,
        )

        self.assertEqual(len(summary.daily_records), 1)
        self.assertEqual(summary.daily_records[0].days_accrued, 3)
        self.assertTrue(summary.daily_records[0].is_weekend)


if __name__ == "__main__":
    unittest.main()
