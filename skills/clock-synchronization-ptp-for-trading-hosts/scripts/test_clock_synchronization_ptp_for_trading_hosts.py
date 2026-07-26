import unittest
from clock_synchronization_ptp_for_trading_hosts import PtpClockSyncManager

class TestPtpClockSyncManager(unittest.TestCase):

    def setUp(self):
        self.manager = PtpClockSyncManager(max_allowed_offset_ns=100000.0, target_hft_offset_ns=1000.0)

    def test_parse_ptp4l_line(self):
        line = "ptp4l[12345.678]: master offset   -42 s2 freq   +1234 path delay   1500"
        sample = self.manager.parse_log_line(line)
        
        self.assertIsNotNone(sample)
        self.assertEqual(sample.daemon, "ptp4l")
        self.assertEqual(sample.offset_ns, -42.0)
        self.assertEqual(sample.ptp_state, "s2")
        self.assertEqual(sample.path_delay_ns, 1500.0)
        self.assertEqual(self.manager.ptp4l_state, "s2")

    def test_parse_phc2sys_line(self):
        line = "phc2sys[12345.678]: CLOCK_REALTIME phc offset   -15 s2 freq   +567"
        sample = self.manager.parse_log_line(line)
        
        self.assertIsNotNone(sample)
        self.assertEqual(sample.daemon, "phc2sys")
        self.assertEqual(sample.offset_ns, -15.0)
        self.assertEqual(sample.ptp_state, "s2")
        self.assertEqual(self.manager.phc2sys_state, "s2")

    def test_compliance_evaluation_pass(self):
        self.manager.parse_log_line("ptp4l[100.0]: master offset -150 s2 freq +10 path delay 500")
        self.manager.parse_log_line("phc2sys[100.0]: CLOCK_REALTIME phc offset -80 s2 freq +5")
        
        comp = self.manager.evaluate_compliance()
        self.assertTrue(comp["is_synced"])
        self.assertTrue(comp["mifid_compliant"])
        self.assertTrue(comp["hft_ready"])
        self.assertEqual(comp["max_offset_ns"], 150.0)

    def test_compliance_evaluation_fail_high_offset(self):
        self.manager.parse_log_line("ptp4l[100.0]: master offset -150000 s2 freq +10 path delay 500")
        self.manager.parse_log_line("phc2sys[100.0]: CLOCK_REALTIME phc offset -80 s2 freq +5")
        
        comp = self.manager.evaluate_compliance()
        self.assertTrue(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])
        self.assertFalse(comp["hft_ready"])

    def test_compliance_evaluation_fail_unlocked_state(self):
        self.manager.parse_log_line("ptp4l[100.0]: master offset -10 s0 freq +10 path delay 500")
        self.manager.parse_log_line("phc2sys[100.0]: CLOCK_REALTIME phc offset -5 s2 freq +5")
        
        comp = self.manager.evaluate_compliance()
        self.assertFalse(comp["is_synced"])
        self.assertFalse(comp["mifid_compliant"])

if __name__ == '__main__':
    unittest.main()
