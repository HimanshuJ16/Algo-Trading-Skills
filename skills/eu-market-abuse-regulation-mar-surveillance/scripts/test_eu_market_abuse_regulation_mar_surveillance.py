import unittest
from eu_market_abuse_regulation_mar_surveillance import (
    EuMarSurveillanceEngine, OrderExecutionEvent, EuMarSurveillanceAuditReport
)

class TestEuMarSurveillanceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EuMarSurveillanceEngine(spoof_cancel_ratio_threshold=0.90)

    def test_wash_trade_detection_and_stor_filing(self):
        # Fill event where buyer_account_id == seller_account_id ('ACC_PROP_100') -> WASH TRADE!
        ev = OrderExecutionEvent(
            event_id="EV_FILL_01", cl_ord_id="ORD_100", isin="DE0007100000", symbol="MBG",
            side="BUY", order_qty=1000, price=62.50, event_type="FILL", timestamp_ns=1000000,
            buyer_account_id="ACC_PROP_100", seller_account_id="ACC_PROP_100"
        )
        report = self.engine.audit_events_for_mar_patterns([ev])
        
        self.assertEqual(report.wash_trade_alerts_count, 1)
        self.assertIsNotNone(report.stor_filing_payload)
        self.assertEqual(report.alerts[0].alert_type, "WASH_TRADE_ALERT")
        self.assertEqual(report.alerts[0].severity, "CRITICAL")

    def test_spoofing_layering_detection(self):
        # 10 NEW orders, 9 CANCEL orders -> 90% Cancel ratio -> SPOOFING ALERT!
        events = []
        for i in range(10):
            events.append(OrderExecutionEvent(
                event_id=f"EV_NEW_{i}", cl_ord_id=f"ORD_{i}", isin="DE0007100000", symbol="MBG",
                side="BUY", order_qty=5000, price=62.00, event_type="NEW", timestamp_ns=1000 + i,
                buyer_account_id="ACC_PROP_100", seller_account_id="ACC_MM_200"
            ))
        for i in range(9):
            events.append(OrderExecutionEvent(
                event_id=f"EV_CAN_{i}", cl_ord_id=f"ORD_{i}", isin="DE0007100000", symbol="MBG",
                side="BUY", order_qty=5000, price=62.00, event_type="CANCEL", timestamp_ns=2000 + i,
                buyer_account_id="ACC_PROP_100", seller_account_id="ACC_MM_200"
            ))

        report = self.engine.audit_events_for_mar_patterns(events)
        
        self.assertEqual(report.spoofing_alerts_count, 1)
        self.assertIsNotNone(report.stor_filing_payload)
        self.assertEqual(report.alerts[0].alert_type, "SPOOFING_ALERT")

if __name__ == '__main__':
    unittest.main()
