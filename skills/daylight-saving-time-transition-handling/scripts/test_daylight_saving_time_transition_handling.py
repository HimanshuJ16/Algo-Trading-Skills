import unittest
from daylight_saving_time_transition_handling import (
    DstTransitionHandlerEngine, ExchangeScheduleSpec, UtcSessionWindow, DstTransitionAuditReport
)

class TestDstTransitionHandlerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DstTransitionHandlerEngine()
        
        # NYSE: New York (09:30 - 16:00)
        self.engine.register_exchange(ExchangeScheduleSpec("NYSE", "New York Stock Exchange", "America/New_York", "09:30", "16:00"))
        # LSE: London (08:00 - 16:30)
        self.engine.register_exchange(ExchangeScheduleSpec("LSE", "London Stock Exchange", "Europe/London", "08:00", "16:30"))

    def test_march_us_eu_dst_desync_window(self):
        # March 15, 2026: US is in EDT (DST active!), but EU is in GMT (DST NOT active until last Sunday of March)
        # 2-week desynchronization window!
        report = self.engine.audit_global_dst_transitions("2026-03-15")
        
        self.assertTrue(report.is_us_eu_desync_window)
        self.assertGreater(len(report.warnings), 0)
        
        nyse_sess = next(s for s in report.sessions if s.exchange_id == "NYSE")
        lse_sess = next(s for s in report.sessions if s.exchange_id == "LSE")
        
        # NYSE Open in EDT (UTC-4) at 09:30 local -> 13:30Z
        self.assertEqual(nyse_sess.utc_open_iso, "2026-03-15T13:30:00Z")
        self.assertTrue(nyse_sess.is_dst_active)
        # LSE Open in GMT (UTC+0) at 08:00 local -> 08:00Z
        self.assertEqual(lse_sess.utc_open_iso, "2026-03-15T08:00:00Z")
        self.assertFalse(lse_sess.is_dst_active)

    def test_april_aligned_dst_session(self):
        # April 15, 2026: Both US (EDT -4) and EU (BST +1) are in DST!
        report = self.engine.audit_global_dst_transitions("2026-04-15")
        
        self.assertFalse(report.is_us_eu_desync_window)
        nyse_sess = next(s for s in report.sessions if s.exchange_id == "NYSE")
        lse_sess = next(s for s in report.sessions if s.exchange_id == "LSE")
        
        self.assertTrue(nyse_sess.is_dst_active)
        self.assertTrue(lse_sess.is_dst_active)

if __name__ == '__main__':
    unittest.main()
