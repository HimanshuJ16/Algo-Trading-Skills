"""
Unit tests for webhook-based-order-fill-notifications.

The regression tests carry a REGRESSION marker naming the defect they pin. Each
one fails against the previous implementation of this skill:

* a payload with no ``timestamp`` field defaulted to ``time.time()``, so replay
  defence passed for every payload that omitted it;
* a duplicate delivery returned ``status="SUCCESS"`` with the fill quantity
  populated, so a caller accumulating on SUCCESS double-counted the fill;
* ``float()``/``int()`` coercion sat outside the guarded block, so a
  non-numeric quantity raised out of ``process_webhook``;
* ``json.loads`` accepted the non-standard ``NaN`` literal, so a NaN quantity
  reached the ledger;
* ``str(payload.get("order_id"))`` turned a JSON ``null`` into the truthy
  string ``"None"`` and accepted it as an order id;
* ``signature.replace("sha256=", "")`` stripped the label from anywhere in the
  token rather than only from the front.
"""
import base64
import hashlib
import hmac
import json
import threading
import time
import unittest

from webhook_consumer import (
    DEFAULT_MAX_DRIFT_SECONDS,
    MAX_SIGNATURE_TOKENS,
    ClaimRecord,
    WebhookConsumerManager,
    WebhookError,
    WebhookIngestionResult,
    WebhookStatus,
)

SECRET = "WEBHOOK_SECRET_999"


def signed(payload_dict, secret=SECRET):
    """Serialises a payload once and signs those exact bytes."""
    body = json.dumps(payload_dict).encode("utf-8")
    return body, WebhookConsumerManager.compute_hmac_signature(body, secret)


def fill(order_id="ORD_1", exec_id="EXEC_1", qty=10.0, ts=None, **extra):
    payload = {
        "order_id": order_id,
        "exec_id": exec_id,
        "filled_qty": qty,
        "timestamp": time.time() if ts is None else ts,
    }
    payload.update(extra)
    return payload


class SignatureVerificationTests(unittest.TestCase):
    def setUp(self):
        self.mgr = WebhookConsumerManager()

    def test_valid_signature_is_accepted(self):
        body, sig = signed(fill())
        res = self.mgr.process_webhook(body, f"sha256={sig}", SECRET)
        self.assertEqual(res.status, WebhookStatus.SUCCESS)
        self.assertEqual(res.reason, "VERIFIED_INGESTED")
        self.assertTrue(res.apply_to_ledger)
        self.assertEqual(res.http_status, 200)

    def test_bare_hex_signature_without_label_is_accepted(self):
        body, sig = signed(fill())
        self.assertTrue(self.mgr.verify_signature(body, sig, SECRET))

    def test_tampered_signature_is_rejected_with_401(self):
        body, _ = signed(fill())
        res = self.mgr.process_webhook(body, "sha256=BAD_SIGNATURE", SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertEqual(res.reason, "INVALID_SIGNATURE")
        self.assertEqual(res.http_status, 401)
        self.assertFalse(res.apply_to_ledger)

    def test_mutated_body_under_a_captured_signature_is_rejected(self):
        """The signature covers the body, so editing the quantity must fail."""
        body, sig = signed(fill(qty=10.0))
        tampered = body.replace(b"10.0", b"99.0")
        self.assertNotEqual(body, tampered)
        res = self.mgr.process_webhook(tampered, sig, SECRET)
        self.assertEqual(res.reason, "INVALID_SIGNATURE")

    def test_signature_computed_over_reserialised_json_does_not_verify(self):
        """Guards the 'sign the raw bytes' rule: key order changes the digest."""
        payload = fill()
        body = json.dumps(payload).encode("utf-8")
        reserialised = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        self.assertNotEqual(body, reserialised)
        sig_over_reserialised = WebhookConsumerManager.compute_hmac_signature(
            reserialised, SECRET
        )
        self.assertFalse(self.mgr.verify_signature(body, sig_over_reserialised, SECRET))

    def test_wrong_secret_is_rejected(self):
        body, sig = signed(fill(), secret="a-different-secret")
        self.assertFalse(self.mgr.verify_signature(body, sig, SECRET))

    def test_empty_or_missing_secret_never_verifies(self):
        body, sig = signed(fill())
        for secret in ("", None, [], ["", None]):
            self.assertFalse(self.mgr.verify_signature(body, sig, secret), secret)

    def test_empty_signature_header_never_verifies(self):
        body, _ = signed(fill())
        for header in ("", None, 123):
            self.assertFalse(self.mgr.verify_signature(body, header, SECRET), header)

    def test_label_is_stripped_only_from_the_front(self):
        """REGRESSION: replace() removed the label from anywhere in the token."""
        body, _ = signed(fill())
        # A token whose body embeds the literal label must not be silently
        # rewritten into a different digest.
        self.assertFalse(self.mgr.verify_signature(body, "aasha256=bb", SECRET))

    def test_base64_standard_webhooks_token_is_accepted(self):
        """Standard Webhooks sends ``v1,<base64>`` rather than a hex digest."""
        body, _ = signed(fill())
        digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
        token = "v1," + base64.b64encode(digest).decode()
        self.assertTrue(self.mgr.verify_signature(body, token, SECRET))

    def test_dual_secret_rotation_window_accepts_either_key(self):
        body = json.dumps(fill()).encode("utf-8")
        old_sig = WebhookConsumerManager.compute_hmac_signature(body, "old-secret")
        new_sig = WebhookConsumerManager.compute_hmac_signature(body, "new-secret")
        for sig in (old_sig, new_sig):
            self.assertTrue(
                self.mgr.verify_signature(body, sig, ["new-secret", "old-secret"])
            )

    def test_space_delimited_multi_signature_header_accepts_any_match(self):
        body = json.dumps(fill()).encode("utf-8")
        good = WebhookConsumerManager.compute_hmac_signature(body, SECRET)
        header = f"v1,{base64.b64encode(b'nonsense').decode()} sha256={good}"
        self.assertTrue(self.mgr.verify_signature(body, header, SECRET))

    def test_signature_work_is_bounded_for_unauthenticated_callers(self):
        """
        An internet-facing endpoint must not do unbounded HMAC or decode work on
        a header anyone can send.
        """
        body, _ = signed(fill())
        self.assertFalse(
            self.mgr.verify_signature(body, " ".join(["a" * 64] * 20_000), SECRET)
        )
        self.assertFalse(self.mgr.verify_signature(body, "f" * 2_000_000, SECRET))

    def test_a_valid_token_beyond_the_token_cap_is_not_searched(self):
        """The cap is a real limit, not decoration -- document where it bites."""
        body, _ = signed(fill())
        good = WebhookConsumerManager.compute_hmac_signature(body, SECRET)
        padding = ["deadbeef"] * MAX_SIGNATURE_TOKENS
        self.assertTrue(
            self.mgr.verify_signature(body, " ".join(padding[:-1] + [good]), SECRET)
        )
        self.assertFalse(self.mgr.verify_signature(body, " ".join(padding + [good]), SECRET))

    def test_compute_signature_refuses_a_non_bytes_body(self):
        with self.assertRaises(TypeError):
            WebhookConsumerManager.compute_hmac_signature({"order_id": "O"}, SECRET)

    def test_signature_is_checked_before_the_payload_is_parsed(self):
        """Unauthenticated bytes must never reach the JSON parser."""
        res = self.mgr.process_webhook(b"{not json at all", "sha256=deadbeef", SECRET)
        self.assertEqual(res.reason, "INVALID_SIGNATURE")


class TimestampReplayTests(unittest.TestCase):
    def setUp(self):
        self.mgr = WebhookConsumerManager()

    def test_missing_timestamp_is_rejected(self):
        """REGRESSION: an absent timestamp defaulted to now and always passed."""
        body, sig = signed({"order_id": "ORD_1", "exec_id": "EXEC_1", "filled_qty": 5.0})
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertEqual(res.reason, "MISSING_TIMESTAMP")
        self.assertFalse(res.apply_to_ledger)

    def test_null_timestamp_is_rejected(self):
        body, sig = signed(fill(ts=None) | {"timestamp": None})
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.reason, "MISSING_TIMESTAMP")

    def test_stale_timestamp_is_rejected(self):
        body, sig = signed(fill(ts=time.time() - 3600))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertEqual(res.reason, "TIMESTAMP_DRIFT_EXCEEDED")

    def test_far_future_timestamp_is_rejected(self):
        body, sig = signed(fill(ts=time.time() + 9999))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.reason, "TIMESTAMP_DRIFT_EXCEEDED")

    def test_drift_window_boundary_is_inclusive_on_both_sides(self):
        """Exact-threshold behaviour, pinned with a frozen clock."""
        now = 1_700_000_000.0
        mgr = WebhookConsumerManager(clock=lambda: now)
        self.assertEqual(mgr.max_drift_seconds, DEFAULT_MAX_DRIFT_SECONDS)
        self.assertTrue(mgr.verify_timestamp(now - 300.0))
        self.assertFalse(mgr.verify_timestamp(now - 300.001))
        self.assertTrue(mgr.verify_timestamp(now + 300.0))
        self.assertFalse(mgr.verify_timestamp(now + 300.001))

    def test_future_window_can_be_tightened_independently(self):
        now = 1_700_000_000.0
        mgr = WebhookConsumerManager(
            max_drift_seconds=300.0, max_future_drift_seconds=5.0, clock=lambda: now
        )
        self.assertTrue(mgr.verify_timestamp(now - 299.0))
        self.assertTrue(mgr.verify_timestamp(now + 5.0))
        self.assertFalse(mgr.verify_timestamp(now + 6.0))

    def test_millisecond_timestamps_are_normalised(self):
        now = 1_700_000_000.0
        mgr = WebhookConsumerManager(clock=lambda: now)
        self.assertTrue(mgr.verify_timestamp(int(now * 1000)))

    def test_iso8601_timestamps_are_accepted(self):
        now = 1_700_000_000.0
        mgr = WebhookConsumerManager(clock=lambda: now)
        self.assertTrue(mgr.verify_timestamp("2023-11-14T22:13:20Z"))
        self.assertTrue(mgr.verify_timestamp("2023-11-14T22:13:20+00:00"))
        # Naive stamps are read as UTC, not as the host's local zone.
        self.assertTrue(mgr.verify_timestamp("2023-11-14T22:13:20"))

    def test_unparseable_timestamps_are_not_treated_as_fresh(self):
        for bad in ("", "   ", "not-a-time", True, {}, [], object()):
            self.assertFalse(self.mgr.verify_timestamp(bad), bad)

    def test_parse_timestamp_raises_rather_than_guessing(self):
        for bad in (None, "", "nonsense", True):
            with self.assertRaises(WebhookError):
                self.mgr.parse_timestamp(bad)

    def test_replay_is_rejected_before_it_consumes_an_idempotency_claim(self):
        """A stale replay must not burn the key its fresh twin will need."""
        stale = fill(order_id="ORD_R", exec_id="EXEC_R", ts=time.time() - 3600)
        body, sig = signed(stale)
        self.assertEqual(self.mgr.process_webhook(body, sig, SECRET).reason,
                         "TIMESTAMP_DRIFT_EXCEEDED")
        self.assertFalse(self.mgr.is_duplicate("ORD_R", "EXEC_R"))


class DeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.mgr = WebhookConsumerManager()

    def test_duplicate_delivery_is_acknowledged_but_not_applied(self):
        body, sig = signed(fill(order_id="ORD_2", exec_id="EXEC_2", qty=20.0))
        first = self.mgr.process_webhook(body, sig, SECRET)
        second = self.mgr.process_webhook(body, sig, SECRET)

        self.assertEqual(first.reason, "VERIFIED_INGESTED")
        self.assertTrue(first.apply_to_ledger)
        self.assertEqual(second.status, WebhookStatus.DUPLICATE)
        self.assertEqual(second.reason, "DUPLICATE_SKIPPED")
        self.assertFalse(second.apply_to_ledger)
        # The broker must still get a 200, or it retries forever.
        self.assertEqual(second.http_status, 200)

    def test_redelivery_cannot_double_count_even_if_the_flag_is_ignored(self):
        """
        REGRESSION: a duplicate used to return SUCCESS with the quantity intact,
        so ``if res.status == "SUCCESS": position += res.filled_quantity``
        booked the fill twice.
        """
        body, sig = signed(fill(order_id="ORD_3", exec_id="EXEC_3", qty=100.0))
        results = [self.mgr.process_webhook(body, sig, SECRET) for _ in range(5)]

        by_flag = sum(r.filled_quantity for r in results if r.apply_to_ledger)
        by_status = sum(r.filled_quantity for r in results if r.status == "SUCCESS")
        self.assertEqual(by_flag, 100.0)
        self.assertEqual(by_status, 100.0)
        self.assertEqual(sum(1 for r in results if r.apply_to_ledger), 1)

    def test_quantity_is_zeroed_whenever_the_event_is_not_applicable(self):
        res = WebhookIngestionResult(
            status=WebhookStatus.DUPLICATE, reason="DUPLICATE_SKIPPED", filled_quantity=42.0
        )
        self.assertEqual(res.filled_quantity, 0.0)

    def test_same_exec_id_under_a_different_order_is_not_a_duplicate(self):
        for order_id in ("ORD_A", "ORD_B"):
            body, sig = signed(fill(order_id=order_id, exec_id="SHARED_EXEC"))
            res = self.mgr.process_webhook(body, sig, SECRET)
            self.assertTrue(res.apply_to_ledger, order_id)

    def test_redelivery_with_a_changed_body_is_flagged_for_reconciliation(self):
        """
        An amended execution reusing its key must not be silently dropped as an
        ordinary duplicate.
        """
        base = fill(order_id="ORD_4", exec_id="EXEC_4", qty=10.0)
        body_a, sig_a = signed(base)
        body_b, sig_b = signed({**base, "filled_qty": 12.0})

        self.assertTrue(self.mgr.process_webhook(body_a, sig_a, SECRET).apply_to_ledger)
        second = self.mgr.process_webhook(body_b, sig_b, SECRET)
        self.assertEqual(second.status, WebhookStatus.DUPLICATE)
        self.assertEqual(second.reason, "DUPLICATE_CONTENT_MISMATCH")
        self.assertTrue(second.requires_reconciliation)
        self.assertFalse(second.apply_to_ledger)

    def test_is_duplicate_is_a_pure_read(self):
        """REGRESSION: the predicate used to claim the key as a side effect."""
        self.assertFalse(self.mgr.is_duplicate("ORD_5", "EXEC_5"))
        self.assertFalse(self.mgr.is_duplicate("ORD_5", "EXEC_5"))
        body, sig = signed(fill(order_id="ORD_5", exec_id="EXEC_5"))
        self.assertTrue(self.mgr.process_webhook(body, sig, SECRET).apply_to_ledger)
        self.assertTrue(self.mgr.is_duplicate("ORD_5", "EXEC_5"))

    def test_claim_execution_grants_the_key_exactly_once(self):
        claimed, prior = self.mgr.claim_execution("ORD_6", "EXEC_6", "digest-a")
        self.assertTrue(claimed)
        self.assertIsNone(prior)

        claimed, prior = self.mgr.claim_execution("ORD_6", "EXEC_6", "digest-b")
        self.assertFalse(claimed)
        self.assertIsInstance(prior, ClaimRecord)
        self.assertEqual(prior.body_digest, "digest-a")

    def test_concurrent_deliveries_yield_exactly_one_applicable_result(self):
        body, sig = signed(fill(order_id="ORD_7", exec_id="EXEC_7", qty=50.0))
        results = []
        barrier = threading.Barrier(16)

        def deliver():
            barrier.wait()
            results.append(self.mgr.process_webhook(body, sig, SECRET))

        threads = [threading.Thread(target=deliver) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 16)
        self.assertEqual(sum(1 for r in results if r.apply_to_ledger), 1)
        self.assertEqual(sum(r.filled_quantity for r in results), 50.0)

    def test_claims_expire_after_the_retention_window(self):
        now = [1_000_000.0]
        mgr = WebhookConsumerManager(retention_seconds=60.0, clock=lambda: now[0])
        self.assertTrue(mgr.claim_execution("ORD_8", "EXEC_8")[0])
        self.assertTrue(mgr.is_duplicate("ORD_8", "EXEC_8"))
        now[0] += 61.0
        self.assertFalse(mgr.is_duplicate("ORD_8", "EXEC_8"))

    def test_claim_store_is_bounded(self):
        mgr = WebhookConsumerManager(max_tracked_executions=10)
        for i in range(50):
            mgr.claim_execution("ORD", f"EXEC_{i}")
        self.assertLessEqual(len(mgr._claims), 10)
        # The newest claims survive; the oldest are the ones dropped.
        self.assertTrue(mgr.is_duplicate("ORD", "EXEC_49"))
        self.assertFalse(mgr.is_duplicate("ORD", "EXEC_0"))

    def test_expiry_does_not_wait_for_the_periodic_sweep(self):
        """
        The full-store sweep is amortised, so expiry has to be decided per key
        at lookup time or a stale claim outlives its retention window.
        """
        now = [1_000_000.0]
        mgr = WebhookConsumerManager(retention_seconds=3600.0, clock=lambda: now[0])
        mgr.claim_execution("ORD_9", "EXEC_9")
        now[0] += 3601.0
        # Well short of the sweep interval (retention/10 = 360 s past the last
        # sweep would be needed), so this can only pass via the per-key check.
        self.assertFalse(mgr.is_duplicate("ORD_9", "EXEC_9"))
        self.assertTrue(mgr.claim_execution("ORD_9", "EXEC_9")[0])

    def test_ingestion_does_not_degrade_with_store_size(self):
        """
        Sweeping the whole store on every claim made ingestion quadratic: a
        200k-claim fill did not complete in two minutes. Pinned as a rough
        upper bound rather than a precise timing assertion.
        """
        mgr = WebhookConsumerManager()
        for i in range(20_000):
            mgr.claim_execution("ORD_PERF", f"EXEC_{i}")

        started = time.perf_counter()
        for i in range(1_000):
            mgr.claim_execution("ORD_PERF", f"LATE_{i}")
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 2.0, f"1k claims against a 20k store took {elapsed:.2f}s")


class PayloadValidationTests(unittest.TestCase):
    def setUp(self):
        self.mgr = WebhookConsumerManager()

    def test_null_order_id_is_not_accepted_as_the_string_none(self):
        """REGRESSION: ``str(None)`` produced the truthy order id ``"None"``."""
        body, sig = signed(fill(order_id=None))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertIn("MISSING_ORDER_OR_EXEC_ID", res.reason)
        self.assertNotEqual(res.order_id, "None")

    def test_blank_and_structural_identifiers_are_rejected(self):
        for bad in ("", "   ", [], {}, True, None):
            body, sig = signed(fill(exec_id=bad))
            res = self.mgr.process_webhook(body, sig, SECRET)
            self.assertIn("MISSING_ORDER_OR_EXEC_ID", res.reason, repr(bad))

    def test_absent_identifier_fields_are_rejected(self):
        body, sig = signed({"filled_qty": 1.0, "timestamp": time.time()})
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertIn("MISSING_ORDER_OR_EXEC_ID", res.reason)

    def test_broker_field_aliases_are_accepted(self):
        body, sig = signed(
            {
                "orderId": "ORD_ALIAS",
                "executionId": "EXEC_ALIAS",
                "quantity": 7.0,
                "time": time.time(),
            }
        )
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertTrue(res.apply_to_ledger)
        self.assertEqual(res.order_id, "ORD_ALIAS")
        self.assertEqual(res.exec_id, "EXEC_ALIAS")
        self.assertEqual(res.filled_quantity, 7.0)

    def test_non_numeric_quantity_is_rejected_not_raised(self):
        """REGRESSION: this raised ValueError out of ``process_webhook``."""
        body, sig = signed(fill(qty="abc"))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertIn("MALFORMED_QUANTITY", res.reason)
        self.assertEqual(res.http_status, 400)

    def test_null_quantity_is_rejected_not_raised(self):
        """REGRESSION: this raised TypeError out of ``process_webhook``."""
        body, sig = signed(fill(qty=None))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertIn("MALFORMED_QUANTITY", res.reason)

    def test_negative_quantity_is_rejected(self):
        body, sig = signed(fill(qty=-5.0))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertIn("MALFORMED_QUANTITY", res.reason)

    def test_nan_and_infinity_literals_are_rejected(self):
        """REGRESSION: json.loads accepts these by default and NaN reached the ledger."""
        for literal in ("NaN", "Infinity", "-Infinity"):
            raw = (
                '{"order_id": "O", "exec_id": "E", "timestamp": %s, "filled_qty": %s}'
                % (time.time(), literal)
            ).encode("utf-8")
            sig = WebhookConsumerManager.compute_hmac_signature(raw, SECRET)
            res = self.mgr.process_webhook(raw, sig, SECRET)
            self.assertEqual(res.status, WebhookStatus.REJECTED, literal)
            self.assertIn("INVALID_JSON", res.reason, literal)

    def test_numeric_string_quantity_is_accepted(self):
        body, sig = signed(fill(qty="12.5"))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertTrue(res.apply_to_ledger)
        self.assertEqual(res.filled_quantity, 12.5)

    def test_zero_quantity_is_a_valid_fill_event(self):
        body, sig = signed(fill(qty=0.0))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertTrue(res.apply_to_ledger)
        self.assertEqual(res.filled_quantity, 0.0)

    def test_malformed_json_is_rejected_with_400(self):
        raw = b"{not json"
        sig = WebhookConsumerManager.compute_hmac_signature(raw, SECRET)
        res = self.mgr.process_webhook(raw, sig, SECRET)
        self.assertEqual(res.status, WebhookStatus.REJECTED)
        self.assertIn("INVALID_JSON", res.reason)
        self.assertEqual(res.http_status, 400)

    def test_non_object_payload_is_rejected(self):
        raw = b"[1, 2, 3]"
        sig = WebhookConsumerManager.compute_hmac_signature(raw, SECRET)
        res = self.mgr.process_webhook(raw, sig, SECRET)
        self.assertEqual(res.reason, "PAYLOAD_NOT_OBJECT")

    def test_invalid_utf8_body_is_rejected(self):
        raw = b'{"order_id": "\xff\xfe"}'
        sig = WebhookConsumerManager.compute_hmac_signature(raw, SECRET)
        res = self.mgr.process_webhook(raw, sig, SECRET)
        self.assertIn("INVALID_ENCODING", res.reason)

    def test_malformed_sequence_number_is_rejected(self):
        body, sig = signed(fill(sequence_num="not-a-number"))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertIn("MALFORMED_SEQUENCE", res.reason)

    def test_constructor_rejects_nonsensical_windows(self):
        for kwargs in (
            {"max_drift_seconds": 0},
            {"max_drift_seconds": -1},
            {"max_future_drift_seconds": -1},
            {"retention_seconds": 0},
            {"max_tracked_executions": 0},
        ):
            with self.assertRaises(ValueError, msg=kwargs):
                WebhookConsumerManager(**kwargs)


class SequenceOrderingTests(unittest.TestCase):
    def setUp(self):
        self.mgr = WebhookConsumerManager()

    def _deliver(self, seq, exec_id, order_id="ORD_SEQ"):
        body, sig = signed(
            fill(order_id=order_id, exec_id=exec_id, sequence_num=seq)
        )
        return self.mgr.process_webhook(body, sig, SECRET)

    def test_in_order_deliveries_are_not_flagged(self):
        for seq in (1, 2, 3):
            res = self._deliver(seq, f"EXEC_{seq}")
            self.assertFalse(res.out_of_order, seq)
            self.assertFalse(res.requires_reconciliation, seq)
            self.assertEqual(res.sequence_num, seq)

    def test_late_fill_is_reported_as_out_of_order(self):
        """
        REGRESSION: the old build logged a warning and returned a result
        indistinguishable from an in-order fill, so a caller applying
        PARTIALLY_FILLED after FILLED had no way to notice.
        """
        self._deliver(3, "EXEC_3")
        late = self._deliver(1, "EXEC_1")
        self.assertTrue(late.out_of_order)
        self.assertTrue(late.requires_reconciliation)
        # It is still ingested; this module reports, it does not buffer.
        self.assertTrue(late.apply_to_ledger)

    def test_sequence_zero_is_a_real_sequence_number(self):
        """Guards the old ``if seq_num > 0`` guard that ignored sequence 0."""
        self._deliver(2, "EXEC_2")
        res = self._deliver(0, "EXEC_0")
        self.assertTrue(res.out_of_order)

    def test_missing_sequences_expose_an_undelivered_gap(self):
        self._deliver(1, "EXEC_1")
        self._deliver(4, "EXEC_4")
        self.assertEqual(self.mgr.missing_sequences("ORD_SEQ"), (2, 3))

    def test_gap_closes_once_the_late_fills_arrive(self):
        self._deliver(1, "EXEC_1")
        self._deliver(3, "EXEC_3")
        self.assertEqual(self.mgr.missing_sequences("ORD_SEQ"), (2,))
        self._deliver(2, "EXEC_2")
        self.assertEqual(self.mgr.missing_sequences("ORD_SEQ"), ())

    def test_sequences_are_tracked_per_order(self):
        self._deliver(9, "EXEC_9", order_id="ORD_X")
        res = self._deliver(1, "EXEC_1", order_id="ORD_Y")
        self.assertFalse(res.out_of_order)

    def test_payloads_without_a_sequence_number_are_accepted(self):
        body, sig = signed(fill(order_id="ORD_NOSEQ", exec_id="EXEC_NOSEQ"))
        res = self.mgr.process_webhook(body, sig, SECRET)
        self.assertTrue(res.apply_to_ledger)
        self.assertIsNone(res.sequence_num)
        self.assertFalse(res.out_of_order)
        self.assertEqual(self.mgr.missing_sequences("ORD_NOSEQ"), ())

    def test_unknown_order_has_no_gaps(self):
        self.assertEqual(self.mgr.missing_sequences("NEVER_SEEN"), ())


class LedgerIntegrationTests(unittest.TestCase):
    """End-to-end shape of the intended caller, exercised against the guard."""

    def test_position_is_correct_across_retries_replays_and_reordering(self):
        mgr = WebhookConsumerManager()
        position = 0.0

        # A retry is the *same bytes* redelivered, so the retried entries reuse
        # the original payload object. Rebuilding one with fill() would restamp
        # its timestamp and make the redelivery a content mismatch instead.
        e1 = fill(order_id="ORD_L", exec_id="E1", qty=30.0, sequence_num=1)
        e3 = fill(order_id="ORD_L", exec_id="E3", qty=40.0, sequence_num=3)
        deliveries = [
            e1,
            e1,                                                               # retry
            e3,
            fill(order_id="ORD_L", exec_id="E2", qty=30.0, sequence_num=2),   # late
            e3,                                                               # retry
        ]
        stale = fill(order_id="ORD_L", exec_id="E4", qty=999.0, ts=time.time() - 7200)

        needs_reconcile = 0
        for payload in deliveries + [stale]:
            body, sig = signed(payload)
            res = mgr.process_webhook(body, sig, SECRET)
            if res.requires_reconciliation:
                needs_reconcile += 1
            if res.apply_to_ledger:
                position += res.filled_quantity

        self.assertEqual(position, 100.0)
        self.assertEqual(needs_reconcile, 1)  # the late sequence-2 fill
        self.assertEqual(mgr.missing_sequences("ORD_L"), ())


if __name__ == "__main__":
    unittest.main()
