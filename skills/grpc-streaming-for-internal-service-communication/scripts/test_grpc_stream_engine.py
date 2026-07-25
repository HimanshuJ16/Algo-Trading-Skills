"""
Unit tests for grpc-streaming-for-internal-service-communication skill.
"""
import unittest
from grpc_stream_engine import GRPCChannelState, GRPCStreamingMarketDataEngine, ProtobufTickFrame


class TestGRPCStreamingMarketDataEngine(unittest.TestCase):

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

    def test_protobuf_binary_serialization_roundtrip(self):
        raw_bytes = self.sample_frame.serialize_binary()
        # Binary frame length should be exactly 44 bytes
        self.assertEqual(len(raw_bytes), 44)

        restored = ProtobufTickFrame.deserialize_binary(raw_bytes)
        self.assertEqual(restored.sequence_id, 100200300)
        self.assertEqual(restored.symbol_id, 101)
        self.assertAlmostEqual(restored.bid_price, 65000.50)
        self.assertAlmostEqual(restored.ask_price, 65001.00)

    def test_grpc_channel_streaming(self):
        self.engine.connect_channel()
        self.assertEqual(self.engine.state, GRPCChannelState.READY)

        payload = self.engine.stream_push_frame(self.sample_frame)
        self.assertEqual(len(payload), 44)
        self.assertEqual(len(self.engine.received_frames), 1)

    def test_benchmark_protobuf_vs_json_savings(self):
        results = self.engine.benchmark_protobuf_vs_json(self.sample_frame)

        self.assertEqual(results["protobuf_bytes"], 44)
        self.assertGreater(results["json_bytes"], 100)
        # Protobuf should achieve >50% byte size reduction compared to JSON
        self.assertGreater(results["size_reduction_percent"], 50.0)


if __name__ == "__main__":
    unittest.main()
