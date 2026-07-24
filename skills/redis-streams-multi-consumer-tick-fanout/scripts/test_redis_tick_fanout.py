"""
Unit tests for redis-streams-multi-consumer-tick-fanout skill.

Tests:
1. Publishing ticks to Redis Streams via XADD.
2. Independent consumer group fanout consumption.
3. Message acknowledgment via XACK.
4. Stale pending message claims via XCLAIM for crashed workers.
"""
import time
import unittest
from redis_tick_fanout import RedisTickFanoutManager, TickData


class TestRedisTickFanoutManager(unittest.TestCase):

    def setUp(self):
        self.mgr = RedisTickFanoutManager(stream_name="test_market_ticks")
        self.mgr.create_consumer_group("grp_strategy")
        self.mgr.create_consumer_group("grp_risk")

    def test_publish_and_fanout_consumption(self):
        t1 = TickData(symbol="AAPL", last_price=150.25, volume=100)
        msg_id = self.mgr.publish_tick(t1)
        self.assertTrue(len(msg_id) > 0)

        # Both consumer groups consume the same tick independently
        strat_ticks = self.mgr.consume_ticks("grp_strategy", "worker_strat_1")
        risk_ticks = self.mgr.consume_ticks("grp_risk", "worker_risk_1")

        self.assertEqual(len(strat_ticks), 1)
        self.assertEqual(len(risk_ticks), 1)
        self.assertEqual(strat_ticks[0][1].symbol, "AAPL")
        self.assertEqual(risk_ticks[0][1].symbol, "AAPL")

    def test_acknowledge_tick(self):
        t1 = TickData(symbol="TSLA", last_price=200.0, volume=50)
        msg_id = self.mgr.publish_tick(t1)

        ticks = self.mgr.consume_ticks("grp_strategy", "worker_strat_1")
        self.assertEqual(len(ticks), 1)

        # XACK removes from PEL
        acked = self.mgr.acknowledge_tick("grp_strategy", msg_id)
        self.assertEqual(acked, 1)

    def test_claim_stale_ticks(self):
        t1 = TickData(symbol="NVDA", last_price=500.0, volume=20)
        msg_id = self.mgr.publish_tick(t1)

        # Worker 1 consumes tick but crashes before XACK
        ticks = self.mgr.consume_ticks("grp_strategy", "worker_crashed")
        self.assertEqual(len(ticks), 1)

        # Simulate time passing for idle threshold
        time.sleep(0.05)

        # Worker 2 claims stale tick (min_idle_ms = 10ms for fast test)
        claimed = self.mgr.claim_stale_ticks("grp_strategy", "worker_active_2", min_idle_ms=10.0, msg_ids=[msg_id])
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0][1].symbol, "NVDA")


if __name__ == "__main__":
    unittest.main()
