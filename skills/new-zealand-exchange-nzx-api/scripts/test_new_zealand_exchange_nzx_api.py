import unittest
from new_zealand_exchange_nzx_api import (
    NewZealandExchangeNZXEngine, NZXOrderRequest, NZXOrderReport
)

class TestNewZealandExchangeNZXEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NewZealandExchangeNZXEngine()

    def test_nzx_tick_size_schedule_validation(self):
        # Sub $0.20 -> 0.001 tick
        self.assertEqual(self.engine.get_nzx_tick_size(0.15), 0.001)
        # $0.20 - $1.995 -> 0.005 tick
        self.assertEqual(self.engine.get_nzx_tick_size(1.50), 0.005)
        # >= $2.00 -> 0.01 tick
        self.assertEqual(self.engine.get_nzx_tick_size(25.00), 0.01)

        # Validate valid tick $1.50 (0.005 step) -> TRUE
        valid_150, _ = self.engine.validate_price_tick(1.50)
        self.assertTrue(valid_150)

        # Validate invalid tick $25.005 (0.005 step for >= $2.00) -> FALSE (requires 0.01 step)
        valid_25005, _ = self.engine.validate_price_tick(25.005)
        self.assertFalse(valid_25005)

    def test_fix_new_order_single_building_and_rejection(self):
        # Valid order: 1000 shares of FPH @ 30.00 NZD
        valid_ord = NZXOrderRequest(
            cl_ord_id="NZX_001",
            symbol="FPH",
            side="BUY",
            quantity=1000,
            price=30.00,
            order_type="LIMIT",
            time_in_force="DAY"
        )
        report = self.engine.build_fix_new_order_single(valid_ord)
        self.assertEqual(report.status, "NEW")
        self.assertEqual(report.fix_msg_type, "D")
        self.assertIn("35=D", report.fix_raw_payload)
        self.assertIn("55=FPH", report.fix_raw_payload)
        self.assertIn("15=NZD", report.fix_raw_payload)

        # Invalid order: price 30.005 NZD violates 0.01 tick size -> REJECTED
        invalid_ord = NZXOrderRequest(
            cl_ord_id="NZX_002",
            symbol="FPH",
            side="BUY",
            quantity=1000,
            price=30.005,
            order_type="LIMIT",
            time_in_force="DAY"
        )
        report_rej = self.engine.build_fix_new_order_single(invalid_ord)
        self.assertEqual(report_rej.status, "REJECTED")
        self.assertIn("NZX REJECT", report_rej.audit_notes)

if __name__ == '__main__':
    unittest.main()
