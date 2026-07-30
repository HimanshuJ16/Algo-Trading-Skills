import unittest
from exchange_self_match_prevention_configuration import (
    ExchangeSelfMatchPreventionEngine, RestingBookOrder, SmpOrderRequest, SmpAuditReport
)

class TestExchangeSelfMatchPreventionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeSelfMatchPreventionEngine(default_smp_instruction="CANCEL_RESTING")

    def test_smp_approved_no_collision(self):
        # Resting SELL order has different SMP ID ('SMP_OTHER') -> APPROVED!
        resting = [
            RestingBookOrder("ORD_REST_01", "AAPL", "SELL", 100, 185.00, "SMP_OTHER")
        ]
        req = SmpOrderRequest(
            cl_ord_id="ORD_INCOMING_01", symbol="AAPL", side="BUY", order_qty=100, price=185.00,
            smp_id="SMP_PROP_100", smp_instruction="CANCEL_RESTING"
        )
        report = self.engine.audit_and_apply_smp(req, resting)
        
        self.assertFalse(report.has_self_collision)
        self.assertTrue(report.is_order_dispatched)
        self.assertEqual(report.fix_tag_7928_smp_id, "SMP_PROP_100")
        self.assertEqual(report.fix_tag_8000_smp_instruction, "CANCEL_RESTING")

    def test_cancel_resting_smp_collision(self):
        # Resting SELL order has SAME SMP ID ('SMP_PROP_100') -> COLLISION! CANCEL_RESTING!
        resting = [
            RestingBookOrder("ORD_REST_01", "AAPL", "SELL", 100, 185.00, "SMP_PROP_100")
        ]
        req = SmpOrderRequest(
            cl_ord_id="ORD_INCOMING_02", symbol="AAPL", side="BUY", order_qty=100, price=185.00,
            smp_id="SMP_PROP_100", smp_instruction="CANCEL_RESTING"
        )
        report = self.engine.audit_and_apply_smp(req, resting)
        
        self.assertTrue(report.has_self_collision)
        self.assertEqual(report.colliding_resting_ord_id, "ORD_REST_01")
        self.assertTrue(report.is_order_dispatched)
        self.assertTrue(report.resting_order_cancelled)

    def test_cancel_aggressive_smp_collision(self):
        # Incoming instruction = CANCEL_AGGRESSIVE -> Incoming order blocked!
        resting = [
            RestingBookOrder("ORD_REST_01", "AAPL", "SELL", 100, 185.00, "SMP_PROP_100")
        ]
        req = SmpOrderRequest(
            cl_ord_id="ORD_INCOMING_03", symbol="AAPL", side="BUY", order_qty=100, price=185.00,
            smp_id="SMP_PROP_100", smp_instruction="CANCEL_AGGRESSIVE"
        )
        report = self.engine.audit_and_apply_smp(req, resting)
        
        self.assertTrue(report.has_self_collision)
        self.assertFalse(report.is_order_dispatched)
        self.assertFalse(report.resting_order_cancelled)

if __name__ == '__main__':
    unittest.main()
