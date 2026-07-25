"""
Unit tests for broker-api-idempotent-cancel-requests skill.
"""
import unittest
from cancel_manager import CancelStatus, IdempotentCancelManager


def mock_http_cancel_success(order_id, client_cancel_id):
    """Mock HTTP transport returning 200 OK."""
    return 200, {"status": "cancelled", "id": order_id}


def mock_http_cancel_filled_race(order_id, client_cancel_id):
    """Mock HTTP transport returning 400 Already Filled."""
    return 400, {"detail": "Order already filled on exchange."}


def mock_http_cancel_already_cancelled(order_id, client_cancel_id):
    """Mock HTTP transport returning 404 Not Found."""
    return 404, {"detail": "Order not found or already cancelled."}


class TestIdempotentCancelManager(unittest.TestCase):

    def test_successful_cancel_and_idempotent_retry(self):
        mgr = IdempotentCancelManager(http_cancel_fn=mock_http_cancel_success)
        cid = "CANCEL_ORD_1001_1"

        # 1st cancel call -> 200 OK
        res1 = mgr.cancel_order_idempotent("ORD_1001", client_cancel_id=cid)
        self.assertEqual(res1.status, CancelStatus.CANCELLED)
        self.assertFalse(res1.is_idempotent_retry)

        # 2nd cancel call with same cid -> Cached Idempotent Return
        res2 = mgr.cancel_order_idempotent("ORD_1001", client_cancel_id=cid)
        self.assertEqual(res2.status, CancelStatus.CANCELLED)
        self.assertTrue(res2.is_idempotent_retry)

    def test_cancel_vs_fill_race_condition(self):
        mgr = IdempotentCancelManager(http_cancel_fn=mock_http_cancel_filled_race)
        res = mgr.cancel_order_idempotent("ORD_1002")

        self.assertEqual(res.status, CancelStatus.FILLED_BEFORE_CANCEL)
        self.assertIn("Cancel-vs-Fill Race", res.message)

    def test_already_cancelled_handling(self):
        mgr = IdempotentCancelManager(http_cancel_fn=mock_http_cancel_already_cancelled)
        res = mgr.cancel_order_idempotent("ORD_1003")

        self.assertEqual(res.status, CancelStatus.ALREADY_CANCELLED)


if __name__ == "__main__":
    unittest.main()
