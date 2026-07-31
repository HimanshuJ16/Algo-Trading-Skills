import unittest
from options_chain_conventions import (
    OptionsChainExpiryConventionsEngine, OptionExpiryQuery, OptionsChainConventionReport
)

class TestOptionsChainExpiryConventionsEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainExpiryConventionsEngine()

    def test_third_friday_calculation(self):
        # January 2024 -> 3rd Friday is Jan 19, 2024
        tf_jan_2024 = self.engine.get_third_friday(2024, 1)
        self.assertEqual(tf_jan_2024.strftime("%Y-%m-%d"), "2024-01-19")

        # June 2024 -> 3rd Friday is June 21, 2024
        tf_jun_2024 = self.engine.get_third_friday(2024, 6)
        self.assertEqual(tf_jun_2024.strftime("%Y-%m-%d"), "2024-06-21")

    def test_spx_monthly_am_settlement_convention(self):
        query = OptionExpiryQuery(
            exchange="CBOE",
            underlying_symbol="SPX",
            reference_date_iso="2024-01-01",
            target_year=2024,
            target_month=1,
            cycle_type="MONTHLY"
        )
        report = self.engine.resolve_conventions(query)
        self.assertEqual(report.expiration_date_iso, "2024-01-19")
        self.assertEqual(report.dte_days, 18)
        self.assertEqual(report.settlement_type, "AM_SETTLED")
        self.assertEqual(report.exercise_style, "EUROPEAN")
        self.assertEqual(report.delivery_type, "CASH")

    def test_spxw_weekly_pm_settlement_convention(self):
        query = OptionExpiryQuery(
            exchange="CBOE",
            underlying_symbol="SPXW",
            reference_date_iso="2024-01-01",
            target_year=2024,
            target_month=1,
            cycle_type="WEEKLY"
        )
        report = self.engine.resolve_conventions(query)
        self.assertEqual(report.settlement_type, "PM_SETTLED")
        self.assertEqual(report.exercise_style, "EUROPEAN")

if __name__ == '__main__':
    unittest.main()
