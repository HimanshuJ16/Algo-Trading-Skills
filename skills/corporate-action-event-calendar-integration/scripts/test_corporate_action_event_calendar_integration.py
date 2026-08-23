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

    def test_query_upcoming_events_rejects_negative_lookahead(self):
        with self.assertRaises(ValueError):
            self.engine.query_upcoming_events(current_date=date(2025, 5, 8), lookahead_days=-1)

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

    def test_dividend_entitlement_returns_latest_recorded_event(self):
        # A calendar normally holds several periodic dividends per symbol.
        # After the August record date passes, the receivable must reflect the
        # August dividend ($1.00), not the first-registered May one ($1.50).
        august_event = CorporateActionEvent(
            event_id="EVT_DIV_02", symbol="AAPL", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 7, 20), ex_date=date(2025, 8, 10),
            record_date=date(2025, 8, 11), payment_date=date(2025, 8, 25), value=1.00
        )
        self.engine.register_event(august_event)

        ent = self.engine.calculate_dividend_entitlement(
            symbol="AAPL", shares_held_on_record_date=1000.0, current_date=date(2025, 8, 15)
        )
        self.assertEqual(ent.dividend_per_share, 1.00)
        self.assertEqual(ent.record_date, date(2025, 8, 11))
        self.assertEqual(ent.gross_receivable_amount, 1000.0)

        # Before the August record date, the May dividend is still the latest recognized one.
        ent_may = self.engine.calculate_dividend_entitlement(
            symbol="AAPL", shares_held_on_record_date=1000.0, current_date=date(2025, 6, 15)
        )
        self.assertEqual(ent_may.dividend_per_share, 1.50)
        self.assertEqual(ent_may.record_date, date(2025, 5, 11))

    def test_dividend_entitlement_rejects_negative_shares(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_dividend_entitlement(
                symbol="AAPL", shares_held_on_record_date=-100.0, current_date=date(2025, 5, 15)
            )

    def test_dividend_entitlement_none_before_record_date(self):
        ent = self.engine.calculate_dividend_entitlement(
            symbol="AAPL", shares_held_on_record_date=100.0, current_date=date(2025, 5, 9)
        )
        self.assertIsNone(ent)

    def test_vendor_feed_reconciliation(self):
        ev_a = self.div_event
        # Vendor B reports a T+1-style calendar (ex-date on the record date,
        # May 11) while Vendor A reports ex-date May 10: ex-date mismatch only.
        ev_b = CorporateActionEvent(
            event_id="EVT_DIV_01", symbol="AAPL", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 11),
            record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=1.50
        )

        discrepancies = self.engine.reconcile_vendor_feeds([ev_a], [ev_b])
        self.assertEqual(len(discrepancies), 1)
        self.assertIn("Ex-Date mismatch", discrepancies[0])

    def test_reconciliation_flags_event_missing_from_vendor_a(self):
        # A whole event present only in Vendor B is the primary parity failure
        # mode and must be flagged, not silently ignored.
        discrepancies = self.engine.reconcile_vendor_feeds([], [self.div_event])
        self.assertEqual(len(discrepancies), 1)
        self.assertIn("present in Vendor B but missing in Vendor A", discrepancies[0])

    def test_reconciliation_flags_record_and_payment_date_mismatch(self):
        ev_b = CorporateActionEvent(
            event_id="EVT_DIV_01", symbol="AAPL", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 10),
            record_date=date(2025, 5, 12), payment_date=date(2025, 5, 26), value=1.50
        )
        discrepancies = self.engine.reconcile_vendor_feeds([self.div_event], [ev_b])
        self.assertEqual(len(discrepancies), 2)
        self.assertTrue(any("Record Date mismatch" in d for d in discrepancies))
        self.assertTrue(any("Payment Date mismatch" in d for d in discrepancies))

    def test_reconciliation_flags_duplicate_ids_within_feed(self):
        discrepancies = self.engine.reconcile_vendor_feeds(
            [self.div_event, self.div_event], [self.div_event]
        )
        self.assertEqual(len(discrepancies), 1)
        self.assertIn("Duplicate event_id EVT_DIV_01 in Vendor A feed", discrepancies[0])

    def test_register_event_is_idempotent_on_event_id(self):
        registered = self.engine.register_event(self.div_event)
        self.assertFalse(registered)
        upcoming = self.engine.query_upcoming_events(current_date=date(2025, 5, 10), lookahead_days=1)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0].event_id, "EVT_DIV_01")

    def test_event_validation_rejects_out_of_order_dates(self):
        with self.assertRaises(ValueError):
            CorporateActionEvent(
                event_id="EVT_BAD_01", symbol="AAPL", event_type="CASH_DIVIDEND",
                declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 12),
                record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=1.50
            )
        with self.assertRaises(ValueError):
            CorporateActionEvent(
                event_id="EVT_BAD_02", symbol="AAPL", event_type="CASH_DIVIDEND",
                declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 10),
                record_date=date(2025, 5, 11), payment_date=date(2025, 5, 9), value=1.50
            )

    def test_event_validation_allows_ex_date_equal_to_record_date(self):
        # Under US T+1 settlement (since 2024-05-28) the ex-date generally
        # coincides with the record date; this must be accepted.
        ev = CorporateActionEvent(
            event_id="EVT_T1_01", symbol="MSFT", event_type="CASH_DIVIDEND",
            declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 12),
            record_date=date(2025, 5, 12), payment_date=date(2025, 6, 12), value=0.75
        )
        self.assertEqual(ev.ex_date, ev.record_date)

    def test_event_validation_rejects_unknown_event_type(self):
        with self.assertRaises(ValueError):
            CorporateActionEvent(
                event_id="EVT_BAD_03", symbol="AAPL", event_type="CASH_DIVIDEND_SPECIAL",
                declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 10),
                record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=1.50
            )

    def test_event_validation_rejects_non_positive_or_non_finite_value(self):
        for bad_value in (0.0, -1.50, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                CorporateActionEvent(
                    event_id="EVT_BAD_04", symbol="AAPL", event_type="CASH_DIVIDEND",
                    declaration_date=date(2025, 5, 1), ex_date=date(2025, 5, 10),
                    record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=bad_value
                )

    def test_datetime_values_are_rejected_for_event_dates(self):
        # datetime subclasses date, so a naive isinstance check accepts it, but
        # datetime != date for the same calendar day, which would silently
        # produce false reconciliation mismatches. Feed parsers returning
        # datetimes must normalize with .date().
        from datetime import datetime
        with self.assertRaises(TypeError):
            CorporateActionEvent(
                event_id="EVT_BAD_05", symbol="AAPL", event_type="CASH_DIVIDEND",
                declaration_date=datetime(2025, 5, 1, 0, 0), ex_date=date(2025, 5, 10),
                record_date=date(2025, 5, 11), payment_date=date(2025, 5, 25), value=1.50
            )

    def test_datetime_current_date_is_rejected(self):
        from datetime import datetime
        with self.assertRaises(TypeError):
            self.engine.query_upcoming_events(current_date=datetime(2025, 5, 8), lookahead_days=5)
        with self.assertRaises(TypeError):
            self.engine.calculate_dividend_entitlement(
                symbol="AAPL", shares_held_on_record_date=100.0,
                current_date=datetime(2025, 5, 15)
            )

if __name__ == '__main__':
    unittest.main()
