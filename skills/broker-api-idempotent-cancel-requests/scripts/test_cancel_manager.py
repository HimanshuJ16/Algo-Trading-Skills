"""
Unit tests for broker-api-idempotent-cancel-requests.

The tests are organised around the safety properties the skill claims, because those
are what a regression would break:

  * an HTTP 2xx is an acknowledgement, not a cancellation;
  * an indeterminate outcome is never cached as terminal, so a later retry under the
    same cancel id genuinely reaches the broker;
  * no ambiguous broker response is ever reported as "the order is dead";
  * concurrent callers of one cancel id produce exactly one broker dispatch.
"""
import logging
import random
import threading
import time
import unittest

from cancel_manager import (
    CancelResult,
    CancelStatus,
    IdempotentCancelManager,
)

logging.disable(logging.CRITICAL)


class RecordingTransport:
    """Transport returning a scripted sequence of responses, counting dispatches."""

    def __init__(self, *responses, repeat_last=True):
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.calls = 0
        self.seen = []

    def __call__(self, order_id, client_cancel_id):
        self.calls += 1
        self.seen.append((order_id, client_cancel_id))
        index = min(self.calls - 1, len(self.responses) - 1) if self.repeat_last else self.calls - 1
        item = self.responses[index]
        if isinstance(item, Exception):
            raise item
        return item


def _manager(transport, **kwargs):
    """Manager with deterministic, non-blocking timing unless a test says otherwise."""
    kwargs.setdefault("base_backoff_ms", 1)
    kwargs.setdefault("jitter_ratio", 0.0)
    kwargs.setdefault("sleep_fn", lambda _s: None)
    return IdempotentCancelManager(http_cancel_fn=transport, **kwargs)


class TestAcknowledgementIsNotCancellation(unittest.TestCase):
    """A 2xx means the cancel request was accepted; the order can still fill."""

    def test_http_200_is_pending_cancel_not_cancelled(self):
        mgr = _manager(RecordingTransport((200, {"status": "pending_cancel"})))
        res = mgr.cancel_order_idempotent("ORD_1")

        self.assertEqual(res.status, CancelStatus.PENDING_CANCEL)
        self.assertFalse(res.is_terminal)
        self.assertTrue(res.requires_reconciliation)

    def test_http_204_is_pending_cancel(self):
        # Alpaca's DELETE /v2/orders/{id} answers 204 while the order sits in
        # pending_cancel until the execution venue confirms.
        mgr = _manager(RecordingTransport((204, {})))
        self.assertEqual(
            mgr.cancel_order_idempotent("ORD_2").status, CancelStatus.PENDING_CANCEL
        )

    def test_synchronous_broker_can_opt_into_terminal_ack(self):
        mgr = _manager(
            RecordingTransport((200, {"status": "CANCELED"})), treat_ack_as_cancelled=True
        )
        res = mgr.cancel_order_idempotent("ORD_3")

        self.assertEqual(res.status, CancelStatus.CANCELLED)
        self.assertTrue(res.is_terminal)
        self.assertFalse(res.requires_reconciliation)


class TestIndeterminateOutcomesStayRetryable(unittest.TestCase):
    """Regression tests for the cache-poisoning defect.

    Previously an exhausted retry sequence was classified ``FAILED`` and written to the
    idempotency cache. Every later retry under the same cancel id then replayed that
    cached failure without contacting the broker, leaving a live order permanently
    un-cancellable.
    """

    def test_exhausted_5xx_is_unknown_not_failed(self):
        transport = RecordingTransport((503, {"error": "upstream unavailable"}))
        mgr = _manager(transport, max_retries=2)
        res = mgr.cancel_order_idempotent("ORD_10", "CID_10")

        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertEqual(res.attempts, 3)
        self.assertTrue(res.requires_reconciliation)

    def test_exhausted_5xx_is_not_cached(self):
        transport = RecordingTransport((503, {"error": "upstream unavailable"}))
        mgr = _manager(transport, max_retries=1)
        mgr.cancel_order_idempotent("ORD_11", "CID_11")

        self.assertIsNone(mgr.get_cached_result("CID_11"))

    def test_retry_after_transport_failure_reaches_the_broker_again(self):
        transport = RecordingTransport(
            ConnectionError("connection reset"), (200, {}), repeat_last=False
        )
        mgr = _manager(transport, max_retries=0)

        first = mgr.cancel_order_idempotent("ORD_12", "CID_12")
        self.assertEqual(first.status, CancelStatus.UNKNOWN)
        self.assertEqual(transport.calls, 1)

        second = mgr.cancel_order_idempotent("ORD_12", "CID_12")
        self.assertEqual(transport.calls, 2, "a cached UNKNOWN would have skipped the broker")
        self.assertEqual(second.status, CancelStatus.PENDING_CANCEL)
        self.assertFalse(second.is_idempotent_retry)

    def test_transport_exception_never_propagates(self):
        mgr = _manager(RecordingTransport(RuntimeError("boom")), max_retries=1)
        res = mgr.cancel_order_idempotent("ORD_13")

        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertIn("RuntimeError", res.message)

    def test_malformed_transport_return_is_contained(self):
        mgr = _manager(RecordingTransport("not-a-tuple"), max_retries=0)
        res = mgr.cancel_order_idempotent("ORD_14")

        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertIn("TypeError", res.message)

    def test_stale_payload_from_an_earlier_attempt_is_not_reused(self):
        # Attempt 1 returns a 502 body; attempt 2 raises. The classification must
        # describe the transport failure, not the stale "bad gateway" text.
        transport = RecordingTransport(
            (502, {"error": "bad gateway"}), ConnectionError("reset"), repeat_last=False
        )
        mgr = _manager(transport, max_retries=1)
        res = mgr.cancel_order_idempotent("ORD_15")

        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertEqual(res.detail, "")
        self.assertIn("ConnectionError", res.message)


class TestRaceAndAmbiguityClassification(unittest.TestCase):
    """Nothing ambiguous may be reported as "the order is no longer working"."""

    def test_too_late_to_cancel_is_filled_before_cancel(self):
        mgr = _manager(RecordingTransport((400, {"detail": "Too late to cancel"})))
        res = mgr.cancel_order_idempotent("ORD_20")

        self.assertEqual(res.status, CancelStatus.FILLED_BEFORE_CANCEL)
        self.assertTrue(res.is_terminal)

    def test_alpaca_422_not_cancelable_with_fill_text(self):
        mgr = _manager(
            RecordingTransport((422, {"message": "order already filled"}))
        )
        self.assertEqual(
            mgr.cancel_order_idempotent("ORD_21").status, CancelStatus.FILLED_BEFORE_CANCEL
        )

    def test_bare_422_is_rejected_not_assumed_dead(self):
        # Alpaca answers 422 "The order status is not cancelable." without saying why.
        mgr = _manager(
            RecordingTransport((422, {"message": "The order status is not cancelable."}))
        )
        res = mgr.cancel_order_idempotent("ORD_22")

        self.assertEqual(res.status, CancelStatus.REJECTED)
        self.assertFalse(res.is_terminal)

    def test_negated_fill_text_is_not_a_fill(self):
        # "not filled" must not match the bare \bfilled\b pattern.
        mgr = _manager(
            RecordingTransport((400, {"detail": "order was not filled, cannot cancel"}))
        )
        self.assertNotEqual(
            mgr.cancel_order_idempotent("ORD_23").status, CancelStatus.FILLED_BEFORE_CANCEL
        )

    def test_partial_fill_is_not_reported_as_fully_filled(self):
        mgr = _manager(
            RecordingTransport((400, {"detail": "order partially filled"}))
        )
        res = mgr.cancel_order_idempotent("ORD_24")

        self.assertNotEqual(res.status, CancelStatus.FILLED_BEFORE_CANCEL)
        self.assertFalse(res.is_terminal)

    def test_explicit_already_cancelled_is_terminal(self):
        mgr = _manager(
            RecordingTransport((400, {"detail": "Order has already been cancelled"}))
        )
        res = mgr.cancel_order_idempotent("ORD_25")

        self.assertEqual(res.status, CancelStatus.ALREADY_CANCELLED)
        self.assertTrue(res.is_terminal)

    def test_404_not_found_is_order_unknown_not_already_cancelled(self):
        # The same "unknown order" answer covers a wrong id, symbol, or API key, where
        # the order is still live. Concluding "cancelled" here strands live exposure.
        mgr = _manager(RecordingTransport((404, {"detail": "Order not found"})))
        res = mgr.cancel_order_idempotent("ORD_26")

        self.assertEqual(res.status, CancelStatus.ORDER_UNKNOWN)
        self.assertFalse(res.is_terminal)
        self.assertTrue(res.requires_reconciliation)

    def test_binance_unknown_order_text_is_order_unknown(self):
        mgr = _manager(RecordingTransport((400, {"msg": "Unknown order sent.", "code": -2011})))
        self.assertEqual(
            mgr.cancel_order_idempotent("ORD_27").status, CancelStatus.ORDER_UNKNOWN
        )

    def test_unclassifiable_rejection_defaults_to_rejected(self):
        mgr = _manager(RecordingTransport((403, {"detail": "insufficient permissions"})))
        res = mgr.cancel_order_idempotent("ORD_28")

        self.assertEqual(res.status, CancelStatus.REJECTED)
        self.assertFalse(res.is_terminal)

    def test_error_text_is_read_from_any_supported_key(self):
        for key in ("detail", "error", "message", "msg", "reason"):
            with self.subTest(key=key):
                mgr = _manager(RecordingTransport((400, {key: "too late to cancel"})))
                self.assertEqual(
                    mgr.cancel_order_idempotent("ORD_29").status,
                    CancelStatus.FILLED_BEFORE_CANCEL,
                )

    def test_rejection_with_empty_body_is_rejected_not_terminal(self):
        mgr = _manager(RecordingTransport((400, {})))
        res = mgr.cancel_order_idempotent("ORD_30")

        self.assertEqual(res.status, CancelStatus.REJECTED)
        self.assertFalse(res.is_terminal)


class TestIdempotency(unittest.TestCase):

    def test_terminal_outcome_replays_without_a_second_dispatch(self):
        transport = RecordingTransport((400, {"detail": "too late to cancel"}))
        mgr = _manager(transport)

        first = mgr.cancel_order_idempotent("ORD_40", "CID_40")
        second = mgr.cancel_order_idempotent("ORD_40", "CID_40")

        self.assertEqual(transport.calls, 1)
        self.assertFalse(first.is_idempotent_retry)
        self.assertTrue(second.is_idempotent_retry)
        self.assertEqual(second.status, CancelStatus.FILLED_BEFORE_CANCEL)
        self.assertEqual(second.order_id, "ORD_40")

    def test_pending_cancel_replays_rather_than_re_dispatching(self):
        transport = RecordingTransport((202, {}))
        mgr = _manager(transport)

        mgr.cancel_order_idempotent("ORD_41", "CID_41")
        second = mgr.cancel_order_idempotent("ORD_41", "CID_41")

        self.assertEqual(transport.calls, 1, "a re-acked cancel is a cancel storm")
        self.assertTrue(second.is_idempotent_retry)

    def test_replay_reports_the_order_the_cancel_id_belongs_to(self):
        # An agent that reuses one cancel id across two orders must not receive a
        # confident terminal status stamped with the *wrong* order id.
        transport = RecordingTransport((400, {"detail": "too late to cancel"}))
        mgr = _manager(transport)

        mgr.cancel_order_idempotent("ORD_44", "SHARED_CID")
        replay = mgr.cancel_order_idempotent("ORD_45", "SHARED_CID")

        self.assertEqual(transport.calls, 1)
        self.assertEqual(replay.order_id, "ORD_44")
        self.assertTrue(replay.is_idempotent_retry)

    def test_very_large_retry_budget_does_not_overflow(self):
        delays = []
        mgr = _manager(
            RecordingTransport((500, {})),
            max_retries=80,
            base_backoff_ms=100,
            max_backoff_ms=1000,
            jitter_ratio=0.0,
            sleep_fn=delays.append,
        )
        res = mgr.cancel_order_idempotent("ORD_46")

        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertEqual(len(delays), 80)
        self.assertTrue(all(d <= 1.0 for d in delays))

    def test_distinct_cancel_ids_each_dispatch(self):
        transport = RecordingTransport((200, {}))
        mgr = _manager(transport)

        mgr.cancel_order_idempotent("ORD_42", "CID_42a")
        mgr.cancel_order_idempotent("ORD_42", "CID_42b")

        self.assertEqual(transport.calls, 2)

    def test_generated_ids_are_unique_and_instance_scoped(self):
        mgr = _manager(RecordingTransport((200, {})))
        ids = {mgr.generate_client_cancel_id("ORD_43") for _ in range(500)}

        self.assertEqual(len(ids), 500)
        # The id is opaque; parse from the right because order ids contain underscores.
        # Two managers (standing in for two processes, or a restart) must not mint
        # colliding ids from their independently restarted sequence counters.
        other = _manager(RecordingTransport((200, {})))
        self.assertNotEqual(
            mgr.generate_client_cancel_id("ORD_43").rsplit("_", 3)[1],
            other.generate_client_cancel_id("ORD_43").rsplit("_", 3)[1],
        )

    def test_cache_is_bounded_and_evicts_oldest_first(self):
        transport = RecordingTransport((200, {}))
        mgr = _manager(transport, max_cache_size=3)
        for i in range(5):
            mgr.cancel_order_idempotent(f"ORD_{i}", f"CID_{i}")

        self.assertIsNone(mgr.get_cached_result("CID_0"))
        self.assertIsNone(mgr.get_cached_result("CID_1"))
        self.assertIsNotNone(mgr.get_cached_result("CID_4"))

    def test_evicted_entry_is_re_dispatched_rather_than_silently_dropped(self):
        transport = RecordingTransport((200, {}))
        mgr = _manager(transport, max_cache_size=1)
        mgr.cancel_order_idempotent("ORD_50", "CID_50")
        mgr.cancel_order_idempotent("ORD_51", "CID_51")
        mgr.cancel_order_idempotent("ORD_50", "CID_50")

        self.assertEqual(transport.calls, 3)


class TestRetryPolicy(unittest.TestCase):

    def test_5xx_retries_then_succeeds(self):
        transport = RecordingTransport(
            (502, {"error": "bad gateway"}),
            (502, {"error": "bad gateway"}),
            (200, {}),
        )
        mgr = _manager(transport, max_retries=3)
        res = mgr.cancel_order_idempotent("ORD_60")

        self.assertEqual(res.status, CancelStatus.PENDING_CANCEL)
        self.assertEqual(transport.calls, 3)
        self.assertEqual(res.attempts, 3)

    def test_429_is_retried(self):
        transport = RecordingTransport((429, {"error": "rate limited"}), (200, {}))
        mgr = _manager(transport, max_retries=2)

        self.assertEqual(
            mgr.cancel_order_idempotent("ORD_61").status, CancelStatus.PENDING_CANCEL
        )
        self.assertEqual(transport.calls, 2)

    def test_client_rejection_is_not_retried(self):
        transport = RecordingTransport((400, {"detail": "too late to cancel"}))
        mgr = _manager(transport, max_retries=3)
        mgr.cancel_order_idempotent("ORD_62")

        self.assertEqual(transport.calls, 1)

    def test_max_retries_zero_dispatches_once(self):
        transport = RecordingTransport((500, {}))
        mgr = _manager(transport, max_retries=0)
        res = mgr.cancel_order_idempotent("ORD_63")

        self.assertEqual(transport.calls, 1)
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.status, CancelStatus.UNKNOWN)

    def test_backoff_is_exponential_and_capped(self):
        delays = []
        transport = RecordingTransport((500, {}))
        mgr = _manager(
            transport,
            max_retries=4,
            base_backoff_ms=100,
            max_backoff_ms=300,
            jitter_ratio=0.0,
            sleep_fn=delays.append,
        )
        mgr.cancel_order_idempotent("ORD_64")

        self.assertEqual(delays, [0.1, 0.2, 0.3, 0.3])

    def test_jitter_only_shortens_and_stays_within_ratio(self):
        delays = []
        mgr = _manager(
            RecordingTransport((500, {})),
            max_retries=1,
            base_backoff_ms=1000,
            max_backoff_ms=1000,
            jitter_ratio=0.25,
            sleep_fn=delays.append,
            rng=random.Random(7),
        )
        mgr.cancel_order_idempotent("ORD_65")

        self.assertEqual(len(delays), 1)
        self.assertGreaterEqual(delays[0], 0.75)
        self.assertLessEqual(delays[0], 1.0)

    def test_retry_after_seconds_overrides_the_schedule(self):
        delays = []
        transport = RecordingTransport((429, {"retry_after": 2}), (200, {}))
        mgr = _manager(
            transport, max_retries=2, base_backoff_ms=100, max_backoff_ms=5000,
            sleep_fn=delays.append,
        )
        mgr.cancel_order_idempotent("ORD_66")

        self.assertEqual(delays, [2.0])

    def test_retry_after_http_date_is_parsed(self):
        delays = []
        future = time.time() + 3.0
        stamp = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(future))
        transport = RecordingTransport((429, {"Retry-After": stamp}), (200, {}))
        mgr = _manager(
            transport, max_retries=1, max_backoff_ms=10000, sleep_fn=delays.append
        )
        mgr.cancel_order_idempotent("ORD_67")

        self.assertEqual(len(delays), 1)
        self.assertGreaterEqual(delays[0], 1.0)
        self.assertLessEqual(delays[0], 4.0)

    def test_retry_after_beyond_budget_returns_control_to_caller(self):
        # Binance answers 418 with a Retry-After covering an IP ban. Sleeping a cancel
        # thread through that is worse than handing the desk the number.
        delays = []
        transport = RecordingTransport((418, {"retry_after": 600}))
        mgr = _manager(
            transport, max_retries=3, max_backoff_ms=5000, sleep_fn=delays.append
        )
        res = mgr.cancel_order_idempotent("ORD_68")

        self.assertEqual(delays, [])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(res.status, CancelStatus.UNKNOWN)
        self.assertEqual(res.retry_after_s, 600.0)

    def test_unparseable_retry_after_falls_back_to_the_schedule(self):
        delays = []
        transport = RecordingTransport((429, {"retry_after": "soon-ish"}), (200, {}))
        mgr = _manager(
            transport, max_retries=1, base_backoff_ms=100, jitter_ratio=0.0,
            sleep_fn=delays.append,
        )
        mgr.cancel_order_idempotent("ORD_69")

        self.assertEqual(delays, [0.1])


class TestConcurrency(unittest.TestCase):

    def test_concurrent_callers_of_one_cancel_id_dispatch_once(self):
        barrier = threading.Barrier(8)
        transport = RecordingTransport((200, {}))
        slow = _SlowTransport(transport, hold_s=0.05)
        mgr = _manager(slow)
        results = []
        lock = threading.Lock()

        def worker():
            barrier.wait()
            res = mgr.cancel_order_idempotent("ORD_70", "CID_70")
            with lock:
                results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(transport.calls, 1, "duplicate concurrent dispatch is a cancel storm")
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r.status is CancelStatus.PENDING_CANCEL for r in results))
        self.assertEqual(sum(1 for r in results if not r.is_idempotent_retry), 1)

    def test_concurrent_distinct_ids_all_dispatch(self):
        transport = RecordingTransport((200, {}))
        mgr = _manager(transport, max_cache_size=64)

        threads = [
            threading.Thread(
                target=mgr.cancel_order_idempotent, args=(f"ORD_{i}", f"CID_{i}")
            )
            for i in range(32)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(transport.calls, 32)

    def test_in_flight_slot_is_released_after_an_indeterminate_outcome(self):
        transport = RecordingTransport(ConnectionError("reset"), (200, {}), repeat_last=False)
        mgr = _manager(transport, max_retries=0)

        mgr.cancel_order_idempotent("ORD_71", "CID_71")
        second = mgr.cancel_order_idempotent("ORD_71", "CID_71")

        self.assertEqual(second.status, CancelStatus.PENDING_CANCEL)
        self.assertEqual(transport.calls, 2)


class _SlowTransport:
    """Holds the first dispatch open long enough for duplicates to pile up behind it."""

    def __init__(self, inner, hold_s):
        self._inner = inner
        self._hold_s = hold_s

    def __call__(self, order_id, client_cancel_id):
        time.sleep(self._hold_s)
        return self._inner(order_id, client_cancel_id)


class TestInputValidation(unittest.TestCase):

    def test_rejects_bad_constructor_arguments(self):
        transport = RecordingTransport((200, {}))
        with self.assertRaises(TypeError):
            IdempotentCancelManager(http_cancel_fn="not-callable")
        for kwargs in (
            {"max_cache_size": 0},
            {"max_retries": -1},
            {"base_backoff_ms": 0},
            {"max_backoff_ms": 10, "base_backoff_ms": 100},
            {"jitter_ratio": 1.0},
            {"jitter_ratio": -0.1},
            {"in_flight_wait_s": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    IdempotentCancelManager(http_cancel_fn=transport, **kwargs)

    def test_rejects_empty_identifiers(self):
        mgr = _manager(RecordingTransport((200, {})))
        for order_id in ("", "   ", None, 42):
            with self.subTest(order_id=order_id):
                with self.assertRaises(ValueError):
                    mgr.cancel_order_idempotent(order_id)
        with self.assertRaises(ValueError):
            mgr.cancel_order_idempotent("ORD_80", client_cancel_id="  ")

    def test_result_is_immutable(self):
        mgr = _manager(RecordingTransport((200, {})))
        res = mgr.cancel_order_idempotent("ORD_81")
        with self.assertRaises(Exception):
            res.status = CancelStatus.CANCELLED  # type: ignore[misc]


class TestStatusContract(unittest.TestCase):
    """Guards the property every caller branches on."""

    def test_only_broker_asserted_outcomes_are_terminal(self):
        terminal = {
            s for s in CancelStatus
            if CancelResult("c", "o", s, False, "").is_terminal
        }
        self.assertEqual(
            terminal,
            {
                CancelStatus.CANCELLED,
                CancelStatus.FILLED_BEFORE_CANCEL,
                CancelStatus.ALREADY_CANCELLED,
            },
        )

    def test_pending_cancel_requires_reconciliation(self):
        res = CancelResult("c", "o", CancelStatus.PENDING_CANCEL, False, "")
        self.assertTrue(res.requires_reconciliation)


if __name__ == "__main__":
    unittest.main()
