import struct
import unittest
from nyse_arca_integrated_feed_handling import (
    NYSEArcaIntegratedFeedEngine, NYSEParsedMessage, NYSEFeedReport
)

class TestNYSEArcaIntegratedFeedEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NYSEArcaIntegratedFeedEngine({101: "SPY", 102: "AAPL"})

    def test_xdp_add_order_and_execution(self):
        # Pack 16-byte XDP Packet Header: PktSize=57, Deliv=0, NumMsgs=1, Seq=1001, Time=1700000000, TimeNS=500000
        pkt_hdr = struct.pack("<HBBIII", 57, 0, 1, 1001, 1700000000, 500000)

        # Pack Add Order Msg (100): MsgSize=41 (4 hdr + 37 payload), MsgType=100
        # Payload 100: SrcT=1700000000, SrcTNS=500000, SymIdx=101 (SPY), SymSeq=1, OrderID=88001, Price=4500000 ($450.00), Vol=500, Side=b'B', Firm=b'ARCA'
        msg_hdr = struct.pack("<HH", 41, 100)
        payload_100 = struct.pack("<IIIIQIIs4s", 1700000000, 500000, 101, 1, 88001, 4500000, 500, b"B", b"ARCA")

        pkt_bytes = pkt_hdr + msg_hdr + payload_100

        msgs = self.engine.parse_xdp_packet(pkt_bytes)
        self.assertEqual(len(msgs), 1)
        m_add = msgs[0]
        self.assertEqual(m_add.msg_type, 100)
        self.assertEqual(m_add.order_id, 88001)
        self.assertEqual(m_add.symbol, "SPY")
        self.assertEqual(m_add.price_usd, 450.00)
        self.assertEqual(m_add.volume, 500)
        self.assertEqual(len(self.engine.active_orders), 1)

        # Pack Execution Msg (103): MsgSize=37 (4 + 33 payload), MsgType=103 for OrderID 88001, ExecVol=200
        pkt_hdr_exec = struct.pack("<HBBIII", 53, 0, 1, 1002, 1700000000, 600000)
        msg_hdr_exec = struct.pack("<HH", 37, 103)
        payload_103 = struct.pack("<IIIIQII1s", 1700000000, 600000, 101, 2, 88001, 4500000, 200, b"Y")

        msgs_exec = self.engine.parse_xdp_packet(pkt_hdr_exec + msg_hdr_exec + payload_103)
        self.assertEqual(msgs_exec[0].msg_type, 103)
        # Order 88001 should now have 300 remaining volume
        self.assertEqual(self.engine.active_orders[88001].volume, 300)

    def test_xdp_delete_order(self):
        # Add order 99002 then Delete (102)
        pkt_hdr = struct.pack("<HBBIII", 57, 0, 1, 1003, 1700000000, 500000)
        msg_hdr = struct.pack("<HH", 41, 100)
        payload_100 = struct.pack("<IIIIQIIs4s", 1700000000, 500000, 102, 1, 99002, 1800000, 100, b"S", b"ARCA")
        self.engine.parse_xdp_packet(pkt_hdr + msg_hdr + payload_100)
        self.assertEqual(len(self.engine.active_orders), 1)

        # Delete order 99002
        pkt_hdr_del = struct.pack("<HBBIII", 44, 0, 1, 1004, 1700000000, 600000)
        msg_hdr_del = struct.pack("<HH", 28, 102)
        payload_102 = struct.pack("<IIIIQ", 1700000000, 600000, 102, 2, 99002)

        msgs_del = self.engine.parse_xdp_packet(pkt_hdr_del + msg_hdr_del + payload_102)
        self.assertEqual(msgs_del[0].msg_type, 102)
        self.assertEqual(len(self.engine.active_orders), 0)

if __name__ == '__main__':
    unittest.main()
