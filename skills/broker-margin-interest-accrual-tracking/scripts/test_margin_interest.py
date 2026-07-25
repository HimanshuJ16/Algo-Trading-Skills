import datetime
import unittest
from margin_interest import MarginInterestTracker, MarginRateTier

class TestMarginInterestTracker(unittest.TestCase):

    def setUp(self):
        # Using simple tiers for testing
        test_tiers = [
            MarginRateTier(0.0, 100000.0, 0.05),     # 5% up to 100k
            MarginRateTier(100000.0, float("inf"), 0.04) # 4% beyond 100k
        ]
        self.tracker_blended = MarginInterestTracker(
            rate_tiers=test_tiers, 
            day_count_convention_margin=360, 
            is_blended_rate=True
        )
        self.tracker_flat = MarginInterestTracker(
            rate_tiers=test_tiers, 
            day_count_convention_margin=360, 
            is_blended_rate=False
        )

    def test_blended_apr_lookup(self):
        # 150k debit balance -> 100k @ 5%, 50k @ 4% -> (5000 + 2000) / 150000 = 7000 / 150000 = 4.6666...%
        eff_apr = self.tracker_blended.get_effective_apr(150000.0)
        self.assertAlmostEqual(eff_apr, 0.0466666, places=5)

    def test_flat_apr_lookup(self):
        # 150k debit balance -> Falls in tier 2 (4% APR)
        eff_apr = self.tracker_flat.get_effective_apr(150000.0)
        self.assertAlmostEqual(eff_apr, 0.04, places=5)

    def test_daily_accrual_and_pnl_deduction(self):
        # Start on Monday
        monday = datetime.date(2026, 6, 1)
        summary = self.tracker_blended.calculate_interest_accrual(
            start_date=monday,
            holding_days=5,  # Mon, Tue, Wed, Thu, Fri (no weekend)
            daily_debit_balance_usd=100000.0,  # 5% APR
            daily_short_mv_usd=0.0,
            short_borrow_fee_apr=0.0,
            gross_pnl_usd=5000.0,
        )

        # Expected 5-day interest ~ 100,000 * (0.05 / 360) * 5 = 69.444
        self.assertAlmostEqual(summary.total_margin_interest_usd, 69.44, delta=0.01)
        self.assertAlmostEqual(summary.adjusted_net_pnl_usd, 5000.0 - 69.444, places=2)

    def test_weekend_compounding_accrual_and_borrow_fees(self):
        # Start on Friday -> Friday accrues 3 days (Fri, Sat, Sun)
        friday = datetime.date(2026, 6, 5)
        summary = self.tracker_blended.calculate_interest_accrual(
            start_date=friday,
            holding_days=3,
            daily_debit_balance_usd=0.0,
            daily_short_mv_usd=100000.0,
            short_borrow_fee_apr=0.10, # 10% hard to borrow
            gross_pnl_usd=1000.0,
        )

        # Should be 1 record with 3 days accrued
        self.assertEqual(len(summary.daily_records), 1)
        self.assertEqual(summary.daily_records[0].days_accrued, 3)
        self.assertTrue(summary.daily_records[0].is_weekend)

        # Borrow fees = 100,000 * (0.10 / 360) * 3 = 83.333
        self.assertAlmostEqual(summary.total_borrow_fees_usd, 83.33, delta=0.01)
        self.assertAlmostEqual(summary.adjusted_net_pnl_usd, 1000.0 - 83.333, places=2)


if __name__ == "__main__":
    unittest.main()
