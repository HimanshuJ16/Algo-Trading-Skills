"""
Unit tests for grpc-streaming-for-internal-service-communication skill.
"""
import json
import math
import unittest
from grpc_stream_engine import (
    DEFAULT_MAX_BACKOFF_S,
    GRPCChannelState,
    GRPCStreamingMarketDataEngine,
    ProtobufTickFrame,
    TICK_FRAME_BYTES,
)


class TestProtobufTickFrame(unittest.TestCase):

    def setUp(self):
        self.sample_frame = ProtobufTickFrame(
            sequence_id=100200300,
            timestamp_ns=1784948000000000,
            symbol_id=101,
            bid_price=65000.50,
            ask_price=65001.00,
            volume=1.5,
        )

    def test_protobuf_binary_serialization_roundtrip(self):
        raw_bytes = self.sample_frame.serialize_binary()
        # Fixed-width layout >QQIddd == 8+8+4+8+8+8 bytes.
        self.assertEqual(len(raw_bytes), 44)
        self.assertEqual(TICK_FRAME_BYTES, 44)

        restored = ProtobufTickFrame.deserialize_binary(raw_bytes)
        self.assertEqual(restored.sequence_id, 100200300)
        self.assertEqual(restored.timestamp_ns, 1784948000000000)
        self.assertEqual(restored.symbol_id, 101)
        self.assertAlmostEqual(restored.bid_price, 65000.50)
        self.assertAlmostEqual(restored.ask_price, 65001.00)
        self.assertAlmostEqual(restored.volume, 1.5)

    def test_deserialize_rejects_wrong_length_buffer(self):
        raw_bytes = self.sample_frame.serialize_binary()
        # A truncated read must fail loudly, not raise an opaque struct.error.
        with self.assertRaises(ValueError):
            ProtobufTickFrame.deserialize_binary(raw_bytes[:-1])
        with self.assertRaises(ValueError):
            ProtobufTickFrame.deserialize_binary(raw_bytes + b"\x00")
        with self.assertRaises(ValueError):
            ProtobufTickFrame.deserialize_binary(b"")

    def test_non_finite_and_out_of_range_fields_rejected(self):
        # NaN/Inf pack into a double without complaint and would corrupt risk state.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                ProtobufTickFrame(1, 1, 1, bad, 2.0, 1.0)
        # uint64/uint32 range violations.
        with self.assertRaises(ValueError):
            ProtobufTickFrame(-1, 1, 1, 1.0, 2.0, 1.0)
        with self.assertRaises(ValueError):
            ProtobufTickFrame(1, 1, 2 ** 32, 1.0, 2.0, 1.0)
        with self.assertRaises(ValueError):
            ProtobufTickFrame(1, 1, 1, 1.0, 2.0, -1.0)

    def test_negative_and_crossed_prices_are_accepted(self):
        # Negative settlement prices (e.g. WTI, April 2020) and cross-venue crossed
        # quotes are real market states and must not be rejected as bad data.
        frame = ProtobufTickFrame(1, 1, 1, bid_price=-37.63, ask_price=-37.70, volume=1.0)
        restored = ProtobufTickFrame.deserialize_binary(frame.serialize_binary())
        self.assertAlmostEqual(restored.bid_price, -37.63)
        self.assertGreater(restored.bid_price, restored.ask_price)

    def test_proto3_wire_size_matches_hand_derived_encoding(self):
        # Hand-derived from the protobuf encoding rules for this schema:
        #   field 1 uint64 100200300      -> 1 key + 4 varint  =  5
        #   field 2 uint64 1784948000000000 -> 1 key + 8 varint  =  9
        #   field 3 uint32 101            -> 1 key + 1 varint  =  2
        #   fields 4,5,6 double           -> 3 * (1 key + 8)   = 27
        self.assertEqual(self.sample_frame.proto3_wire_size(), 43)

        # proto3 omits default-valued fields entirely, so the wire size is variable -
        # the fixed 44-byte struct size is not the protobuf size.
        empty = ProtobufTickFrame(0, 0, 0, 0.0, 0.0, 0.0)
        self.assertEqual(empty.proto3_wire_size(), 0)

        # Maximum: 1+10 varint twice, 1+5 varint once, plus 3 * 9 fixed64.
        widest = ProtobufTickFrame(2 ** 64 - 1, 2 ** 64 - 1, 2 ** 32 - 1, 1.0, 2.0, 3.0)
        self.assertEqual(widest.proto3_wire_size(), 55)
        self.assertGreater(widest.proto3_wire_size(), TICK_FRAME_BYTES)


class TestGRPCChannelLifecycle(unittest.TestCase):

    def setUp(self):
        self.engine = GRPCStreamingMarketDataEngine("execution-service-v1")
        self.sample_frame = ProtobufTickFrame(
            sequence_id=100200300,
            timestamp_ns=1784948000000000,
            symbol_id=101,
            bid_price=65000.50,
            ask_price=65001.00,
            volume=1.5,
        )

    def test_grpc_channel_streaming(self):
        self.assertTrue(self.engine.connect_channel())
        self.assertEqual(self.engine.state, GRPCChannelState.READY)

        payload = self.engine.stream_push_frame(self.sample_frame)
        self.assertEqual(len(payload), 44)
        self.assertEqual(len(self.engine.received_frames), 1)

    def test_push_on_non_ready_channel_raises(self):
        # IDLE: never established.
        with self.assertRaises(RuntimeError):
            self.engine.stream_push_frame(self.sample_frame)
        # TRANSIENT_FAILURE: dropped mid-stream, must reconnect before pushing.
        self.engine.connect_channel()
        self.engine.mark_transient_failure("peer reset")
        self.assertEqual(self.engine.state, GRPCChannelState.TRANSIENT_FAILURE)
        with self.assertRaises(RuntimeError):
            self.engine.stream_push_frame(self.sample_frame)
        self.assertEqual(self.engine.frames_sent, 0)

    def test_failed_connect_enters_transient_failure_and_recovers(self):
        self.assertFalse(self.engine.connect_channel(connect_succeeds=False))
        self.assertEqual(self.engine.state, GRPCChannelState.TRANSIENT_FAILURE)
        self.assertEqual(self.engine.reconnect_attempts, 1)
        # TRANSIENT_FAILURE -> CONNECTING -> READY resets the backoff schedule.
        self.assertTrue(self.engine.connect_channel())
        self.assertEqual(self.engine.state, GRPCChannelState.READY)
        self.assertEqual(self.engine.reconnect_attempts, 0)
        self.assertEqual(self.engine.next_backoff_delay_s(), 0.0)

    def test_backoff_follows_grpc_reference_schedule(self):
        # INITIAL_BACKOFF 1s, MULTIPLIER 1.6, MAX_BACKOFF 120s.
        self.engine.reconnect_attempts = 1
        self.assertAlmostEqual(self.engine.next_backoff_delay_s(), 1.0)
        self.engine.reconnect_attempts = 2
        self.assertAlmostEqual(self.engine.next_backoff_delay_s(), 1.6)
        self.engine.reconnect_attempts = 3
        self.assertAlmostEqual(self.engine.next_backoff_delay_s(), 2.56)
        # 1.6**11 == 175.9 > 120, so the cap binds from attempt 12 onward.
        self.engine.reconnect_attempts = 12
        self.assertAlmostEqual(self.engine.next_backoff_delay_s(), DEFAULT_MAX_BACKOFF_S)
        self.engine.reconnect_attempts = 50
        self.assertAlmostEqual(self.engine.next_backoff_delay_s(), DEFAULT_MAX_BACKOFF_S)

    def test_goaway_drops_ready_channel_to_idle_silently(self):
        self.engine.connect_channel()
        # In-flight RPCs drain first: the channel stays READY.
        self.assertEqual(
            self.engine.on_goaway_received(has_pending_rpcs=True), GRPCChannelState.READY
        )
        # With nothing in flight the channel goes IDLE with no error on the stream,
        # so the next push must fail rather than silently discard the frame.
        self.assertEqual(
            self.engine.on_goaway_received(has_pending_rpcs=False), GRPCChannelState.IDLE
        )
        with self.assertRaises(RuntimeError):
            self.engine.stream_push_frame(self.sample_frame)
        # A duplicate/late GOAWAY on an already-IDLE channel is a no-op, not a crash.
        self.assertEqual(
            self.engine.on_goaway_received(has_pending_rpcs=False), GRPCChannelState.IDLE
        )

    def test_shutdown_is_terminal(self):
        self.engine.connect_channel()
        self.engine.shutdown()
        self.assertEqual(self.engine.state, GRPCChannelState.SHUTDOWN)
        self.engine.shutdown()  # idempotent
        self.assertEqual(self.engine.state, GRPCChannelState.SHUTDOWN)
        with self.assertRaises(RuntimeError):
            self.engine.connect_channel()
        with self.assertRaises(RuntimeError):
            self.engine.stream_push_frame(self.sample_frame)


class TestStreamIntegrity(unittest.TestCase):

    @staticmethod
    def _frame(seq):
        return ProtobufTickFrame(
            sequence_id=seq,
            timestamp_ns=1784948000000000 + seq,
            symbol_id=101,
            bid_price=65000.50,
            ask_price=65001.00,
            volume=1.5,
        )

    def test_thousand_frame_stream_reports_zero_drops(self):
        engine = GRPCStreamingMarketDataEngine("risk-engine-gateway", max_retained_frames=1000)
        engine.connect_channel()
        for seq in range(1, 1001):
            engine.stream_push_frame(self._frame(seq))

        report = engine.stream_integrity_report()
        self.assertEqual(report["frames_sent"], 1000)
        self.assertEqual(report["frames_received"], 1000)
        self.assertEqual(report["sequence_gap_events"], 0)
        self.assertEqual(report["missing_frame_count"], 0)
        self.assertEqual(report["out_of_order_frames"], 0)
        self.assertTrue(report["is_contiguous"])

    def test_sequence_gap_and_duplicate_are_detected(self):
        engine = GRPCStreamingMarketDataEngine("risk-engine-gateway")
        engine.connect_channel()
        for seq in (1, 2, 5, 5, 4):
            engine.stream_push_frame(self._frame(seq))

        report = engine.stream_integrity_report()
        self.assertEqual(report["sequence_gap_events"], 1)
        self.assertEqual(report["missing_frame_count"], 2)  # 3 and 4 skipped
        self.assertEqual(report["out_of_order_frames"], 2)  # repeat of 5, then 4
        self.assertFalse(report["is_contiguous"])

    def test_retained_frames_are_bounded(self):
        engine = GRPCStreamingMarketDataEngine("risk-engine-gateway", max_retained_frames=10)
        engine.connect_channel()
        for seq in range(1, 101):
            engine.stream_push_frame(self._frame(seq))

        # Counters keep the full history; the retained buffer does not grow unbounded.
        self.assertEqual(engine.frames_received, 100)
        self.assertEqual(len(engine.received_frames), 10)
        self.assertEqual(engine.received_frames[-1].sequence_id, 100)
        self.assertEqual(engine.received_frames[0].sequence_id, 91)

    def test_invalid_retention_size_rejected(self):
        with self.assertRaises(ValueError):
            GRPCStreamingMarketDataEngine("risk-engine-gateway", max_retained_frames=0)


class TestSerializationBenchmark(unittest.TestCase):

    def setUp(self):
        self.engine = GRPCStreamingMarketDataEngine("execution-service-v1")
        self.sample_frame = ProtobufTickFrame(
            sequence_id=100200300,
            timestamp_ns=1784948000000000,
            symbol_id=101,
            bid_price=65000.50,
            ask_price=65001.00,
            volume=1.5,
        )

    def test_benchmark_protobuf_vs_json_savings(self):
        results = self.engine.benchmark_protobuf_vs_json(self.sample_frame, iterations=200)

        self.assertEqual(results["protobuf_bytes"], 44)
        self.assertEqual(results["proto3_wire_bytes"], 43)
        self.assertEqual(results["iterations"], 200)

        # Compact JSON for this frame is exactly 126 bytes: the 137-byte default
        # rendering minus the 11 cosmetic spaces json.dumps inserts after ':' and ','.
        self.assertEqual(results["json_bytes"], 126)

        # >50% byte reduction holds; the "3x-5x smaller" framing does not.
        self.assertGreater(results["size_reduction_percent"], 50.0)
        self.assertLess(results["json_bytes"] / results["protobuf_bytes"], 3.0)
        self.assertGreater(results["proto3_size_reduction_percent"], 50.0)

    def test_benchmark_json_baseline_is_compact(self):
        # Guards against regressing to json.dumps defaults, which inflate the JSON
        # baseline by ~8% and overstate the binary advantage.
        results = self.engine.benchmark_protobuf_vs_json(self.sample_frame, iterations=10)
        default_len = len(json.dumps(self.sample_frame.to_json_dict()).encode("utf-8"))
        self.assertEqual(default_len, 137)
        self.assertLess(results["json_bytes"], default_len)

    def test_benchmark_rejects_non_positive_iterations(self):
        with self.assertRaises(ValueError):
            self.engine.benchmark_protobuf_vs_json(self.sample_frame, iterations=0)

    def test_benchmark_timings_are_finite_per_operation_means(self):
        results = self.engine.benchmark_protobuf_vs_json(self.sample_frame, iterations=500)
        self.assertTrue(math.isfinite(results["protobuf_serialize_us"]))
        self.assertTrue(math.isfinite(results["json_serialize_us"]))
        self.assertGreaterEqual(results["protobuf_serialize_us"], 0.0)
        self.assertGreaterEqual(results["json_serialize_us"], 0.0)


if __name__ == "__main__":
    unittest.main()
