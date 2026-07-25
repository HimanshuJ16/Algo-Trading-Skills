"""
Unit tests for broker-api-idempotent-cancel-requests skill.
"""
import unittest
import threading
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

class MockRetryHandler:
    def __init__(self):
        self.calls = 0

    def mock_http_cancel_5xx_retry(self, order_id, client_cancel_id):
        self.calls += 1
        if self.calls < 3:
            return 502, {"error": "Bad Gateway"}
        return 200, {"status": "cancelled"}
        
    def mock_http_cancel_exception(self, order_id, client_cancel_id):
        self.calls += 1
        raise ConnectionError("Network reset")


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
        
    def test_retry_on_5xx(self):
        handler = MockRetryHandler()
        mgr = IdempotentCancelManager(
            http_cancel_fn=handler.mock_http_cancel_5xx_retry, 
            max_retries=3, 
            base_backoff_ms=1
        )
        res = mgr.cancel_order_idempotent("ORD_1004")
        self.assertEqual(res.status, CancelStatus.CANCELLED)
        self.assertEqual(handler.calls, 3)

    def test_failure_on_exception(self):
        handler = MockRetryHandler()
        mgr = IdempotentCancelManager(
            http_cancel_fn=handler.mock_http_cancel_exception, 
            max_retries=1, 
            base_backoff_ms=1
        )
        res = mgr.cancel_order_idempotent("ORD_1005")
        self.assertEqual(res.status, CancelStatus.FAILED)
        self.assertIn("network reset", res.message)

    def test_thread_safety_cache_eviction(self):
        mgr = IdempotentCancelManager(http_cancel_fn=mock_http_cancel_success, max_cache_size=10)
        
        def worker(i):
            mgr.cancel_order_idempotent(f"ORD_{i}", f"CID_{i}")
            
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
            
        # Cache eviction should ensure only 10 items remain
        self.assertEqual(len(mgr._cancel_history), 10)


if __name__ == "__main__":
    unittest.main()
