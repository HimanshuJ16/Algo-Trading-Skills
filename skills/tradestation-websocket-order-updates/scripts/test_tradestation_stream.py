"""Unit tests for tradestation-websocket-order-updates.

Payload shapes are taken from TradeStation's published v3 OpenAPI examples for
``GET /v3/brokerage/stream/accounts/{accounts}/orders`` (Order, Heartbeat,
StreamStatus and ErrorResponse schemas), so the expected values here are derived
from the vendor's specification rather than restated from the implementation.
"""
import json
import logging
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from tradestation_stream import (
    DEFAULT_STALL_THRESHOLD_SECONDS,
    FILL_BEARING_STATUSES,
    FRAME_EMPTY,
    FRAME_ERROR,
    FRAME_HEARTBEAT,
    FRAME_MALFORMED,
    FRAME_ORDER,
    FRAME_STREAM_STATUS,
    FRAME_UNKNOWN,
    HEARTBEAT_IDLE_SECONDS,
    MAX_HISTORICAL_LOOKBACK_DAYS,
    ORDER_STATUS_DESCRIPTIONS,
    STREAM_STATUS_END_SNAPSHOT,
    STREAM_STATUS_GO_AWAY,
    TERMINAL_ORDER_STATUSES,
    TradeStationOrderUpdate,
    TradeStationStreamError,
    TradeStationStreamManager,
    build_order_update,
)

logging.getLogger("tradestation_stream").setLevel(logging.CRITICAL)


def order_payload(
    order_id="286234131",
    status="FPR",
    legs=(("MSFT", "10", "4", "6"),),
    filled_price="112.28",
    opened="2021-02-24T15:47:45Z",
    closed=None,
):
    """Build a v3 Order object. Legs are (symbol, ordered, executed, remaining)."""
    payload = {
        "AccountID": "123456782",
        "OrderID": order_id,
        "Status": status,
        "StatusDescription": ORDER_STATUS_DESCRIPTIONS.get(status, ""),
        "OrderType": "Limit",
        "FilledPrice": filled_price,
        "OpenedDateTime": opened,
        "Legs": [
            {
                "AssetType": "STOCK",
                "BuyOrSell": "Buy",
                "Symbol": symbol,
                "QuantityOrdered": ordered,
                "ExecQuantity": executed,
                "QuantityRemaining": remaining,
                "ExecutionPrice": filled_price,
            }
            for symbol, ordered, executed, remaining in legs
        ],
    }
    if closed is not None:
        payload["ClosedDateTime"] = closed
    return payload


def frame(**kwargs):
    return json.dumps(order_payload(**kwargs))


class FakeClock:
    """Controllable monotonic clock."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestFrameClassification(unittest.TestCase):
    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")

    def test_order_frame_extracts_v3_fields(self):
        f = self.mgr.classify_frame(frame(status="FPR", legs=(("MSFT", "10", "4", "6"),)))
        self.assertEqual(f.kind, FRAME_ORDER)
        update = f.order
        self.assertEqual(update.order_id, "286234131")
        self.assertEqual(update.status, "FPR")
        self.assertEqual(update.status_description, "Partial Fill (Alive)")
        self.assertEqual(update.account_id, "123456782")
        # Filled quantity lives on Legs[].ExecQuantity, not a top-level field.
        self.assertEqual(update.filled_quantity, Decimal("4"))
        # Average fill price is FilledPrice, not AveragePrice.
        self.assertEqual(update.average_price, Decimal("112.28"))
        self.assertEqual(len(update.legs), 1)
        self.assertEqual(update.legs[0].quantity_remaining, Decimal("6"))
        self.assertFalse(update.is_terminal)

    def test_legacy_field_names_yield_no_quantity(self):
        """A v2-style payload has none of the fields v3 actually sends.

        Regression guard for the original implementation, which read
        ``FilledQuantity``/``AveragePrice`` and therefore silently recorded zero
        for every real v3 fill.
        """
        legacy = json.dumps(
            {"OrderID": "TS999", "Status": "FLL", "FilledQuantity": 100, "AveragePrice": 150.25}
        )
        update = self.mgr.classify_frame(legacy).order
        self.assertEqual(update.filled_quantity, Decimal("0"))
        self.assertEqual(update.average_price, Decimal("0"))
        # ...whereas the real v3 shape carries the quantity through.
        real = self.mgr.classify_frame(
            frame(order_id="TS999", status="FLL", legs=(("MSFT", "100", "100", "0"),),
                  filled_price="150.25")
        ).order
        self.assertEqual(real.filled_quantity, Decimal("100"))
        self.assertEqual(real.average_price, Decimal("150.25"))

    def test_heartbeat_frame(self):
        f = self.mgr.classify_frame('{"Heartbeat": 1, "Timestamp": "2026-07-24T12:00:00Z"}')
        self.assertEqual(f.kind, FRAME_HEARTBEAT)
        self.assertIsNone(f.order)
        self.assertEqual(f.heartbeat_at, datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc))
        self.assertFalse(f.requires_reconnect)

    def test_end_snapshot_frame(self):
        f = self.mgr.classify_frame('{"StreamStatus": "EndSnapshot"}')
        self.assertEqual(f.kind, FRAME_STREAM_STATUS)
        self.assertEqual(f.stream_status, STREAM_STATUS_END_SNAPSHOT)
        self.assertFalse(f.requires_reconnect)

    def test_go_away_frame_demands_reconnect(self):
        f = self.mgr.classify_frame('{"StreamStatus": "GoAway"}')
        self.assertEqual(f.kind, FRAME_STREAM_STATUS)
        self.assertEqual(f.stream_status, STREAM_STATUS_GO_AWAY)
        self.assertTrue(f.requires_reconnect)

    def test_error_frame_demands_reconnect(self):
        f = self.mgr.classify_frame(
            '{"Error":"ServiceUnavailable","Message":"Stream quota exceeded","AccountID":"123"}'
        )
        self.assertEqual(f.kind, FRAME_ERROR)
        self.assertEqual(f.error, "ServiceUnavailable")
        self.assertEqual(f.message, "Stream quota exceeded")
        self.assertTrue(f.requires_reconnect)

    def test_empty_and_malformed_frames_do_not_raise(self):
        self.assertEqual(self.mgr.classify_frame("   ").kind, FRAME_EMPTY)
        # A JSON object split across HTTP chunks: framing bug, not an order.
        self.assertEqual(self.mgr.classify_frame('{"OrderID": "TS1", "Sta').kind, FRAME_MALFORMED)
        self.assertEqual(self.mgr.classify_frame("[1, 2, 3]").kind, FRAME_MALFORMED)
        self.assertEqual(self.mgr.classify_frame(None).kind, FRAME_MALFORMED)

    def test_object_without_order_id_is_unknown(self):
        self.assertEqual(self.mgr.classify_frame('{"Status": "FLL"}').kind, FRAME_UNKNOWN)

    def test_unparseable_numeric_does_not_kill_the_stream(self):
        payload = order_payload(legs=(("MSFT", "10", "n/a", "6"),), filled_price="")
        update = self.mgr.classify_frame(json.dumps(payload)).order
        self.assertEqual(update.filled_quantity, Decimal("0"))
        self.assertEqual(update.average_price, Decimal("0"))

    def test_unknown_status_is_kept_not_dropped(self):
        update = self.mgr.classify_frame(frame(status="ZZZ")).order
        self.assertIsNotNone(update)
        self.assertEqual(update.status, "ZZZ")
        self.assertFalse(update.is_terminal)


class TestStatusTables(unittest.TestCase):
    def test_documented_enum_is_complete(self):
        # The 20 codes in the v3 Status enum.
        self.assertEqual(len(ORDER_STATUS_DESCRIPTIONS), 20)
        for code in ("ACK", "FLL", "FLP", "FPR", "OPN", "REJ", "OUT", "TSC", "SUS"):
            self.assertIn(code, ORDER_STATUS_DESCRIPTIONS)

    def test_alive_partial_fill_is_not_terminal(self):
        # FPR is "Partial Fill (Alive)" and can still execute more.
        self.assertNotIn("FPR", TERMINAL_ORDER_STATUSES)
        # FLP is "Partial Fill (UROut)" - the remainder was cancelled.
        self.assertIn("FLP", TERMINAL_ORDER_STATUSES)
        # Cancel/replace attempts are not the end of the order.
        for code in ("LAT", "RJC", "UCN", "RSN", "UCH"):
            self.assertNotIn(code, TERMINAL_ORDER_STATUSES)

    def test_fill_bearing_statuses(self):
        self.assertEqual(FILL_BEARING_STATUSES, frozenset({"FLL", "FLP", "FPR"}))


class TestDeduplication(unittest.TestCase):
    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")

    def test_is_duplicate_is_pure(self):
        update = self.mgr.classify_frame(frame()).order
        self.assertFalse(self.mgr.is_duplicate(update))
        # Asking twice must not itself record anything: an unapplied event has
        # to stay recoverable.
        self.assertFalse(self.mgr.is_duplicate(update))
        self.assertEqual(self.mgr.tracked_signature_count, 0)
        self.mgr.mark_processed(update)
        self.assertTrue(self.mgr.is_duplicate(update))

    def test_uncommitted_update_survives_a_crash(self):
        """Regression: the original marked the signature inside the check.

        A consumer that failed while applying the event could never see it
        again, because catch-up suppressed it as already processed.
        """
        raw = frame(status="FLL", legs=(("MSFT", "10", "10", "0"),))
        first = self.mgr.parse_stream_message(raw)
        self.assertIsNotNone(first)
        # Simulate the ledger raising before mark_processed() is reached.
        replayed = self.mgr.reconcile_missed_orders(
            lambda since: [order_payload(status="FLL", legs=(("MSFT", "10", "10", "0"),))]
        )
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].filled_quantity, Decimal("10"))

    def test_successive_partial_fills_are_distinct_events(self):
        """Regression: every partial fill after the first used to be dropped.

        The old key was ``OrderID:Status:FilledQuantity`` over a field v3 never
        sends, so consecutive FPR frames on one order collapsed to one
        signature.
        """
        applied = []
        for executed, remaining in (("2", "8"), ("5", "5"), ("9", "1")):
            update = self.mgr.parse_stream_message(
                frame(status="FPR", legs=(("MSFT", "10", executed, remaining),))
            )
            self.assertIsNotNone(update, f"partial fill at {executed} was dropped")
            self.mgr.mark_processed(update)
            applied.append(update.filled_quantity)
        self.assertEqual(applied, [Decimal("2"), Decimal("5"), Decimal("9")])

    def test_identical_replayed_frame_is_suppressed(self):
        raw = frame(status="FLL", legs=(("MSFT", "10", "10", "0"),))
        first = self.mgr.parse_stream_message(raw)
        self.mgr.mark_processed(first)
        self.assertIsNone(self.mgr.parse_stream_message(raw))

    def test_decimal_formatting_variants_collapse_to_one_signature(self):
        a = build_order_update(order_payload(legs=(("MSFT", "10", "4", "6"),), filled_price="112.28"))
        b = build_order_update(
            order_payload(legs=(("MSFT", "10.00", "4.000", "6.0"),), filled_price="112.280")
        )
        self.assertEqual(a.signature, b.signature)

    def test_multi_leg_reallocation_is_not_collapsed(self):
        """Two legs at 1/9 then 9/1 have an equal total but are distinct states."""
        a = build_order_update(
            order_payload(legs=(("MSFT", "10", "1", "9"), ("AAPL", "10", "9", "1")))
        )
        b = build_order_update(
            order_payload(legs=(("MSFT", "10", "9", "1"), ("AAPL", "10", "1", "9")))
        )
        self.assertEqual(a.filled_quantity, b.filled_quantity)
        self.assertNotEqual(a.signature, b.signature)

    def test_leg_ordering_does_not_change_the_signature(self):
        """The stream and the two REST endpoints may order legs differently."""
        a = build_order_update(
            order_payload(legs=(("MSFT", "10", "4", "6"), ("AAPL", "10", "7", "3")))
        )
        b = build_order_update(
            order_payload(legs=(("AAPL", "10", "7", "3"), ("MSFT", "10", "4", "6")))
        )
        self.assertEqual(a.signature, b.signature)

    def test_signature_set_is_bounded(self):
        mgr = TradeStationStreamManager(account_id="SIM123456", max_tracked_signatures=3)
        for i in range(10):
            update = mgr.classify_frame(frame(order_id=f"TS{i}")).order
            mgr.mark_processed(update)
        self.assertEqual(mgr.tracked_signature_count, 3)
        # Oldest evicted, newest retained.
        self.assertFalse(mgr.is_duplicate(mgr.classify_frame(frame(order_id="TS0")).order))
        self.assertTrue(mgr.is_duplicate(mgr.classify_frame(frame(order_id="TS9")).order))


class TestControlFrameEscalation(unittest.TestCase):
    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")

    def test_go_away_raises_rather_than_returning_none(self):
        """Returning None for GoAway is how a bot silently stops getting fills."""
        with self.assertRaises(TradeStationStreamError):
            self.mgr.parse_stream_message('{"StreamStatus": "GoAway"}')

    def test_error_frame_raises(self):
        with self.assertRaises(TradeStationStreamError):
            self.mgr.parse_stream_message('{"Error":"Forbidden","Message":"no access"}')

    def test_end_snapshot_and_heartbeat_return_none(self):
        self.assertIsNone(self.mgr.parse_stream_message('{"StreamStatus": "EndSnapshot"}'))
        self.assertIsNone(self.mgr.parse_stream_message('{"Heartbeat": 3}'))


class TestStallDetection(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr = TradeStationStreamManager(account_id="SIM123456", monotonic=self.clock)

    def test_heartbeat_resets_the_stall_timer(self):
        self.clock.advance(14.0)
        self.assertFalse(self.mgr.is_stream_stalled())
        self.mgr.classify_frame('{"Heartbeat": 1}')
        self.assertEqual(self.mgr.seconds_since_last_frame(), 0.0)

    def test_silence_beyond_threshold_is_a_stall(self):
        self.clock.advance(DEFAULT_STALL_THRESHOLD_SECONDS)
        self.assertFalse(self.mgr.is_stream_stalled())  # exactly at the boundary
        self.clock.advance(0.01)
        self.assertTrue(self.mgr.is_stream_stalled())

    def test_malformed_frame_still_counts_as_liveness(self):
        self.clock.advance(20.0)
        self.mgr.classify_frame("{not json")
        self.assertFalse(self.mgr.is_stream_stalled())

    def test_reconnect_resets_the_timer(self):
        self.clock.advance(30.0)
        self.assertTrue(self.mgr.is_stream_stalled())
        self.mgr.mark_connected()
        self.assertFalse(self.mgr.is_stream_stalled())
        self.assertTrue(self.mgr.is_connected)
        self.mgr.mark_disconnected()
        self.assertFalse(self.mgr.is_connected)

    def test_threshold_must_exceed_heartbeat_interval(self):
        with self.assertRaises(ValueError):
            TradeStationStreamManager("SIM1", stall_threshold_seconds=HEARTBEAT_IDLE_SECONDS)

    def test_constructor_validates_inputs(self):
        with self.assertRaises(ValueError):
            TradeStationStreamManager("")
        with self.assertRaises(ValueError):
            TradeStationStreamManager("SIM1", max_tracked_signatures=0)


class TestCatchUpWindow(unittest.TestCase):
    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")
        self.now = datetime(2026, 9, 2, 14, 30, tzinfo=timezone.utc)

    def test_cold_start_reaches_back_one_day(self):
        """/orders covers only today, so a cold start must not anchor on today.

        An order filled in the previous session's extended hours is absent from
        /orders and absent from historicalorders?since=today.
        """
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-09-01")

    def test_non_finite_quantity_does_not_poison_the_ledger(self):
        """json.loads accepts bare NaN/Infinity, and Decimal parses them."""
        mgr = TradeStationStreamManager(account_id="SIM123456")
        update = mgr.classify_frame(
            '{"OrderID":"TS1","Status":"FLL","FilledPrice":"Infinity",'
            '"Legs":[{"Symbol":"MSFT","ExecQuantity":NaN}]}'
        ).order
        self.assertEqual(update.filled_quantity, Decimal("0"))
        self.assertEqual(update.average_price, Decimal("0"))
        self.assertTrue(update.filled_quantity.is_finite())

    def test_high_precision_fractional_seconds_still_parse(self):
        """RFC3339 allows more than six fractional digits; datetime does not."""
        self.mgr.classify_frame(frame(opened="2026-08-30T13:00:00.123456789Z"))
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-08-30")

    def test_uses_broker_event_date_not_local_clock(self):
        self.mgr.classify_frame(
            frame(opened="2026-08-30T13:00:00Z", closed="2026-08-31T18:05:00Z")
        )
        self.assertEqual(
            self.mgr.last_broker_event_utc,
            datetime(2026, 8, 31, 18, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-08-31")

    def test_heartbeat_timestamp_advances_broker_time(self):
        self.mgr.classify_frame('{"Heartbeat": 1, "Timestamp": "2026-09-01T09:00:00Z"}')
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-09-01")

    def test_broker_time_never_moves_backwards(self):
        self.mgr.classify_frame(frame(order_id="A", opened="2026-09-01T10:00:00Z"))
        self.mgr.classify_frame(frame(order_id="B", opened="2026-08-01T10:00:00Z"))
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-09-01")

    def test_clamped_to_the_90_day_window(self):
        self.mgr.classify_frame(frame(opened="2020-01-01T00:00:00Z"))
        expected = (self.now.date() - timedelta(days=MAX_HISTORICAL_LOOKBACK_DAYS)).strftime(
            "%Y-%m-%d"
        )
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), expected)

    def test_future_broker_time_is_clamped_to_today(self):
        self.mgr.classify_frame(frame(opened="2027-01-01T00:00:00Z"))
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-09-02")

    def test_naive_and_offset_timestamps_normalize_to_utc(self):
        self.mgr.classify_frame(frame(opened="2026-08-31T23:30:00-05:00"))
        # 23:30 US-Central is 04:30 UTC the following day.
        self.assertEqual(self.mgr.catch_up_since_date(now=self.now), "2026-09-01")


class TestReconciliation(unittest.TestCase):
    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")

    def test_since_argument_is_a_date_string(self):
        seen = {}

        def fetch(since):
            seen["since"] = since
            return []

        self.mgr.classify_frame(frame(opened="2026-08-30T13:00:00Z"))
        self.mgr.reconcile_missed_orders(fetch)
        # Not a Unix timestamp: historicalorders takes a date, nothing finer.
        self.assertEqual(seen["since"], "2026-08-30")
        datetime.strptime(seen["since"], "%Y-%m-%d")

    def test_only_uncommitted_states_are_returned(self):
        pre = self.mgr.parse_stream_message(frame(order_id="TS101", status="ACK",
                                                  legs=(("MSFT", "10", "0", "10"),)))
        self.mgr.mark_processed(pre)

        def fetch(since):
            return [
                order_payload(order_id="TS101", status="ACK", legs=(("MSFT", "10", "0", "10"),)),
                order_payload(order_id="TS102", status="FLL", legs=(("MSFT", "25", "25", "0"),),
                              filled_price="300.5", closed="2026-09-02T10:00:00Z"),
            ]

        reconciled = self.mgr.reconcile_missed_orders(fetch)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].order_id, "TS102")
        self.assertEqual(reconciled[0].filled_quantity, Decimal("25"))
        self.assertTrue(reconciled[0].is_terminal)

    def test_overlap_between_orders_and_historicalorders_is_deduped(self):
        """The two endpoints overlap, so one order can appear twice per batch."""
        row = order_payload(order_id="TS200", status="FLL", legs=(("MSFT", "5", "5", "0"),))
        reconciled = self.mgr.reconcile_missed_orders(lambda since: [row, dict(row)])
        self.assertEqual(len(reconciled), 1)

    def test_reconcile_does_not_commit(self):
        reconciled = self.mgr.reconcile_missed_orders(
            lambda since: [order_payload(order_id="TS300")]
        )
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(self.mgr.tracked_signature_count, 0)
        self.assertFalse(self.mgr.is_duplicate(reconciled[0]))

    def test_fetch_failure_is_wrapped(self):
        def boom(since):
            raise ConnectionError("connection reset")

        with self.assertRaises(TradeStationStreamError):
            self.mgr.reconcile_missed_orders(boom)

    def test_none_payload_is_rejected(self):
        with self.assertRaises(TradeStationStreamError):
            self.mgr.reconcile_missed_orders(lambda since: None)

    def test_generator_failing_mid_iteration_is_wrapped(self):
        """A generator raises while being consumed, not when it is called."""

        def paginating_fetch(since):
            yield order_payload(order_id="TS1")
            raise ConnectionError("nextToken page 2 failed")

        with self.assertRaises(TradeStationStreamError):
            self.mgr.reconcile_missed_orders(paginating_fetch)
        # A half-consumed recovery batch must not leave partial state behind.
        self.assertEqual(self.mgr.tracked_signature_count, 0)

    def test_malformed_rows_are_skipped_not_fatal(self):
        reconciled = self.mgr.reconcile_missed_orders(
            lambda since: ["junk", None, {"Status": "FLL"}, order_payload(order_id="TS400")]
        )
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].order_id, "TS400")

    def test_recovered_close_time_advances_the_catch_up_anchor(self):
        self.mgr.reconcile_missed_orders(
            lambda since: [order_payload(order_id="TS500", status="FLL",
                                         closed="2026-09-01T20:00:00Z")]
        )
        self.assertEqual(
            self.mgr.last_broker_event_utc,
            datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc),
        )


class TestBuildOrderUpdate(unittest.TestCase):
    def test_rest_and_stream_rows_share_one_schema(self):
        payload = order_payload(order_id="TS600", status="FLL",
                                legs=(("MSFT", "10", "10", "0"),))
        from_rest = build_order_update(payload)
        mgr = TradeStationStreamManager(account_id="SIM123456")
        from_stream = mgr.classify_frame(json.dumps(payload)).order
        self.assertEqual(from_rest.signature, from_stream.signature)

    def test_missing_order_id_returns_none(self):
        self.assertIsNone(build_order_update({"Status": "FLL"}))
        self.assertIsNone(build_order_update({"OrderID": "", "Status": "FLL"}))

    def test_order_with_no_legs_is_valid(self):
        update = build_order_update({"OrderID": "TS700", "Status": "REJ",
                                     "RejectReason": "Insufficient buying power"})
        self.assertEqual(update.filled_quantity, Decimal("0"))
        self.assertEqual(update.reject_reason, "Insufficient buying power")
        self.assertTrue(update.is_terminal)

    def test_update_is_immutable(self):
        update = build_order_update(order_payload())
        with self.assertRaises(Exception):
            update.filled_quantity = Decimal("999")

    def test_update_type(self):
        self.assertIsInstance(build_order_update(order_payload()), TradeStationOrderUpdate)


if __name__ == "__main__":
    unittest.main()
