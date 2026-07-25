"""
Unit tests for market-data-replay-harness-for-integration-testing skill.
"""
import unittest
from replay_harness import MarketDataReplayHarness, ReplayTick


class TestMarketDataReplayHarness(unittest.TestCase):

    def setUp(self):
        self.harness = MarketDataReplayHarness(speed_multiplier=100.0)  # 100x fast forward
        self.ticks = [
            ReplayTick("AAPL", 1000.0, 1, 150.00, 150.05, 100),
            ReplayTick("AAPL", 1001.0, 2, 150.10, 150.15, 200),
            ReplayTick("AAPL", 1002.0, 3, 150.20, 150.25, 300),
        ]

    def test_asap_replay_execution(self):
        orders = []

        def dummy_strategy(tick):
            if tick.price >= 150.20:
                return {"action": "SELL", "price": tick.price}
            return None

        summary = self.harness.replay_session(self.ticks, dummy_strategy, asap_mode=True)

        self.assertEqual(summary.total_ticks_replayed, 3)
        self.assertEqual(summary.emitted_orders_count, 1)
        self.assertEqual(summary.simulated_duration_sec, 2.0)
        self.assertEqual(self.harness.generated_orders[0]["action"], "SELL")

    def test_sorted_tick_replay(self):
        # Unsorted ticks input
        unsorted = [self.ticks[2], self.ticks[0], self.ticks[1]]
        received_seqs = []

        def callback(tick):
            received_seqs.append(tick.sequence_id)
            return None

        self.harness.replay_session(unsorted, callback, asap_mode=True)

        # Should replay strictly in timestamp sorted order (seq 1, 2, 3)
        self.assertEqual(received_seqs, [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
