import unittest
from datetime import date
from corporate_action_event_calendar_integration import (
    CorporateActionEventCalendarEngine, CorporateActionEvent
)

class TestCorporateActionEventCalendarEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CorporateActionEventCalendarEngine()
        self.div_event = CorporateActionEvent(
            event_id="EVT_DIV_01", symbol="AAPL", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 10),
            record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=1.50
        )
        self.split_event = CorporateActionEvent(
            event_id="EVT_SPLIT_01", symbol="NVDA", event_type="STOCK_SPLIT",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 12),
            record_date=date(2025, 5, 13), payment_date=date(2025, 5, 20), value=4.0
        )
        self.engine.register_event(self.div_event)
        self.engine.register_event(self.split_event)

    def test_query_upcoming_events(self):
        # Query on May 8 with 5 day window (May 8 - May 13)
        # Should return both AAPL (May 10) and NVDA (May 12)
        upcoming = self.engine.query_upcoming_events(current_date=date(2025, 5, 8), lookahead_days=5)
        self.assertEqual(len(upcoming), 2)
        self.assertEqual(upcoming[0].symbol, "AAPL")
        self.assertEqual(upcoming[1].symbol, "NVDA")

    def test_dividend_entitlement_calculation(self):
        # On May 15 (after Record Date May 11, before Payment Date May 25)
        ent = self.engine.calculate_dividend_entitlement(
            symbol="AAPL", shares_held_on_record_date=10000.0, current_date=date(2025, 5, 15)
        )
        self.assertIsNotNone(ent)
        self.assertEqual(ent.gross_receivable_amount, 15000.0) # 10,000 * $1.50
        self.assertEqual(ent.status, "PENDING_PAYMENT")

        # On May 26 (after Payment Date May 25)
        ent_paid = self.engine.calculate_dividend_entitlement(
            symbol="AAPL", shares_held_on_record_date=10000.0, current_date=date(2025, 5, 26)
        )
        self.assertEqual(ent_paid.status, "PAID")

    def test_vendor_feed_reconciliation(self):
        ev_a = self.div_event
        # Vendor B has mismatched Ex-Date (May 12 instead of May 10)
        ev_b = CorporateActionEvent(
            event_id="EVT_DIV_01", symbol="AAPL", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 12),
            record_date=date(2025, 5, 13), payment_date=date(2025, 5, 25), value=1.50
        )
        
        discrepancies = self.engine.reconcile_vendor_feeds([ev_a], [ev_b])
        self.assertEqual(len(discrepancies), 1)
        self.assertIn("Ex-Date mismatch", discrepancies[0])

if __name__ == '__main__':
    unittest.main()
