"""
Unit tests for order-placement-idempotency.

The suite is organised around the five invariants documented in
``order_ledger``'s module docstring, plus explicit regression tests for the
defects found in the 1.0.0 reference implementation:

* R1  A timeout followed by another ``place_order`` re-sent the order.
* R2  Reconciliation matched on ``(symbol, side, quantity)`` only, so an
      unrelated identical order was credited to a new intent.
* R3  Any response that was not ``{"status": "SUCCESS"}`` — including a real
      Kite Connect success body — was recorded as ``REJECTED``.
* R4  Concurrent calls with one key all reached the broker.
* R5  Key derivation was type-sensitive (``50`` != ``50.0``).
* R6  ``update_status`` allowed transitions out of terminal states and wiped
      ``broker_order_id``.
* R7  Startup crash recovery was documented but not implemented.
* R8  ``ABSENT_SAFE_TO_RESEND`` was a dead end: nothing released the ledger
      claim, so the re-send it authorised looped back to ABSENT forever.
* R9  ``{"status": "error"}`` was read as a terminal rejection, so a Kite
      ``NetworkException`` — a gateway fault whose order may well be live —
      recorded "no order exists".
"""
import logging
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from order_ledger import (
    BROKER_KEY_MAX_LEN,
    IdempotentOrderRouter,
    IllegalStateTransition,
    OrderIntentStatus,
    OrderLedger,
    PENDING,
    PLACED,
    REJECTED,
    UNKNOWN,
    ReconcileOutcome,
    classify_broker_response,
    make_idempotency_key,
)


def setUpModule() -> None:
    # The module logs warnings on every indeterminate outcome, which is most of
    # this suite. Tests that assert on log output use assertLogs, which
    # temporarily lowers the level again.
    logging.getLogger("order_ledger").setLevel(logging.CRITICAL)


def ok_response(order_id: str = "BROKER_ORDER_999"):
    return {"status": "SUCCESS", "broker_order_id": order_id}


class TestIdempotencyKey(unittest.TestCase):
    """Key derivation must be deterministic, restart-stable, and bounded."""

    def test_deterministic_and_default_length(self):
        args = ("strat1", "NIFTY", "BUY", 1700000000, 50.0, 19500.0)
        self.assertEqual(make_idempotency_key(*args), make_idempotency_key(*args))
        self.assertEqual(len(make_idempotency_key(*args)), 24)

    def test_regression_r5_int_and_float_arguments_agree(self):
        """`qty=50` after a restart must not derive a different key from `50.0`."""
        self.assertEqual(
            make_idempotency_key("s", "NIFTY", "BUY", 100, 50, 19500),
            make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0),
        )

    def test_symbol_and_side_are_case_and_whitespace_insensitive(self):
        self.assertEqual(
            make_idempotency_key("s", "NIFTY", "BUY", 100),
            make_idempotency_key("s", " nifty ", "buy", 100),
        )

    def test_naive_datetime_is_treated_as_utc(self):
        aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        naive = datetime(2026, 1, 2, 3, 4, 5)
        self.assertEqual(
            make_idempotency_key("s", "N", "BUY", aware),
            make_idempotency_key("s", "N", "BUY", naive),
        )

    def test_equivalent_timezones_agree(self):
        utc = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(
            make_idempotency_key("s", "N", "BUY", utc),
            make_idempotency_key("s", "N", "BUY", ist),
        )

    def test_distinct_orders_produce_distinct_keys(self):
        base = make_idempotency_key("s", "NIFTY", "BUY", 100, 50, 19500)
        self.assertNotEqual(base, make_idempotency_key("s", "NIFTY", "SELL", 100, 50, 19500))
        self.assertNotEqual(base, make_idempotency_key("s", "BANKNIFTY", "BUY", 100, 50, 19500))
        self.assertNotEqual(base, make_idempotency_key("s", "NIFTY", "BUY", 100, 51, 19500))
        self.assertNotEqual(base, make_idempotency_key("s", "NIFTY", "BUY", 100, 50, 19501))
        self.assertNotEqual(base, make_idempotency_key("s2", "NIFTY", "BUY", 100, 50, 19500))

    def test_sequence_separates_legitimately_identical_orders(self):
        """Two child slices at one signal timestamp must not collapse onto one key."""
        first = make_idempotency_key("s", "N", "BUY", 100, 50, 19500, sequence=0)
        second = make_idempotency_key("s", "N", "BUY", 100, 50, 19500, sequence=1)
        self.assertNotEqual(first, second)

    def test_key_fits_kite_tag_limit(self):
        key = make_idempotency_key("s", "N", "BUY", 100, max_len=BROKER_KEY_MAX_LEN["zerodha_kite"])
        self.assertEqual(len(key), 20)
        self.assertTrue(key.isalnum(), "Kite documents `tag` as alphanumeric")

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(ValueError):
            make_idempotency_key("", "N", "BUY", 1)
        with self.assertRaises(ValueError):
            make_idempotency_key("s", "  ", "BUY", 1)
        with self.assertRaises(ValueError):
            make_idempotency_key("s", "N", "", 1)
        with self.assertRaises(ValueError):
            make_idempotency_key("s", "N", "BUY", 1, sequence=-1)
        with self.assertRaises(ValueError):
            make_idempotency_key("s", "N", "BUY", 1, max_len=4)
        with self.assertRaises(ValueError):
            make_idempotency_key("s", "N", "BUY", 1, max_len=65)


class TestResponseClassification(unittest.TestCase):
    """Anything the broker did not state unambiguously must classify UNKNOWN."""

    def test_flat_success(self):
        status, order_id, _ = classify_broker_response(ok_response("B1"))
        self.assertIs(status, OrderIntentStatus.PLACED)
        self.assertEqual(order_id, "B1")

    def test_regression_r3_kite_success_body_is_not_a_rejection(self):
        """`{"status": "success", "data": {"order_id": ...}}` is a real Kite ack."""
        status, order_id, _ = classify_broker_response(
            {"status": "success", "data": {"order_id": "151220000000000"}}
        )
        self.assertIs(status, OrderIntentStatus.PLACED)
        self.assertEqual(order_id, "151220000000000")

    def test_auto_sliced_placement_keeps_every_order_id(self):
        status, order_id, _ = classify_broker_response(
            {"status": "success", "data": [{"order_id": "A1"}, {"order_id": "A2"}]}
        )
        self.assertIs(status, OrderIntentStatus.PLACED)
        self.assertEqual(order_id, "A1,A2")

    def test_explicit_rejection_carries_reason(self):
        status, order_id, detail = classify_broker_response(
            {"status": "rejected", "message": "Insufficient margin"}
        )
        self.assertIs(status, OrderIntentStatus.REJECTED)
        self.assertIsNone(order_id)
        self.assertEqual(detail, "Insufficient margin")

    def test_regression_r3_indeterminate_bodies_are_unknown_not_rejected(self):
        for body in ({}, {"status": "PUT ORDER REQ RECEIVED"}, {"status": "TRIGGER PENDING"}):
            with self.subTest(body=body):
                status, _, _ = classify_broker_response(body)
                self.assertIs(status, OrderIntentStatus.UNKNOWN)

    def test_success_without_order_id_is_unknown(self):
        """No id means the order can never be reconciled; do not invent one."""
        status, order_id, _ = classify_broker_response({"status": "SUCCESS"})
        self.assertIs(status, OrderIntentStatus.UNKNOWN)
        self.assertIsNone(order_id)

    def test_working_state_acknowledgements_are_placements(self):
        """Kite `OPEN`, Alpaca `new`, Binance `NEW` are acks, not indeterminate."""
        for body in (
            {"status": "OPEN", "order_id": "K1"},
            {"status": "new", "id": "A1"},
            {"status": "NEW", "orderId": 12345},
        ):
            with self.subTest(body=body):
                status, order_id, _ = classify_broker_response(body)
                self.assertIs(status, OrderIntentStatus.PLACED)
                self.assertTrue(order_id)

    def test_rejection_token_wins_over_a_returned_order_id(self):
        status, order_id, detail = classify_broker_response(
            {"status": "rejected", "order_id": "R1", "message": "RMS blocked"}
        )
        self.assertIs(status, OrderIntentStatus.REJECTED)
        self.assertIsNone(order_id)
        self.assertEqual(detail, "RMS blocked")

    def test_regression_r9_kite_network_exception_is_unknown_not_rejected(self):
        """A gateway fault says nothing about the order; it may well be live."""
        for body in (
            {"status": "error", "error_type": "NetworkException",
             "message": "Gateway timeout"},
            {"status": "error", "error_type": "GatewayTimeout"},
            {"status": "error", "error_type": "DataException"},
            {"status": "error", "error_type": "GeneralException"},
            {"status": "failed", "message": "upstream connection reset"},
            {"status": "error"},
        ):
            with self.subTest(body=body):
                status, order_id, _ = classify_broker_response(body)
                self.assertIs(status, OrderIntentStatus.UNKNOWN)
                self.assertIsNone(order_id)

    def test_regression_r9_kite_input_exception_is_an_explicit_rejection(self):
        """The broker evaluated the request and declined it: no order exists."""
        status, order_id, detail = classify_broker_response(
            {"status": "error", "error_type": "InputException",
             "message": "Invalid `tradingsymbol`"}
        )
        self.assertIs(status, OrderIntentStatus.REJECTED)
        self.assertIsNone(order_id)
        self.assertEqual(detail, "Invalid `tradingsymbol`")

    def test_every_documented_refusal_class_rejects(self):
        for error_type in ("InputException", "OrderException", "MarginException",
                           "PermissionException", "TokenException"):
            with self.subTest(error_type=error_type):
                status, _, _ = classify_broker_response(
                    {"status": "error", "error_type": error_type, "message": "no"}
                )
                self.assertIs(status, OrderIntentStatus.REJECTED)

    def test_regression_r9_server_errors_are_unknown_even_when_named_a_refusal(self):
        """A 5xx is the server failing to answer, not the server declining."""
        status, _, detail = classify_broker_response(
            {"status": "error", "error_type": "OrderException", "status_code": 503,
             "message": "Service Unavailable"}
        )
        self.assertIs(status, OrderIntentStatus.UNKNOWN)
        self.assertIn("503", detail)

    def test_numeric_error_codes_are_not_mistaken_for_http_statuses(self):
        """Alpaca's `code` is an error code, not a 5xx."""
        status, order_id, _ = classify_broker_response(
            {"status": "new", "id": "A1", "code": 40310000}
        )
        self.assertIs(status, OrderIntentStatus.PLACED)
        self.assertEqual(order_id, "A1")

    def test_non_mapping_response_is_unknown(self):
        for body in (None, "OK", 200, ["order"]):
            with self.subTest(body=body):
                status, _, _ = classify_broker_response(body)
                self.assertIs(status, OrderIntentStatus.UNKNOWN)


class TestOrderLedger(unittest.TestCase):

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.key = make_idempotency_key("s", "NIFTY", "BUY", 1)

    def test_record_intent_writes_pending(self):
        self.assertTrue(self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500))
        row = self.ledger.get_order(self.key)
        self.assertEqual(row["status"], PENDING)
        self.assertEqual(row["symbol"], "NIFTY")
        self.assertEqual(row["quantity"], 50)

    def test_duplicate_intent_is_refused_not_overwritten(self):
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500)
        self.assertFalse(self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 999, 1))
        self.assertEqual(self.ledger.get_order(self.key)["quantity"], 50)

    def test_unresolved_tracks_pending_and_unknown_only(self):
        self.ledger.record_intent(self.key, "s", "NIFTY")
        self.assertIn(self.key, self.ledger.unresolved())
        self.ledger.update_status(self.key, UNKNOWN, rejection_reason="timeout")
        self.assertIn(self.key, self.ledger.unresolved())
        self.ledger.update_status(self.key, PLACED, broker_order_id="b123")
        self.assertNotIn(self.key, self.ledger.unresolved())

    def test_regression_r6_terminal_states_reject_further_transitions(self):
        self.ledger.record_intent(self.key, "s", "NIFTY")
        self.ledger.update_status(self.key, PLACED, broker_order_id="b123")
        with self.assertRaises(IllegalStateTransition):
            self.ledger.update_status(self.key, PENDING)
        with self.assertRaises(IllegalStateTransition):
            self.ledger.update_status(self.key, REJECTED, rejection_reason="late")
        row = self.ledger.get_order(self.key)
        self.assertEqual(row["status"], PLACED)
        self.assertEqual(row["broker_order_id"], "b123")

    def test_regression_r6_broker_order_id_is_never_wiped(self):
        self.ledger.record_intent(self.key, "s", "NIFTY")
        self.ledger.update_status(self.key, UNKNOWN, rejection_reason="timeout")
        self.ledger.update_status(self.key, PLACED, broker_order_id="b123")
        # A same-state rewrite that omits the id must preserve it.
        self.ledger.update_status(self.key, PLACED)
        self.assertEqual(self.ledger.get_order(self.key)["broker_order_id"], "b123")

    def test_update_status_rejects_unknown_status_value(self):
        self.ledger.record_intent(self.key, "s", "NIFTY")
        with self.assertRaises(ValueError):
            self.ledger.update_status(self.key, "PARTIALLY_FILLED")

    def test_update_status_on_missing_key_returns_false(self):
        self.assertFalse(self.ledger.update_status("no-such-key", PLACED))

    def test_linked_broker_order_ids_splits_sliced_ids(self):
        self.ledger.record_intent(self.key, "s", "NIFTY")
        self.ledger.update_status(self.key, PLACED, broker_order_id="A1,A2")
        self.assertEqual(self.ledger.linked_broker_order_ids(), {"A1", "A2"})

    def test_release_intent_frees_the_key_and_archives_the_row(self):
        """The claim is dropped, but the placement history is not."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500)
        self.ledger.update_status(self.key, UNKNOWN, rejection_reason="timeout")

        self.assertTrue(self.ledger.release_intent(self.key, "proved absent"))
        self.assertIsNone(self.ledger.get_order(self.key))
        self.assertEqual(self.ledger.unresolved(), [])
        # The key can be claimed again — that is the whole point of releasing.
        self.assertTrue(self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500))

        archived = self.ledger.released_history(self.key)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["status_at_release"], UNKNOWN)
        self.assertEqual(archived[0]["symbol"], "NIFTY")
        self.assertEqual(archived[0]["release_reason"], "proved absent")

    def test_release_intent_refuses_a_terminal_row(self):
        """Releasing a PLACED row would license a second send of a live order."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500)
        self.ledger.update_status(self.key, PLACED, broker_order_id="b123")
        with self.assertRaises(IllegalStateTransition):
            self.ledger.release_intent(self.key, "absent")
        self.assertEqual(self.ledger.get_order(self.key)["status"], PLACED)
        self.assertEqual(self.ledger.released_history(), [])

    def test_release_intent_on_an_unknown_key_returns_false(self):
        self.assertFalse(self.ledger.release_intent("no-such-key"))

    def test_regression_r8_unknown_cannot_be_silently_re_armed_to_pending(self):
        """Re-arming an unresolved intent must go through the audited release."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500)
        self.ledger.update_status(self.key, UNKNOWN, rejection_reason="timeout")
        with self.assertRaises(IllegalStateTransition):
            self.ledger.update_status(self.key, PENDING)
        self.assertEqual(self.ledger.get_order(self.key)["status"], UNKNOWN)

    def test_record_intent_requires_strategy_id_and_symbol(self):
        """A row describing an order nobody placed is unmatchable at reconcile time."""
        with self.assertRaises(TypeError):
            self.ledger.record_intent(self.key)
        with self.assertRaises(TypeError):
            self.ledger.record_intent(self.key, "s")
        with self.assertRaises(ValueError):
            self.ledger.record_intent(self.key, "s", "   ")
        with self.assertRaises(ValueError):
            self.ledger.record_intent(self.key, "", "NIFTY")
        self.assertIsNone(self.ledger.get_order(self.key))

    def test_intent_survives_a_simulated_process_restart(self):
        """The write-ahead record is worthless if it does not outlive the process."""
        path = os.path.join(tempfile.mkdtemp(), "ledger.db")
        with OrderLedger(db_path=path) as ledger:
            ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50, 19500)
        with OrderLedger(db_path=path) as reopened:
            self.assertEqual(reopened.get_order(self.key)["status"], PENDING)
            self.assertEqual(reopened.unresolved(), [self.key])

    def test_closed_ledger_raises_rather_than_silently_losing_writes(self):
        ledger = OrderLedger(db_path=":memory:")
        ledger.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            ledger.record_intent(self.key, "s", "NIFTY")


class TestPlaceOrder(unittest.TestCase):

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.alerts = []
        self.router = IdempotentOrderRouter(self.ledger, alert_fn=self.alerts.append)

    def test_intent_is_pending_in_the_ledger_before_the_broker_is_called(self):
        seen = {}

        def broker(key, *args):
            seen["status"] = self.ledger.get_order(key)["status"]
            return ok_response()

        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker)
        self.assertEqual(seen["status"], PENDING)

    def test_successful_placement(self):
        ok, status, broker_id = self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100, lambda *a: ok_response()
        )
        self.assertTrue(ok)
        self.assertEqual(status, "PLACED")
        self.assertEqual(broker_id, "BROKER_ORDER_999")

    def test_duplicate_call_after_success_skips_the_network(self):
        broker = Mock(return_value=ok_response())
        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker)
        ok, status, broker_id = self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker
        )
        self.assertTrue(ok)
        self.assertEqual(status, "ALREADY_PLACED")
        self.assertEqual(broker_id, "BROKER_ORDER_999")
        broker.assert_called_once()

    def test_rejection_is_terminal_and_not_retried(self):
        broker = Mock(return_value={"status": "rejected", "message": "Insufficient margin"})
        ok, status, _ = self.router.place_order("s", "N", "BUY", 1, 1, 1, broker)
        self.assertFalse(ok)
        self.assertEqual(status, "REJECTED: Insufficient margin")

        ok, status, _ = self.router.place_order("s", "N", "BUY", 1, 1, 1, broker)
        self.assertFalse(ok)
        self.assertEqual(status, "ALREADY_REJECTED: Insufficient margin")
        broker.assert_called_once()

    def test_regression_r1_timeout_does_not_re_send_on_the_next_call(self):
        """The core defect: a lost response must never license a blind re-send."""
        broker = Mock(side_effect=TimeoutError("response lost"))
        ok, status, _ = self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        self.assertEqual(self.ledger.get_order(self.ledger.unresolved()[0])["status"], UNKNOWN)

        broker.side_effect = None
        broker.return_value = ok_response("SECOND_ORDER")
        ok, status, _ = self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        self.assertEqual(broker.call_count, 1, "second call must not reach the broker")
        self.assertTrue(self.alerts, "an unresolved intent must raise an operator alert")

    def test_indeterminate_response_does_not_re_send_either(self):
        broker = Mock(return_value={"status": "PUT ORDER REQ RECEIVED"})
        ok, status, _ = self.router.place_order("s", "N", "BUY", 1, 1, 1, broker)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        self.router.place_order("s", "N", "BUY", 1, 1, 1, broker)
        broker.assert_called_once()

    def test_at_most_one_broker_call_per_place_order_invocation(self):
        broker = Mock(side_effect=TimeoutError("lost"))
        book = Mock(return_value=[])
        self.router.place_order("s", "N", "BUY", 1, 1, 1, broker, broker_order_book_fn=book)
        broker.assert_called_once()

    def test_regression_r4_concurrent_calls_send_once(self):
        calls = []
        start = threading.Barrier(8)

        def broker(key, *args):
            calls.append(key)
            return ok_response()

        def worker():
            start.wait(timeout=10)
            self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(len(calls), 1)

    def test_rejects_invalid_order_parameters(self):
        broker = Mock()
        with self.assertRaises(ValueError):
            self.router.place_order("s", "N", "BUY", 0, 1, 1, broker)
        with self.assertRaises(ValueError):
            self.router.place_order("s", "N", "BUY", -1, 1, 1, broker)
        with self.assertRaises(ValueError):
            self.router.place_order("s", "N", "BUY", 1, -0.5, 1, broker)
        broker.assert_not_called()

    def test_sequence_allows_two_identical_child_slices(self):
        broker = Mock(side_effect=[ok_response("C1"), ok_response("C2")])
        first = self.router.place_order("s", "N", "BUY", 25, 19500.0, 100, broker, sequence=0)
        second = self.router.place_order("s", "N", "BUY", 25, 19500.0, 100, broker, sequence=1)
        self.assertEqual((first[1], first[2]), ("PLACED", "C1"))
        self.assertEqual((second[1], second[2]), ("PLACED", "C2"))
        self.assertEqual(broker.call_count, 2)


class TestReconciliation(unittest.TestCase):

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.alerts = []
        self.router = IdempotentOrderRouter(self.ledger, alert_fn=self.alerts.append)
        self.timeout_broker = Mock(side_effect=TimeoutError("response lost"))

    def _place(self, book_fn, router=None, **kwargs):
        router = router or self.router
        return router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100,
            self.timeout_broker, broker_order_book_fn=book_fn, **kwargs
        )

    def test_echoed_key_present_in_book_resolves_to_placed(self):
        key = make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0)
        book = [{"tag": key, "order_id": "LIVE_1", "status": "OPEN"}]
        ok, status, broker_id = self._place(lambda: book)
        self.assertTrue(ok)
        self.assertEqual(status, "RECONCILED_PLACED")
        self.assertEqual(broker_id, "LIVE_1")
        self.assertEqual(self.ledger.get_order(key)["status"], PLACED)
        self.timeout_broker.assert_called_once()

    def test_echoed_key_absent_from_book_is_provably_absent(self):
        ok, status, _ = self._place(lambda: [{"tag": "someone-elses-key", "order_id": "X"}])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("ABSENT_SAFE_TO_RESEND"))

    def test_matched_book_entry_in_rejected_state_resolves_to_rejected(self):
        key = make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0)
        book = [{"tag": key, "order_id": "L1", "status": "REJECTED", "message": "RMS blocked"}]
        ok, status, _ = self._place(lambda: book)
        self.assertFalse(ok)
        self.assertEqual(status, "RECONCILED_REJECTED: RMS blocked")
        self.assertEqual(self.ledger.get_order(key)["status"], REJECTED)

    def test_failed_order_book_query_is_inconclusive_never_absent(self):
        def book():
            raise ConnectionError("order book endpoint down")

        ok, status, _ = self._place(book)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_regression_r9_book_entry_reporting_error_is_inconclusive(self):
        """`error` in the book states neither working nor refused — escalate."""
        key = make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0)
        book = [{"tag": key, "order_id": "L1", "status": "error", "message": "upstream"}]
        ok, status, _ = self._place(lambda: book)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        # Not written off as REJECTED, and the entry is in the book, so the
        # claim survives and nothing is re-sent.
        self.assertEqual(self.ledger.get_order(key)["status"], UNKNOWN)
        self.assertTrue(self.alerts)

    def test_book_entry_without_an_order_id_is_inconclusive(self):
        key = make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0)
        ok, status, _ = self._place(lambda: [{"tag": key, "status": "OPEN"}])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))


class TestAttributeReconciliation(unittest.TestCase):
    """Brokers that do not echo the client key (SKILL.md workflow step 5)."""

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.alerts = []
        self.router = IdempotentOrderRouter(
            self.ledger, alert_fn=self.alerts.append, broker_echoes_key=False
        )
        self.timeout_broker = Mock(side_effect=TimeoutError("response lost"))

    def _place(self, book, **kwargs):
        return self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100,
            self.timeout_broker, broker_order_book_fn=lambda: book, **kwargs
        )

    def _entry(self, **overrides):
        entry = {
            "symbol": "NIFTY", "side": "BUY", "quantity": 50.0, "price": 19500.0,
            "order_id": "LIVE_1", "status": "OPEN",
        }
        entry.update(overrides)
        # Stamp inside the default 300s window relative to the intent row.
        entry.setdefault("order_timestamp", None)
        if entry["order_timestamp"] is None:
            entry["order_timestamp"] = self._intent_created_at()
        return entry

    def _intent_created_at(self):
        keys = self.ledger.unresolved()
        if not keys:
            import time
            return time.time()
        return self.ledger.get_order(keys[0])["created_at"]

    def test_exact_attribute_match_inside_the_window_resolves(self):
        # Seed the intent first so the book entry can be stamped against it.
        self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100, self.timeout_broker
        )
        ok, status, broker_id = self._place([self._entry()])
        self.assertTrue(ok)
        self.assertEqual(status, "RECONCILED_PLACED")
        self.assertEqual(broker_id, "LIVE_1")

    def test_regression_r2_price_mismatch_is_not_a_match(self):
        """The 1.0.0 matcher ignored price and adopted an unrelated order."""
        ok, status, _ = self._place([self._entry(price=19000.0, order_id="YESTERDAY")])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_regression_r2_entry_outside_the_time_window_is_not_a_match(self):
        stale = self._entry(order_id="LAST_WEEK", order_timestamp=1.0)
        ok, status, _ = self._place([stale])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_regression_r2_untimestamped_entry_is_not_a_match(self):
        entry = self._entry()
        entry.pop("order_timestamp")
        ok, status, _ = self._place([entry])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_regression_r2_order_already_claimed_by_another_intent_is_excluded(self):
        other_key = make_idempotency_key("s", "NIFTY", "BUY", 99, 50.0, 19500.0)
        self.ledger.record_intent(other_key, "s", "NIFTY", "BUY", 50.0, 19500.0)
        self.ledger.update_status(other_key, PLACED, broker_order_id="LIVE_1")

        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, self.timeout_broker)
        ok, status, _ = self._place([self._entry(order_id="LIVE_1")])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_two_equally_plausible_orders_escalate_rather_than_guess(self):
        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, self.timeout_broker)
        book = [self._entry(order_id="A"), self._entry(order_id="B")]
        ok, status, _ = self._place(book)
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_non_numeric_quantity_in_the_book_is_not_a_match(self):
        """A NaN comparison must fail the filter, not slip through it."""
        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, self.timeout_broker)
        ok, status, _ = self._place([self._entry(quantity="fifty")])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_millisecond_timestamps_fall_outside_the_window_rather_than_matching(self):
        self.router.place_order("s", "NIFTY", "BUY", 50.0, 19500.0, 100, self.timeout_broker)
        entry = self._entry()
        entry["order_timestamp"] = entry["order_timestamp"] * 1000.0
        ok, status, _ = self._place([entry])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))

    def test_absence_is_never_evidence_when_the_key_is_not_echoed(self):
        ok, status, _ = self._place([])
        self.assertFalse(ok)
        self.assertNotIn("ABSENT", status)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        self.assertTrue(self.alerts)


class TestStartupRecovery(unittest.TestCase):
    """Regression R7 — the documented startup sweep now exists."""

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.alerts = []
        self.router = IdempotentOrderRouter(self.ledger, alert_fn=self.alerts.append)

    def test_sweep_links_live_orders_and_clears_absent_ones(self):
        live = make_idempotency_key("s", "NIFTY", "BUY", 11)
        dead = make_idempotency_key("s", "NIFTY", "SELL", 12)
        self.ledger.record_intent(live, "s", "NIFTY", "BUY", 50, 19500)
        self.ledger.record_intent(dead, "s", "NIFTY", "SELL", 50, 19500)

        results = self.router.recover_unresolved(
            lambda: [{"client_order_id": live, "order_id": "LIVE_1", "status": "OPEN"}]
        )

        self.assertIs(results[live].outcome, ReconcileOutcome.FOUND_PLACED)
        self.assertIs(results[dead].outcome, ReconcileOutcome.ABSENT)
        self.assertEqual(self.ledger.get_order(live)["status"], PLACED)
        self.assertEqual(self.ledger.get_order(live)["broker_order_id"], "LIVE_1")
        # "Cleared" has to mean the sweep actually finished with it: an ABSENT
        # row left in unresolved() blocks the strategy forever and re-reconciles
        # to ABSENT on every subsequent sweep.
        self.assertNotIn(dead, self.ledger.unresolved())
        self.assertEqual(self.ledger.unresolved(), [])
        self.assertEqual(self.ledger.released_history(dead)[0]["side"], "SELL")
        self.assertEqual(self.alerts, [])

    def test_unresolvable_intent_alerts_and_stays_unresolved(self):
        stuck = make_idempotency_key("s", "NIFTY", "BUY", 13)
        self.ledger.record_intent(stuck, "s", "NIFTY", "BUY", 50, 19500)

        def book():
            raise ConnectionError("broker unreachable")

        results = self.router.recover_unresolved(book)
        self.assertFalse(results[stuck].resolved)
        self.assertIn(stuck, self.ledger.unresolved())
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("do not", self.alerts[0].lower())

    def test_stale_after_filter_skips_fresh_intents(self):
        fresh = make_idempotency_key("s", "NIFTY", "BUY", 14)
        self.ledger.record_intent(fresh, "s", "NIFTY", "BUY", 50, 19500)
        results = self.router.recover_unresolved(lambda: [], stale_after_s=3600)
        self.assertEqual(results, {})
        self.assertIn(fresh, self.ledger.unresolved())

    def test_sweep_is_empty_once_everything_is_terminal(self):
        key = make_idempotency_key("s", "NIFTY", "BUY", 15)
        self.ledger.record_intent(key, "s", "NIFTY")
        self.ledger.update_status(key, PLACED, broker_order_id="B1")
        self.assertEqual(self.router.recover_unresolved(lambda: []), {})


class TestAbsentResend(unittest.TestCase):
    """Regression R8 — an ABSENT verdict must lead to an actual re-send.

    Before the fix, ``ABSENT_SAFE_TO_RESEND`` told the caller to call
    ``place_order`` again, but the ledger row stayed in ``PENDING``/``UNKNOWN``.
    The re-invocation hit the primary-key claim, reconciled, concluded ABSENT
    again, and looped — the order was never sent, and the intent never settled.
    """

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.alerts = []
        self.router = IdempotentOrderRouter(self.ledger, alert_fn=self.alerts.append)
        self.key = make_idempotency_key("s", "NIFTY", "BUY", 100, 50.0, 19500.0)

    def _place(self, broker, book):
        return self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker,
            broker_order_book_fn=lambda: book,
        )

    def test_regression_r8_re_invocation_after_absent_sends_exactly_once(self):
        """Intent, timeout, reconcile to ABSENT, re-invoke: one send, settled."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50.0, 19500.0)
        self.ledger.update_status(
            self.key, UNKNOWN, rejection_reason="TimeoutError: response lost"
        )

        results = self.router.recover_unresolved(lambda: [])
        self.assertIs(results[self.key].outcome, ReconcileOutcome.ABSENT)

        broker = Mock(return_value=ok_response("RESENT_1"))
        ok, status, broker_id = self._place(broker, [])

        broker.assert_called_once()
        self.assertTrue(ok)
        self.assertEqual(status, "PLACED")
        self.assertEqual(broker_id, "RESENT_1")
        row = self.ledger.get_order(self.key)
        self.assertEqual(row["status"], PLACED)
        self.assertEqual(row["broker_order_id"], "RESENT_1")
        self.assertEqual(self.ledger.unresolved(), [])
        self.assertEqual(self.alerts, [])

    def test_regression_r8_place_order_re_sends_a_crash_left_intent_proven_absent(self):
        """One place_order call: reconcile the stale claim, then send once."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50.0, 19500.0)
        broker = Mock(return_value=ok_response("RESENT_2"))

        ok, status, broker_id = self._place(
            broker, [{"tag": "someone-elses-key", "order_id": "X", "status": "OPEN"}]
        )

        broker.assert_called_once()  # the one-send-per-call invariant still holds
        self.assertTrue(ok)
        self.assertEqual(status, "PLACED")
        self.assertEqual(broker_id, "RESENT_2")
        self.assertEqual(self.ledger.get_order(self.key)["status"], PLACED)

    def test_absent_after_this_calls_own_send_defers_the_re_send(self):
        """The send already happened here; a second one would be two per call."""
        broker = Mock(side_effect=[TimeoutError("lost"), ok_response("RESENT_3")])

        ok, status, _ = self._place(broker, [])
        self.assertFalse(ok)
        self.assertTrue(status.startswith("ABSENT_SAFE_TO_RESEND"))
        self.assertEqual(broker.call_count, 1)
        self.assertEqual(self.ledger.unresolved(), [])

        ok, status, broker_id = self._place(broker, [])
        self.assertTrue(ok)
        self.assertEqual((status, broker_id), ("PLACED", "RESENT_3"))
        self.assertEqual(broker.call_count, 2)

    def test_an_inconclusive_verdict_still_never_re_sends(self):
        """Only proven absence releases the claim — never a failed query."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50.0, 19500.0)
        broker = Mock(return_value=ok_response("MUST_NOT_SEND"))

        def book():
            raise ConnectionError("order book endpoint down")

        ok, status, _ = self.router.place_order(
            "s", "NIFTY", "BUY", 50.0, 19500.0, 100, broker,
            broker_order_book_fn=book,
        )
        self.assertFalse(ok)
        self.assertTrue(status.startswith("UNRESOLVED_REQUIRES_RECONCILIATION"))
        broker.assert_not_called()
        self.assertEqual(self.ledger.get_order(self.key)["status"], PENDING)

    def test_a_placed_intent_is_never_re_sent_by_the_absent_path(self):
        """Reconciliation that finds the order keeps the claim and the id."""
        self.ledger.record_intent(self.key, "s", "NIFTY", "BUY", 50.0, 19500.0)
        broker = Mock(return_value=ok_response("MUST_NOT_SEND"))
        book = [{"tag": self.key, "order_id": "LIVE_9", "status": "OPEN"}]

        ok, status, broker_id = self._place(broker, book)
        broker.assert_not_called()
        self.assertTrue(ok)
        self.assertEqual((status, broker_id), ("RECONCILED_PLACED", "LIVE_9"))
        self.assertEqual(self.ledger.released_history(), [])


class TestBackwardCompatibility(unittest.TestCase):
    """The older import surface stays importable and behaves sensibly."""

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.addCleanup(self.ledger.close)
        self.router = IdempotentOrderRouter(self.ledger)

    def test_status_constants(self):
        self.assertEqual((PENDING, PLACED, REJECTED, UNKNOWN),
                         ("PENDING", "PLACED", "REJECTED", "UNKNOWN"))
        self.assertEqual(OrderIntentStatus.PENDING.value, PENDING)

    def test_ledger_defaults_and_status_round_trip(self):
        key = make_idempotency_key("s1", "sym", "BUY", 100)
        self.ledger.record_intent(key, "s1", "sym")
        self.assertIn(key, self.ledger.unresolved())
        self.ledger.update_status(key, PLACED, broker_order_id="b123")
        self.assertNotIn(key, self.ledger.unresolved())

    def test_reconciliation_conflict_on_a_terminal_row_escalates(self):
        """Broker and ledger disagreeing is escalated, never silently overwritten."""
        alerts = []
        router = IdempotentOrderRouter(self.ledger, alert_fn=alerts.append)
        key = make_idempotency_key("s", "NIFTY", "BUY", 1)
        self.ledger.record_intent(key, "s", "NIFTY", "BUY", 50, 19500)
        self.ledger.update_status(key, REJECTED, rejection_reason="margin")

        book = [{"client_order_id": key, "order_id": "LIVE_1", "status": "OPEN"}]
        self.assertIsNone(router._reconcile_unknown(key, lambda: book))
        self.assertEqual(self.ledger.get_order(key)["status"], REJECTED)
        self.assertEqual(len(alerts), 1)
        self.assertIn("conflict", alerts[0].lower())

    def test_legacy_reconcile_unknown_shim(self):
        key = make_idempotency_key("s", "NIFTY", "BUY", 1)
        self.ledger.record_intent(key, "s", "NIFTY", "BUY", 50, 19500)
        book = [{"client_order_id": key, "order_id": "L9", "status": "OPEN"}]
        self.assertEqual(self.router._reconcile_unknown(key, lambda: book), "L9")
        self.assertIsNone(self.router._reconcile_unknown("missing-key", lambda: book))


if __name__ == "__main__":
    unittest.main()
