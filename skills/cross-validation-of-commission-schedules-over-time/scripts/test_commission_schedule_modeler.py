"""Unit tests for cross-validation-of-commission-schedules-over-time."""
import unittest
from commission_schedule_modeler import HistoricalCommissionModeler


class TestHistoricalCommissionModeler(unittest.TestCase):
    def setUp(self):
        self.modeler = HistoricalCommissionModeler()

    def test_historical_era_commissions(self):
        # 2015 trade: $9.95 min fee + $0.005/share
        res_2015 = self.modeler.calculate_trade_commission("t1", "2015-05-10", "AAPL", 100, 100.0)
        self.assertEqual(res_2015.calculated_commission, 10.45)

        # 2018 trade: $4.95 min fee + $0.005/share
        res_2018 = self.modeler.calculate_trade_commission("t2", "2018-03-15", "AAPL", 100, 150.0)
        self.assertEqual(res_2018.calculated_commission, 5.45)

        # 2021 trade: zero commission era
        res_2021 = self.modeler.calculate_trade_commission("t3", "2021-01-20", "AAPL", 100, 130.0)
        self.assertEqual(res_2021.calculated_commission, 0.0)

    def test_audit_fee_impact(self):
        trades = [
            {"id": "t1", "date": "2015-01-10", "symbol": "AAPL", "shares": 100, "price": 100.0},
            {"id": "t2", "date": "2021-01-10", "symbol": "AAPL", "shares": 100, "price": 130.0},
        ]
        report = self.modeler.audit_impact(trades, starting_capital=10000.0)
        self.assertEqual(report.total_trades, 2)
        self.assertGreater(report.total_commission_historical, 0.0)
        self.assertEqual(report.total_commission_modern_flat, 0.0)


if __name__ == "__main__":
    unittest.main()
