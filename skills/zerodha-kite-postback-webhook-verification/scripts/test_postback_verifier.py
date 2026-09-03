"""
Unit tests for zerodha-kite-postback-webhook-verification.

The suite is built around the exact postback payload published in the Kite
Connect v3 documentation (https://kite.trade/docs/connect/v3/postbacks/) so the
real wire format -- a raw JSON body whose `order_timestamp` is a naive IST
"YYYY-MM-DD HH:MM:SS" string -- is exercised rather than a convenient stand-in.

Regression coverage for the defects this suite exists to prevent:

* an omitted `checksum` field silently bypassing verification (fail-open auth);
* the documented `order_timestamp` format being unparseable, which rejected
  every genuine postback;
* naive IST timestamps read as server-local time, which rejects everything on a
  UTC host;
* deduplicating on `order_id` + `status`, which discards every partial fill
  after the first because they all carry status OPEN;
* a duplicate delivery reporting `valid is True`, inviting double application;
* an attacker-controlled non-ASCII checksum raising TypeError out of the
  handler.
"""
import json
import threading
import unittest
from datetime import datetime, timedelta, timezone

from postback_verifier import (
    IST,
    KITE_TIMESTAMP_FORMAT,
    KitePostbackVerifier,
    PostbackOutcome,
    PostbackVerificationError,
)

# 2022-03-03 09:24:25 IST, the instant in the documented example payload.
FIXED_NOW = datetime(2022, 3, 3, 3, 54, 25, tzinfo=timezone.utc)

API_SECRET = "kitetestsecret"
DOC_ORDER_ID = "220303000308932"
DOC_ORDER_TIMESTAMP = "2022-03-03 09:24:25"

# Independently derived with GNU coreutils sha256sum, not with this module:
#   printf '%s' '2203030003089322022-03-03 09:24:25kitetestsecret' | sha256sum
DOC_CHECKSUM = "b70c7de9e093dfd33faf47af1258ae4f5e1150dd637f5e7a990bf8b2cd464ba4"

# Verbatim shape of the example payload in the Kite Connect v3 postback docs.
DOC_PAYLOAD = {
    "user_id": "AB1234",
    "unfilled_quantity": 0,
    "app_id": 1234,
    "checksum": DOC_CHECKSUM,
    "placed_by": "AB1234",
    "order_id": DOC_ORDER_ID,
    "exchange_order_id": "1000000001482421",
    "parent_order_id": None,
    "status": "COMPLETE",
    "status_message": None,
    "order_timestamp": DOC_ORDER_TIMESTAMP,
    "exchange_update_timestamp": DOC_ORDER_TIMESTAMP,
    "exchange_timestamp": DOC_ORDER_TIMESTAMP,
    "variety": "regular",
    "exchange": "NSE",
    "tradingsymbol": "SBIN",
    "instrument_token": 779521,
    "order_type": "MARKET",
    "transaction_type": "BUY",
    "validity": "DAY",
    "product": "CNC",
    "quantity": 1,
    "disclosed_quantity": 0,
    "price": 0,
    "trigger_price": 0,
    "average_price": 470,
    "filled_quantity": 1,
    "pending_quantity": 0,
    "cancelled_quantity": 0,
    "market_protection": 0,
    "meta": {},
    "tag": None,
    "guid": "XXXXXX",
}


def ist_timestamp(seconds_in_past: float = 0.0) -> str:
    """Kite-format IST timestamp, `seconds_in_past` seconds before FIXED_NOW."""
    moment = (FIXED_NOW - timedelta(seconds=seconds_in_past)).astimezone(IST)
    return moment.strftime(KITE_TIMESTAMP_FORMAT)


def signed_payload(order_id, order_timestamp, secret=API_SECRET, **overrides):
    """Build a correctly signed postback payload."""
    payload = {
        "order_id": order_id,
        "order_timestamp": order_timestamp,
        "status": "COMPLETE",
        "filled_quantity": 1,
        "average_price": 470,
        "checksum": KitePostbackVerifier.compute_checksum(
            order_id, order_timestamp, secret
        ),
    }
    payload.update(overrides)
    return payload


class VerifierTestBase(unittest.TestCase):
    def setUp(self):
        self.verifier = KitePostbackVerifier(
            max_drift_seconds=300.0,
            max_future_seconds=60.0,
            clock=lambda: FIXED_NOW,
        )


class TestChecksumComputation(unittest.TestCase):
    """The digest itself, against an externally computed vector."""

    def test_matches_independently_derived_sha256(self):
        self.assertEqual(
            KitePostbackVerifier.compute_checksum(
                DOC_ORDER_ID, DOC_ORDER_TIMESTAMP, API_SECRET
            ),
            DOC_CHECKSUM,
        )

    def test_field_order_is_order_id_then_timestamp_then_secret(self):
        """A transposed concatenation must not produce the documented digest."""
        transposed = KitePostbackVerifier.compute_checksum(
            DOC_ORDER_TIMESTAMP, DOC_ORDER_ID, API_SECRET
        )
        self.assertNotEqual(transposed, DOC_CHECKSUM)

    def test_empty_secret_refused(self):
        with self.assertRaises(PostbackVerificationError):
            KitePostbackVerifier.compute_checksum(DOC_ORDER_ID, DOC_ORDER_TIMESTAMP, "")


class TestDocumentedPayload(VerifierTestBase):
    """End-to-end against the payload Kite publishes."""

    def test_documented_payload_accepted(self):
        result = self.verifier.verify_postback(DOC_PAYLOAD, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.ACCEPTED)
        self.assertTrue(result.valid)
        self.assertTrue(result.authenticated)
        self.assertEqual(result.order_id, DOC_ORDER_ID)
        self.assertEqual(result.status, "COMPLETE")
        self.assertEqual(result.filled_quantity, 1)

    def test_documented_payload_accepted_as_raw_body(self):
        """Kite posts a raw JSON body; the receiver path must handle bytes."""
        body = json.dumps(DOC_PAYLOAD).encode("utf-8")
        result = self.verifier.verify_raw_body(body, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.ACCEPTED)

    def test_kite_timestamp_format_is_parseable(self):
        """Regression: the documented format used to fall through to float()."""
        parsed = self.verifier.parse_order_timestamp(DOC_ORDER_TIMESTAMP)
        self.assertIsNotNone(parsed.tzinfo)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(parsed.astimezone(timezone.utc), FIXED_NOW)


class TestFailClosedOnMissingChecksum(VerifierTestBase):
    """Regression: an unsigned postback used to be accepted outright."""

    def test_absent_checksum_field_rejected(self):
        payload = dict(DOC_PAYLOAD)
        del payload["checksum"]
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_MISSING_CHECKSUM)
        self.assertFalse(result.valid)
        self.assertFalse(result.authenticated)

    def test_empty_checksum_field_rejected(self):
        payload = dict(DOC_PAYLOAD, checksum="")
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_MISSING_CHECKSUM)

    def test_null_checksum_field_rejected(self):
        payload = dict(DOC_PAYLOAD, checksum=None)
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_MISSING_CHECKSUM)

    def test_forged_unsigned_fill_does_not_reach_the_ledger(self):
        """The attack the fail-open bug enabled: a fabricated COMPLETE fill."""
        forged = {
            "order_id": "999999999999999",
            "order_timestamp": ist_timestamp(),
            "status": "COMPLETE",
            "filled_quantity": 10_000,
        }
        result = self.verifier.verify_postback(forged, API_SECRET)
        self.assertFalse(result.valid)
        self.assertEqual(self.verifier.tracked_event_count, 0)


class TestChecksumRejection(VerifierTestBase):
    def test_tampered_checksum_rejected(self):
        payload = dict(DOC_PAYLOAD, checksum="0" * 64)
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_BAD_CHECKSUM)
        self.assertFalse(result.authenticated)

    def test_wrong_secret_rejected(self):
        result = self.verifier.verify_postback(DOC_PAYLOAD, "a-different-secret")
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_BAD_CHECKSUM)

    def test_non_ascii_checksum_does_not_raise(self):
        """Regression: str-mode compare_digest raises TypeError on non-ASCII."""
        payload = dict(DOC_PAYLOAD, checksum="é" * 64)
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_BAD_CHECKSUM)

    def test_non_hex_and_wrong_length_checksums_rejected(self):
        for bad in ("BAD_CHECKSUM_SPOOFED", "zz" * 32, DOC_CHECKSUM[:-1], DOC_CHECKSUM + "0"):
            with self.subTest(checksum=bad):
                payload = dict(DOC_PAYLOAD, checksum=bad)
                result = self.verifier.verify_postback(payload, API_SECRET)
                self.assertIs(result.outcome, PostbackOutcome.REJECTED_BAD_CHECKSUM)

    def test_uppercase_checksum_accepted(self):
        payload = dict(DOC_PAYLOAD, checksum=DOC_CHECKSUM.upper())
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.ACCEPTED)

    def test_empty_api_secret_is_a_deployment_error(self):
        with self.assertRaises(PostbackVerificationError):
            self.verifier.verify_postback(DOC_PAYLOAD, "")


class TestFreshnessWindow(VerifierTestBase):
    def test_stale_postback_rejected_but_still_authenticated(self):
        timestamp = ist_timestamp(seconds_in_past=3600)
        payload = signed_payload("220303000308933", timestamp)
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_STALE)
        self.assertFalse(result.valid)
        # Authentic-but-stale is an operational event, not a security incident.
        self.assertTrue(result.authenticated)

    def test_boundary_exactly_at_limit_accepted(self):
        payload = signed_payload("220303000308934", ist_timestamp(300))
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.ACCEPTED,
        )

    def test_boundary_one_second_past_limit_rejected(self):
        payload = signed_payload("220303000308935", ist_timestamp(301))
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.REJECTED_STALE,
        )

    def test_small_future_dating_tolerated_as_clock_skew(self):
        payload = signed_payload("220303000308936", ist_timestamp(-30))
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.ACCEPTED,
        )

    def test_large_future_dating_rejected_separately_from_stale(self):
        payload = signed_payload("220303000308937", ist_timestamp(-600))
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.REJECTED_FUTURE_DATED)

    def test_naive_timestamp_is_read_as_ist_not_server_local(self):
        """Regression: reading IST wall-clock as UTC adds 5.5h of false drift."""
        utc_verifier = KitePostbackVerifier(
            timestamp_tz=timezone.utc, clock=lambda: FIXED_NOW
        )
        self.assertTrue(self.verifier.verify_timestamp(DOC_ORDER_TIMESTAMP))
        self.assertFalse(utc_verifier.verify_timestamp(DOC_ORDER_TIMESTAMP))

    def test_iso8601_with_explicit_offset_accepted(self):
        self.assertTrue(self.verifier.verify_timestamp("2022-03-03T03:54:25+00:00"))

    def test_epoch_seconds_rejected_as_unparseable(self):
        """Kite never sends epoch; accepting it hides wire-format bugs."""
        with self.assertRaises(PostbackVerificationError):
            self.verifier.parse_order_timestamp("1646279665")
        self.assertFalse(self.verifier.verify_timestamp("1646279665"))


class TestIdempotency(VerifierTestBase):
    def test_exact_redelivery_is_duplicate_and_not_valid(self):
        first = self.verifier.verify_postback(DOC_PAYLOAD, API_SECRET)
        second = self.verifier.verify_postback(DOC_PAYLOAD, API_SECRET)
        self.assertIs(first.outcome, PostbackOutcome.ACCEPTED)
        self.assertIs(second.outcome, PostbackOutcome.DUPLICATE)
        # Regression: a duplicate reporting valid=True invited double-application.
        self.assertFalse(second.valid)
        self.assertTrue(second.authenticated)

    def test_successive_partial_fills_are_all_accepted(self):
        """Regression: order_id+status dedup swallowed every fill after the first.

        Kite emits an UPDATE postback per partial fill and they all carry
        status OPEN, so status-keyed dedup loses real quantity.
        """
        order_id = "220303000308940"
        outcomes = []
        for index, (filled, seconds_ago) in enumerate(
            [(25, 30), (60, 20), (100, 10)]
        ):
            timestamp = ist_timestamp(seconds_ago)
            payload = signed_payload(
                order_id,
                timestamp,
                status="OPEN",
                filled_quantity=filled,
                average_price=470 + index,
            )
            outcomes.append(self.verifier.verify_postback(payload, API_SECRET))

        self.assertEqual(
            [o.outcome for o in outcomes], [PostbackOutcome.ACCEPTED] * 3
        )
        self.assertEqual([o.filled_quantity for o in outcomes], [25, 60, 100])

    def test_tracked_events_are_bounded(self):
        verifier = KitePostbackVerifier(max_tracked_events=2, clock=lambda: FIXED_NOW)
        for fingerprint in ("a", "b", "c"):
            self.assertTrue(verifier.claim_event(fingerprint))
        self.assertEqual(verifier.tracked_event_count, 2)
        # "a" was evicted, so it is no longer recognised as seen.
        self.assertTrue(verifier.claim_event("a"))
        self.assertFalse(verifier.claim_event("c"))

    def test_concurrent_redeliveries_accept_exactly_once(self):
        results = []
        barrier = threading.Barrier(16)

        def worker():
            barrier.wait()
            results.append(self.verifier.verify_postback(DOC_PAYLOAD, API_SECRET))

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        accepted = [r for r in results if r.outcome is PostbackOutcome.ACCEPTED]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(results), 16)


class TestUnauthenticatedBodyFields(VerifierTestBase):
    """The checksum covers order_id + order_timestamp only -- pin that fact."""

    def test_mutated_status_and_quantity_still_verify(self):
        """A captured postback with an altered body passes signature checks.

        This is a property of Kite's scheme, not a defect in this module. It is
        why an accepted result demands reconciliation against
        GET /orders/:order_id before any position mutation.
        """
        mutated = dict(DOC_PAYLOAD, status="COMPLETE", filled_quantity=9_999)
        result = self.verifier.verify_postback(mutated, API_SECRET)
        self.assertIs(result.outcome, PostbackOutcome.ACCEPTED)
        self.assertTrue(result.requires_reconciliation)

    def test_rejected_results_never_request_reconciliation(self):
        payload = dict(DOC_PAYLOAD, checksum="0" * 64)
        result = self.verifier.verify_postback(payload, API_SECRET)
        self.assertFalse(result.requires_reconciliation)


class TestMalformedInput(VerifierTestBase):
    def test_missing_order_id_rejected(self):
        payload = dict(DOC_PAYLOAD)
        del payload["order_id"]
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.REJECTED_MALFORMED,
        )

    def test_missing_order_timestamp_rejected(self):
        payload = dict(DOC_PAYLOAD)
        del payload["order_timestamp"]
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.REJECTED_MALFORMED,
        )

    def test_unparseable_timestamp_rejected_without_raising(self):
        timestamp = "03/03/2022 09:24:25"
        payload = signed_payload("220303000308950", timestamp)
        self.assertIs(
            self.verifier.verify_postback(payload, API_SECRET).outcome,
            PostbackOutcome.REJECTED_MALFORMED,
        )

    def test_invalid_filled_quantity_rejected_without_raising(self):
        for bad in ("abc", -5, 1.5, True, [1]):
            with self.subTest(filled_quantity=bad):
                payload = signed_payload(
                    "220303000308951", ist_timestamp(), filled_quantity=bad
                )
                self.assertIs(
                    self.verifier.verify_postback(payload, API_SECRET).outcome,
                    PostbackOutcome.REJECTED_MALFORMED,
                )

    def test_non_json_body_rejected_without_raising(self):
        for body in (b"not json at all", b"\xff\xfe\x00", b"[1, 2, 3]", b""):
            with self.subTest(body=body):
                self.assertIs(
                    self.verifier.verify_raw_body(body, API_SECRET).outcome,
                    PostbackOutcome.REJECTED_MALFORMED,
                )

    def test_constructor_rejects_nonsense_configuration(self):
        for kwargs in (
            {"max_drift_seconds": 0},
            {"max_future_seconds": -1},
            {"max_tracked_events": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(PostbackVerificationError):
                    KitePostbackVerifier(**kwargs)


if __name__ == "__main__":
    unittest.main()
