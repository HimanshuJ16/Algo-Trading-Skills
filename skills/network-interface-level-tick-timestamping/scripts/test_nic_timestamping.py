"""
Unit tests for network-interface-level-tick-timestamping skill.
"""
import socket
import struct
import unittest
from nic_timestamping import NICHardwareTimestamperEngine, TimestampSource


class TestNICHardwareTimestamperEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NICHardwareTimestamperEngine()

    def test_ancillary_hardware_timestamp_decoding(self):
        # Build mock timespec: sec=1784948000, nsec=500000 (16 bytes: 'qq')
        cmsg_data = struct.pack("qq", 1784948000, 500000)
        ancillary = [(socket.SOL_SOCKET, 35, cmsg_data)]  # 35 = SO_TIMESTAMPNS

        app_time = 1784948000.001  # App time is 1ms (1000us) later

        pkt = self.engine.process_packet_with_nic_timestamp(b"TICK_DATA", ancillary, app_time)

        self.assertEqual(pkt.source, TimestampSource.HARDWARE_NIC)
        self.assertEqual(pkt.timestamp_ns, (1784948000 * 1000000000) + 500000)
        self.assertAlmostEqual(pkt.kernel_queueing_jitter_us, 500.0, delta=10.0)

    def test_application_fallback_when_ancillary_empty(self):
        ancillary = []
        app_time = 1784948000.0

        pkt = self.engine.process_packet_with_nic_timestamp(b"TICK_DATA", ancillary, app_time)

        self.assertEqual(pkt.source, TimestampSource.APPLICATION_FALLBACK)
        self.assertEqual(pkt.timestamp_ns, 1784948000000000000)
        self.assertEqual(pkt.kernel_queueing_jitter_us, 0.0)


if __name__ == "__main__":
    unittest.main()
