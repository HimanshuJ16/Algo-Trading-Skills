"""
Unit tests for redis-streams-multi-consumer-tick-fanout.

The tests are organised around behaviour that production depends on:

1. Fanout: independent consumer groups each see every tick; consumers inside one
   group share it.
2. Delivery semantics: ``>`` returns only never-delivered entries, so an
   acknowledged tick is never redelivered and an un-acknowledged one is not
   redelivered either until it is claimed.
3. Payload integrity: an undecodable or trimmed-away entry never becomes a
   zero-priced tick.
4. Recovery: XPENDING discovery, XCLAIM idle gating and single-winner claiming,
   XAUTOCLAIM sweeps, delivery counters and poison detection.
5. Client-shape parity: the manager works against RESP2 and RESP3 reply shapes
   and against byte-valued replies from a client without ``decode_responses``.

Time is injected (``clock_ms``) so idle-time assertions are exact rather than
dependent on ``sleep``.
"""
import logging
import unittest

from redis_tick_fanout import (
    DEFAULT_MAXLEN,
    MockRedisStreamEngine,
    RedisTickFanoutManager,
    TickBatch,
    TickData,
    TickDecodeError,
    TickValidationError,
    _iter_stream_entries,
    _parse_stream_id,
)


def setUpModule():
    """Keeps the engine's own log records out of the suite's stderr output."""
    module_logger = logging.getLogger("redis_tick_fanout")
    module_logger.addHandler(logging.NullHandler())
    module_logger.propagate = False


class FakeClock:
    """Manually advanced millisecond clock."""

    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self.now_ms = start_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms


def make_manager(clock=None, **kwargs):
    engine = MockRedisStreamEngine(clock_ms=clock)
    return RedisTickFanoutManager(redis_client=engine, stream_name="test_ticks", **kwargs), engine


def tick(symbol="AAPL", price=150.25, volume=100.0, timestamp=1_700_000_000.0):
    return TickData(symbol=symbol, last_price=price, volume=volume, timestamp=timestamp)


class TestTickDataValidation(unittest.TestCase):
    def test_valid_tick_round_trips_through_field_map(self):
        original = tick(price=150.25, volume=1_000.0, timestamp=1_700_000_000.123456)
        restored = TickData.from_dict(original.to_dict())
        self.assertEqual(restored.symbol, "AAPL")
        # repr() round-trips a float exactly; str() truncation would not.
        self.assertEqual(restored.last_price, 150.25)
        self.assertEqual(restored.timestamp, 1_700_000_000.123456)

    def test_empty_or_whitespace_symbol_rejected(self):
        for bad in ("", "   "):
            with self.assertRaises(TickValidationError):
                tick(symbol=bad)

    def test_non_finite_values_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(TickValidationError):
                tick(price=bad)
            with self.assertRaises(TickValidationError):
                tick(volume=bad)

    def test_non_positive_price_rejected_by_default_and_allowed_on_opt_in(self):
        with self.assertRaises(TickValidationError):
            tick(price=0.0)
        with self.assertRaises(TickValidationError):
            tick(price=-37.63)
        allowed = TickData(
            symbol="CLK0", last_price=-37.63, volume=1.0, timestamp=1_587_340_800.0,
            allow_non_positive_price=True,
        )
        self.assertEqual(allowed.last_price, -37.63)

    def test_negative_volume_and_non_positive_timestamp_rejected(self):
        with self.assertRaises(TickValidationError):
            tick(volume=-1.0)
        with self.assertRaises(TickValidationError):
            tick(timestamp=0.0)

    def test_missing_field_raises_instead_of_defaulting_to_zero(self):
        # Regression: the previous implementation returned symbol="" price=0.0.
        with self.assertRaises(TickDecodeError):
            TickData.from_dict({"symbol": "AAPL", "last_price": "10.0"})

    def test_empty_payload_of_trimmed_entry_raises(self):
        with self.assertRaises(TickDecodeError):
            TickData.from_dict({})
        with self.assertRaises(TickDecodeError):
            TickData.from_dict(None)

    def test_unparseable_number_raises(self):
        payload = tick().to_dict()
        payload["last_price"] = "not-a-number"
        with self.assertRaises(TickDecodeError):
            TickData.from_dict(payload)

    def test_byte_keys_and_values_decode(self):
        payload = {k.encode(): v.encode() for k, v in tick().to_dict().items()}
        decoded = TickData.from_dict(payload)
        self.assertEqual(decoded.symbol, "AAPL")
        self.assertEqual(decoded.last_price, 150.25)


class TestStreamIdOrdering(unittest.TestCase):
    def test_sequence_numbers_compare_numerically_not_lexicographically(self):
        self.assertLess(_parse_stream_id("5-9"), _parse_stream_id("5-10"))
        self.assertLess("5-10", "5-9")  # the string comparison this replaces

    def test_incomplete_id_means_sequence_zero(self):
        self.assertEqual(_parse_stream_id("1700000000000"), (1_700_000_000_000, 0))

    def test_malformed_id_raises(self):
        with self.assertRaises(ValueError):
            _parse_stream_id("not-an-id")

    def test_ids_are_monotonic_when_the_clock_goes_backwards(self):
        clock = FakeClock()
        engine = MockRedisStreamEngine(clock_ms=clock)
        first = engine.xadd("s", {"a": "1"})
        clock.advance(-5_000)
        second = engine.xadd("s", {"a": "2"})
        self.assertLess(_parse_stream_id(first), _parse_stream_id(second))


class TestFanoutAndDeliverySemantics(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr, self.engine = make_manager(clock=self.clock)
        self.mgr.create_consumer_group("grp_strategy", start_id="0")
        self.mgr.create_consumer_group("grp_risk", start_id="0")

    def test_every_group_receives_every_tick(self):
        msg_id = self.mgr.publish_tick(tick())
        strategy = self.mgr.consume_ticks("grp_strategy", "worker_strat_1")
        risk = self.mgr.consume_ticks("grp_risk", "worker_risk_1")
        self.assertEqual([i for i, _ in strategy], [msg_id])
        self.assertEqual([i for i, _ in risk], [msg_id])
        self.assertEqual(strategy[0][1].symbol, "AAPL")
        self.assertEqual(risk[0][1], strategy[0][1])

    def test_consumers_in_one_group_split_the_stream(self):
        ids = [self.mgr.publish_tick(tick(symbol=s)) for s in ("AAPL", "MSFT", "NVDA")]
        first = self.mgr.consume_ticks("grp_strategy", "worker_a", count=2)
        second = self.mgr.consume_ticks("grp_strategy", "worker_b", count=2)
        self.assertEqual([i for i, _ in first], ids[:2])
        self.assertEqual([i for i, _ in second], ids[2:])
        # Load balancing is exactly why per-symbol ordering is not preserved
        # across consumers in one group.

    def test_acknowledged_tick_is_never_redelivered(self):
        # Regression: the previous engine redelivered every acknowledged entry
        # forever, because it treated "not in the PEL" as "new".
        msg_id = self.mgr.publish_tick(tick())
        self.assertEqual(len(self.mgr.consume_ticks("grp_strategy", "w1")), 1)
        self.assertEqual(self.mgr.acknowledge_tick("grp_strategy", msg_id), 1)
        self.assertEqual(self.mgr.consume_ticks("grp_strategy", "w1"), [])
        self.assertEqual(self.mgr.consume_ticks("grp_strategy", "w2"), [])

    def test_unacknowledged_tick_is_not_redelivered_under_new_messages(self):
        self.mgr.publish_tick(tick())
        self.assertEqual(len(self.mgr.consume_ticks("grp_strategy", "w1")), 1)
        self.assertEqual(self.mgr.consume_ticks("grp_strategy", "w1"), [])
        self.assertEqual(self.mgr.consume_ticks("grp_strategy", "w2"), [])

    def test_history_read_returns_own_pending_entries(self):
        msg_id = self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp_strategy", "w1")
        replay = self.mgr.consume_batch("grp_strategy", "w1", start_id="0")
        self.assertEqual([i for i, _ in replay.ticks], [msg_id])
        # Another consumer's history is not visible.
        self.assertEqual(self.mgr.consume_batch("grp_strategy", "w2", start_id="0").ticks, [])

    def test_group_created_at_dollar_skips_the_existing_backlog(self):
        self.mgr.publish_tick(tick(symbol="OLD"))
        self.assertTrue(self.mgr.create_consumer_group("grp_late"))  # start_id="$"
        self.assertEqual(self.mgr.consume_ticks("grp_late", "w1"), [])
        self.mgr.publish_tick(tick(symbol="NEW"))
        received = self.mgr.consume_ticks("grp_late", "w1")
        self.assertEqual([t.symbol for _, t in received], ["NEW"])

    def test_group_created_at_zero_replays_the_backlog(self):
        self.mgr.publish_tick(tick(symbol="OLD"))
        self.mgr.create_consumer_group("grp_replay", start_id="0")
        self.assertEqual([t.symbol for _, t in self.mgr.consume_ticks("grp_replay", "w1")], ["OLD"])

    def test_recreating_a_group_returns_false_and_does_not_raise(self):
        self.assertFalse(self.mgr.create_consumer_group("grp_strategy"))

    def test_non_busygroup_error_is_not_swallowed(self):
        class BrokenClient:
            def xgroup_create(self, *args, **kwargs):
                raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")

        mgr = RedisTickFanoutManager(redis_client=BrokenClient(), stream_name="s")
        with self.assertRaises(ConnectionError):
            mgr.create_consumer_group("grp")

    def test_count_bounds_the_batch(self):
        for _ in range(5):
            self.mgr.publish_tick(tick())
        self.assertEqual(len(self.mgr.consume_ticks("grp_strategy", "w1", count=2)), 2)
        with self.assertRaises(ValueError):
            self.mgr.consume_ticks("grp_strategy", "w1", count=0)


class TestAcknowledgement(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr, self.engine = make_manager(clock=self.clock)
        self.mgr.create_consumer_group("grp", start_id="0")

    def test_ack_removes_from_pel_and_is_idempotent(self):
        msg_id = self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w1")
        self.assertEqual(len(self.mgr.pending_summary("grp")), 1)
        self.assertEqual(self.mgr.acknowledge_tick("grp", msg_id), 1)
        self.assertEqual(self.mgr.pending_summary("grp"), [])
        self.assertEqual(self.mgr.acknowledge_tick("grp", msg_id), 0)

    def test_batch_ack_counts_only_pending_ids(self):
        ids = [self.mgr.publish_tick(tick()) for _ in range(3)]
        self.mgr.consume_ticks("grp", "w1")
        self.assertEqual(self.mgr.acknowledge_ticks("grp", *ids, "9999999-0"), 3)

    def test_ack_with_no_ids_is_a_no_op(self):
        self.assertEqual(self.mgr.acknowledge_ticks("grp"), 0)


class TestTrimmingAndPoisonPayloads(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr, self.engine = make_manager(clock=self.clock, maxlen=3, approximate_trim=False)
        self.mgr.create_consumer_group("grp", start_id="0")

    def test_maxlen_caps_the_stream(self):
        for _ in range(10):
            self.mgr.publish_tick(tick())
        self.assertEqual(self.engine.xlen("test_ticks"), 3)

    def test_trimming_keeps_pel_references_and_claim_drops_them(self):
        msg_id = self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w_crashed")
        for _ in range(5):  # trim the pending entry out of the stream (KEEPREF)
            self.mgr.publish_tick(tick())
        self.assertEqual([e.message_id for e in self.mgr.pending_summary("grp")][0], msg_id)

        self.clock.advance(60_000)
        claimed = self.mgr.claim_stale_ticks("grp", "w_new", min_idle_ms=30_000, msg_ids=[msg_id])
        self.assertEqual(claimed, [])  # not claimable: the payload is gone
        self.assertNotIn(msg_id, [e.message_id for e in self.mgr.pending_summary("grp")])

    def test_autoclaim_reports_trimmed_entries_as_deleted(self):
        msg_id = self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w_crashed")
        for _ in range(5):
            self.mgr.publish_tick(tick())
        self.clock.advance(60_000)
        result = self.mgr.recover_stale_ticks("grp", "w_new", min_idle_ms=30_000)
        self.assertEqual(result.deleted_ids, [msg_id])
        self.assertEqual(result.claimed, [])

    def test_unrecoverable_trimmed_entries_are_logged_as_a_warning(self):
        self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w_crashed")
        for _ in range(5):
            self.mgr.publish_tick(tick())
        self.clock.advance(60_000)
        with self.assertLogs("redis_tick_fanout", level=logging.WARNING) as logs:
            self.mgr.recover_stale_ticks("grp", "w_new", min_idle_ms=30_000)
        self.assertIn("unrecoverable", "".join(logs.output))

    def test_trimmed_pending_entry_does_not_decode_into_a_zero_price_tick(self):
        # Regression: a null payload previously decoded to symbol="" price=0.0.
        msg_id = self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w1")
        for _ in range(5):
            self.mgr.publish_tick(tick())
        batch = self.mgr.consume_batch("grp", "w1", start_id="0")
        self.assertEqual(batch.ticks, [])
        self.assertIn(msg_id, [i for i, _ in batch.malformed])

    def test_consume_ticks_drops_what_consume_batch_reports(self):
        self.engine.xadd("test_ticks", {"garbage": "1"})
        batch = self.mgr.consume_batch("grp", "w1")
        self.assertEqual(batch.ticks, [])
        self.assertEqual(len(batch.malformed), 1)


class TestRecovery(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.mgr, self.engine = make_manager(clock=self.clock)
        self.mgr.create_consumer_group("grp", start_id="0")
        self.msg_id = self.mgr.publish_tick(tick(symbol="NVDA", price=500.0))
        self.mgr.consume_ticks("grp", "w_crashed")

    def test_pending_summary_reports_owner_idle_and_delivery_count(self):
        self.clock.advance(1_500)
        pending = self.mgr.pending_summary("grp")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].consumer, "w_crashed")
        self.assertEqual(pending[0].idle_ms, 1_500)
        self.assertEqual(pending[0].delivery_count, 1)

    def test_claim_is_refused_below_the_idle_threshold(self):
        self.clock.advance(29_999)
        self.assertEqual(
            self.mgr.claim_stale_ticks("grp", "w_new", 30_000, [self.msg_id]), []
        )
        self.assertEqual(self.mgr.pending_summary("grp")[0].consumer, "w_crashed")

    def test_claim_at_exactly_the_idle_threshold_succeeds(self):
        self.clock.advance(30_000)
        claimed = self.mgr.claim_stale_ticks("grp", "w_new", 30_000, [self.msg_id])
        self.assertEqual([i for i, _ in claimed], [self.msg_id])
        self.assertEqual(claimed[0][1].symbol, "NVDA")
        self.assertEqual(claimed[0][1].last_price, 500.0)

    def test_claim_transfers_ownership_resets_idle_and_increments_deliveries(self):
        self.clock.advance(30_000)
        self.mgr.claim_stale_ticks("grp", "w_new", 30_000, [self.msg_id])
        pending = self.mgr.pending_summary("grp")[0]
        self.assertEqual(pending.consumer, "w_new")
        self.assertEqual(pending.idle_ms, 0)
        self.assertEqual(pending.delivery_count, 2)

    def test_only_one_of_two_racing_claimers_wins(self):
        self.clock.advance(30_000)
        first = self.mgr.claim_stale_ticks("grp", "w_a", 30_000, [self.msg_id])
        second = self.mgr.claim_stale_ticks("grp", "w_b", 30_000, [self.msg_id])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])  # idle was reset by the first claim
        self.assertEqual(self.mgr.pending_summary("grp")[0].consumer, "w_a")

    def test_claiming_an_unknown_or_acknowledged_id_returns_nothing(self):
        self.clock.advance(30_000)
        self.assertEqual(self.mgr.claim_stale_ticks("grp", "w_new", 0, ["1-1"]), [])
        self.mgr.acknowledge_tick("grp", self.msg_id)
        self.assertEqual(self.mgr.claim_stale_ticks("grp", "w_new", 0, [self.msg_id]), [])

    def test_reclaim_is_logged_so_duplicate_processing_is_visible(self):
        self.clock.advance(30_000)
        with self.assertLogs("redis_tick_fanout", level=logging.WARNING) as logs:
            self.mgr.claim_stale_ticks("grp", "w_new", 30_000, [self.msg_id])
        self.assertIn("Re-claimed stale tick", "".join(logs.output))

    def test_claim_rejects_negative_idle_and_empty_id_list(self):
        with self.assertRaises(ValueError):
            self.mgr.claim_stale_ticks("grp", "w_new", -1, [self.msg_id])
        self.assertEqual(self.mgr.claim_stale_ticks("grp", "w_new", 0, []), [])

    def test_autoclaim_sweeps_the_pel_and_completes_with_cursor_zero(self):
        self.clock.advance(30_000)
        result = self.mgr.recover_stale_ticks("grp", "w_new", 30_000)
        self.assertEqual([i for i, _ in result.claimed], [self.msg_id])
        self.assertEqual(result.cursor, "0-0")
        self.assertEqual(result.deleted_ids, [])

    def test_autoclaim_returns_a_resumable_cursor_when_count_is_reached(self):
        for _ in range(4):
            self.mgr.publish_tick(tick())
        self.mgr.consume_ticks("grp", "w_crashed")
        self.clock.advance(30_000)
        first = self.mgr.recover_stale_ticks("grp", "w_new", 30_000, count=2)
        self.assertEqual(len(first.claimed), 2)
        self.assertNotEqual(first.cursor, "0-0")
        second = self.mgr.recover_stale_ticks(
            "grp", "w_new", 30_000, count=10, start_id=first.cursor
        )
        self.assertEqual(len(second.claimed), 3)
        self.assertEqual(second.cursor, "0-0")

    def test_poison_entries_are_detected_by_delivery_count(self):
        for _ in range(3):
            self.clock.advance(30_000)
            self.mgr.claim_stale_ticks("grp", "w_retry", 30_000, [self.msg_id])
        self.assertEqual(self.mgr.pending_summary("grp")[0].delivery_count, 4)
        self.assertEqual(self.mgr.find_poison_entries("grp", max_delivery_count=10), [])
        poison = self.mgr.find_poison_entries("grp", max_delivery_count=3)
        self.assertEqual([e.message_id for e in poison], [self.msg_id])
        with self.assertRaises(ValueError):
            self.mgr.find_poison_entries("grp", max_delivery_count=0)


class TestClientShapeParity(unittest.TestCase):
    """The manager must survive both redis-py protocol versions and raw bytes."""

    def test_resp2_list_shape(self):
        entry = ("1-1", tick().to_dict())
        self.assertEqual(list(_iter_stream_entries([["s", [entry]]])), [("1-1", entry[1])])

    def test_resp3_map_shape(self):
        entry = ("1-1", tick().to_dict())
        self.assertEqual(list(_iter_stream_entries({"s": [[entry]]})), [("1-1", entry[1])])

    def test_empty_and_none_replies(self):
        self.assertEqual(list(_iter_stream_entries(None)), [])
        self.assertEqual(list(_iter_stream_entries([])), [])
        self.assertEqual(list(_iter_stream_entries([["s", []]])), [])

    def test_redis_6_2_two_element_autoclaim_reply(self):
        """Redis 6.2 omits the deleted-IDs element; 7.0+ adds it."""
        entry = ("1-1", tick().to_dict())

        class OldServerClient:
            def xautoclaim(self, *args, **kwargs):
                return ["0-0", [entry]]

        mgr = RedisTickFanoutManager(redis_client=OldServerClient(), stream_name="s")
        result = mgr.recover_stale_ticks("grp", "w1", 1_000)
        self.assertEqual(result.cursor, "0-0")
        self.assertEqual([i for i, _ in result.claimed], ["1-1"])
        self.assertEqual(result.deleted_ids, [])  # not three characters of "0-0"

    def test_manager_decodes_a_byte_valued_resp2_reply(self):
        payload = {k.encode(): v.encode() for k, v in tick().to_dict().items()}

        class ByteClient:
            def xreadgroup(self, groupname, consumername, streams, count=None, **kwargs):
                assert isinstance(streams, dict)  # redis-py requires a mapping
                return [[b"test_ticks", [(b"1-1", payload)]]]

        mgr = RedisTickFanoutManager(redis_client=ByteClient(), stream_name="test_ticks")
        batch = mgr.consume_batch("grp", "w1")
        self.assertEqual(batch.malformed, [])
        self.assertEqual(batch.ticks[0][0], "1-1")
        self.assertEqual(batch.ticks[0][1].symbol, "AAPL")


class TestManagerConfiguration(unittest.TestCase):
    def test_defaults(self):
        mgr = RedisTickFanoutManager()
        self.assertEqual(mgr.maxlen, DEFAULT_MAXLEN)
        self.assertTrue(mgr.approximate_trim)
        self.assertIsInstance(mgr.redis, MockRedisStreamEngine)

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            RedisTickFanoutManager(stream_name="  ")
        with self.assertRaises(ValueError):
            RedisTickFanoutManager(maxlen=-1)
        mgr, _ = make_manager()
        with self.assertRaises(ValueError):
            mgr.create_consumer_group("")
        with self.assertRaises(TypeError):
            mgr.publish_tick({"symbol": "AAPL"})

    def test_maxlen_none_disables_trimming(self):
        mgr, engine = make_manager(maxlen=None)
        for _ in range(50):
            mgr.publish_tick(tick())
        self.assertEqual(engine.xlen("test_ticks"), 50)

    def test_consuming_from_an_unknown_group_raises(self):
        mgr, _ = make_manager()
        mgr.publish_tick(tick())
        with self.assertRaises(ValueError):
            mgr.consume_ticks("no_such_group", "w1")

    def test_tick_batch_length_counts_decodable_ticks_only(self):
        batch = TickBatch(ticks=[("1-1", tick())], malformed=[("1-2", "bad")])
        self.assertEqual(len(batch), 1)


if __name__ == "__main__":
    unittest.main()
