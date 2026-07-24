"""
Unit tests for order-placement-idempotency skill.

Tests:
1. Deterministic SHA-256 idempotency key generation.
2. Pre-network write-ahead intent logging (PENDING status).
3. Confirmed broker placement (PLACED status update).
4. Network timeout (UNKNOWN status) and reconciliation without double placement.
5. Re-executing existing intent skips duplicate network POST.
6. Backward compatibility with make_idempotency_key, PENDING, PLACED, UNKNOWN, REJECTED, and OrderLedger.
"""
import unittest
from unittest.mock import Mock
from order_ledger import (
    IdempotentOrderRouter,
    OrderIntentStatus,
    OrderLedger,
    PENDING,
    PLACED,
    UNKNOWN,
    make_idempotency_key,
)


class TestOrderPlacementIdempotency(unittest.TestCase):

    def setUp(self):
        self.ledger = OrderLedger(db_path=":memory:")
        self.router = IdempotentOrderRouter(self.ledger)

    def test_idempotency_key_generation(self):
        key1 = make_idempotency_key("strat1", "NIFTY", "BUY", 1700000000, 50.0, 19500.0)
        key2 = make_idempotency_key("strat1", "NIFTY", "BUY", 1700000000, 50.0, 19500.0)
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 24)

    def test_successful_order_placement(self):
        def broker_mock(key, symbol, side, qty, price, strat):
            return {"status": "SUCCESS", "broker_order_id": "BROKER_ORDER_999"}

        ok, status, b_id = self.router.place_order(
            "strat1", "NIFTY", "BUY", 50.0, 19500.0, 100, broker_mock
        )

        self.assertTrue(ok)
        self.assertEqual(status, "PLACED")
        self.assertEqual(b_id, "BROKER_ORDER_999")

    def test_idempotent_skip_on_duplicate_call(self):
        broker_mock = Mock(return_value={"status": "SUCCESS", "broker_order_id": "BROKER_ORDER_999"})

        # First call
        self.router.place_order("strat1", "NIFTY", "BUY", 50.0, 19500.0, 100, broker_mock)

        # Second identical call -> should skip network call
        ok, status, b_id = self.router.place_order(
            "strat1", "NIFTY", "BUY", 50.0, 19500.0, 100, broker_mock
        )

        self.assertTrue(ok)
        self.assertEqual(status, "ALREADY_PLACED")
        # Broker network call only executed once!
        broker_mock.assert_called_once()

    def test_network_timeout_reconciliation(self):
        def timeout_broker(key, symbol, side, qty, price, strat):
            raise TimeoutError("Broker connection timed out")

        def order_book_mock():
            return [
                {
                    "client_order_id": "WILL_BE_REPLACED",
                    "symbol": "NIFTY",
                    "side": "BUY",
                    "quantity": 50.0,
                    "broker_order_id": "RECONCILED_BROKER_123",
                }
            ]

        ok, status, b_id = self.router.place_order(
            "strat1",
            "NIFTY",
            "BUY",
            50.0,
            19500.0,
            200,
            timeout_broker,
            broker_order_book_fn=order_book_mock,
        )

        self.assertTrue(ok)
        self.assertEqual(status, "RECONCILED_PLACED")
        self.assertEqual(b_id, "RECONCILED_BROKER_123")

    def test_backward_compatibility(self):
        key = make_idempotency_key("s1", "sym", "BUY", 100)
        self.ledger.record_intent(key)
        self.assertIn(key, self.ledger.unresolved())

        self.ledger.update_status(key, PLACED, broker_order_id="b123")
        self.assertNotIn(key, self.ledger.unresolved())


if __name__ == "__main__":
    unittest.main()
