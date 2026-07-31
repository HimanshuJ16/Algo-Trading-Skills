import unittest
from order_to_trade_ratio_fee_penalty_avoidance import (
    OrderToTradeRatioFeePenaltyEngine, OTRMarketSession, OTRThresholdPolicy, OTRReport
)

class TestOrderToTradeRatioFeePenaltyEngine(unittest.TestCase):

    def setUp(self):
        policy = OTRThresholdPolicy(
            max_count_otr=50.0,
            warning_threshold_pct=0.80,
            penalty_fee_per_excess_order=0.05
        )
        self.engine = OrderToTradeRatioFeePenaltyEngine(policy)

    def test_otr_compliant_session(self):
        # 200 orders + 100 cancels + 100 modifies = 400 msgs across 20 trades -> 20:1 Count OTR < 40:1 warning
        session = OTRMarketSession(
            venue="XETRA",
            orders_count=200,
            cancels_count=100,
            modifies_count=100,
            trades_count=20,
            ordered_volume=10000.0,
            traded_volume=2000.0
        )
        report = self.engine.audit_session_otr(session)
        self.assertEqual(report.status, "OTR_COMPLIANT_SAFE")
        self.assertEqual(report.count_otr, 20.0)
        self.assertEqual(report.excess_messages, 0)
        self.assertEqual(report.penalty_fee_accrued_usd, 0.0)
        self.assertEqual(report.recommended_action, "ALLOW_ORDER")

    def test_otr_warning_throttle_active(self):
        # 420 msgs across 10 trades -> 42:1 Count OTR (84% of 50:1 limit)
        session = OTRMarketSession(
            venue="NSE",
            orders_count=200,
            cancels_count=120,
            modifies_count=100,
            trades_count=10,
            ordered_volume=5000.0,
            traded_volume=500.0
        )
        report = self.engine.audit_session_otr(session)
        self.assertEqual(report.status, "OTR_WARNING_THROTTLE_ACTIVE")
        self.assertEqual(report.count_otr, 42.0)
        self.assertEqual(report.recommended_action, "THROTTLE_ORDER_MODIFICATIONS")

    def test_otr_breach_penalty_active(self):
        # 1,000 msgs across 10 trades -> 100:1 Count OTR vs 50:1 limit (Max allowed = 500 msgs)
        # Excess = 500 msgs * $0.05 = $25.00 penalty fee
        session = OTRMarketSession(
            venue="LSE",
            orders_count=400,
            cancels_count=300,
            modifies_count=300,
            trades_count=10,
            ordered_volume=50000.0,
            traded_volume=1000.0
        )
        report = self.engine.audit_session_otr(session)
        self.assertEqual(report.status, "OTR_BREACH_PENALTY_ACTIVE")
        self.assertEqual(report.count_otr, 100.0)
        self.assertEqual(report.excess_messages, 500)
        self.assertEqual(report.penalty_fee_accrued_usd, 25.0)
        self.assertEqual(report.recommended_action, "FREEZE_ORDER_MODIFICATIONS_REQUIRE_TAKER_FILL")

if __name__ == '__main__':
    unittest.main()
