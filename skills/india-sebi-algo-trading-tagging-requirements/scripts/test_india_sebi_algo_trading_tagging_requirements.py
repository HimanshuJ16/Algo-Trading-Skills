import unittest
from india_sebi_algo_trading_tagging_requirements import (
    SebiAlgoTaggingEngine, SebiAlgoOrderPayload, SebiOtrMetrics, SebiAlgoTaggingReport
)

class TestSebiAlgoTaggingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SebiAlgoTaggingEngine(
            otr_warning_threshold=500.0, otr_cooling_off_threshold=2000.0
        )

    def test_sebi_tagging_approval(self):
        payload = SebiAlgoOrderPayload(
            algo_id="NSE_ALGO_99812", client_category="PRO", symbol="RELIANCE",
            exchange="NSE", side="BUY", price=2500.0, quantity=100
        )
        metrics = SebiOtrMetrics(total_order_messages=200, total_executed_trades=5) # OTR = 40.0
        report = self.engine.audit_sebi_algo_order(payload, metrics)

        self.assertEqual(report.status, "SEBI_TAGGING_APPROVED")
        self.assertEqual(report.algo_id, "NSE_ALGO_99812")
        self.assertEqual(report.client_category, "PRO")
        self.assertEqual(report.calculated_otr_ratio, 40.0)
        self.assertEqual(report.otr_status, "OTR_NORMAL")

    def test_untagged_algo_order_rejection(self):
        payload = SebiAlgoOrderPayload(
            algo_id="", client_category="CLI", symbol="INFY",
            exchange="NSE", side="BUY", price=1500.0, quantity=50
        )
        metrics = SebiOtrMetrics(total_order_messages=10, total_executed_trades=1)
        report = self.engine.audit_sebi_algo_order(payload, metrics)

        self.assertEqual(report.status, "REJECTED_UNTAGGED_ALGO")
        self.assertFalse(report.is_algo_id_valid)

    def test_otr_cooling_off_penalty_trigger(self):
        payload = SebiAlgoOrderPayload(
            algo_id="NSE_ALGO_99812", client_category="PRO", symbol="RELIANCE",
            exchange="NSE", side="BUY", price=2500.0, quantity=100
        )
        # Total Order Messages = 25,000, Trades = 10 -> OTR = 2,500 > 2,000!
        metrics = SebiOtrMetrics(total_order_messages=25000, total_executed_trades=10)
        report = self.engine.audit_sebi_algo_order(payload, metrics)

        self.assertEqual(report.status, "SEBI_TAGGING_APPROVED")
        self.assertEqual(report.calculated_otr_ratio, 2500.0)
        self.assertEqual(report.otr_status, "OTR_PENALTY_COOLING_OFF")

if __name__ == '__main__':
    unittest.main()
