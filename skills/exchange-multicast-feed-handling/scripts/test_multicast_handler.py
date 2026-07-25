"""
Unit tests for exchange-multicast-feed-handling skill.
"""
import unittest
from multicast_handler import ExchangeMulticastFeedHandler, MulticastChannel, MulticastPacket


class TestExchangeMulticastFeedHandler(unittest.TestCase):

    def setUp(self):
        self.handler = ExchangeMulticastFeedHandler(initial_sequence=100)

    def test_dual_ab_channel_arbitration(self):
        # Seq 100 on Channel A -> Processed
        res_a = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"TICK_100")
        self.assertEqual(len(res_a.processed_packets), 1)
        self.assertFalse(res_a.is_gap_detected)

        # Seq 100 on Channel B -> Duplicate ignored
        res_b = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 100, b"TICK_100")
        self.assertEqual(len(res_b.processed_packets), 0)

        # Seq 101 on Channel B -> Seamless failover processing
        res_c = self.handler.ingest_packet(MulticastChannel.CHANNEL_B, 101, b"TICK_101")
        self.assertEqual(len(res_c.processed_packets), 1)

    def test_tcp_retransmission_gap_recovery(self):
        # Ingest seq 100
        self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 100, b"TICK_100")

        # Multicast gap: Seq 103 arrives on Channel A (missing 101, 102)
        res_gap = self.handler.ingest_packet(MulticastChannel.CHANNEL_A, 103, b"TICK_103")
        self.assertTrue(res_gap.is_gap_detected)
        self.assertEqual(res_gap.missing_range, (101, 102))

        # Reconcile via TCP retransmission
        tcp_pkts = [
            MulticastPacket(MulticastChannel.TCP_RETRANSMISSION, 101, b"TICK_101", 0.0),
            MulticastPacket(MulticastChannel.TCP_RETRANSMISSION, 102, b"TICK_102", 0.0),
        ]
        reconciled = self.handler.reconcile_tcp_retransmission(tcp_pkts)

        # Should recover 101, 102, and automatically drain 103 -> 3 packets total
        self.assertEqual(len(reconciled), 3)
        self.assertEqual(self.handler.expected_sequence, 104)


if __name__ == "__main__":
    unittest.main()
