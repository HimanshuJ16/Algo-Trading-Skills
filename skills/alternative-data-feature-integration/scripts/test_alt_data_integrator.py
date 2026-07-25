import unittest
from datetime import datetime, timedelta
from alt_data_integrator import AltDataIntegrator, RawAltDataEvent

class TestAltDataIntegrator(unittest.TestCase):
    def setUp(self):
        self.integrator = AltDataIntegrator()
        
    def test_pit_publication_lag_mapping(self):
        # Event occurs on Monday at 12:00, but takes 48 hours to process and publish.
        event_time = datetime(2026, 1, 5, 12, 0)
        lag = timedelta(hours=48)
        
        event = RawAltDataEvent(
            source_id="SAT_IMG_01",
            event_timestamp=event_time,
            publication_lag=lag,
            feature_value=150.5
        )
        
        self.integrator.ingest_events([event])
        
        # Knowledge timestamp should strictly be Wednesday at 12:00
        expected_knowledge_time = datetime(2026, 1, 7, 12, 0)
        self.assertEqual(self.integrator._pit_features[0].knowledge_timestamp, expected_knowledge_time)

    def test_safe_trading_schedule_alignment(self):
        # Event 1: Known on Tuesday 10:00 AM
        e1 = RawAltDataEvent("A", datetime(2026, 1, 6, 8, 0), timedelta(hours=2), 10.0) 
        # Event 2: Known on Thursday 10:00 AM
        e2 = RawAltDataEvent("A", datetime(2026, 1, 8, 8, 0), timedelta(hours=2), 20.0)
        
        self.integrator.ingest_events([e1, e2])
        
        # Trading Schedule: Mon, Tue, Wed, Thu at 4:00 PM (16:00)
        trading_times = [
            datetime(2026, 1, 5, 16, 0), # Monday (Before e1 knowledge)
            datetime(2026, 1, 6, 16, 0), # Tuesday (After e1, before e2)
            datetime(2026, 1, 7, 16, 0), # Wednesday (After e1, before e2) -> Should forward fill e1
            datetime(2026, 1, 8, 16, 0)  # Thursday (After e2)
        ]
        
        aligned = self.integrator.align_to_trading_schedule(trading_times)
        
        # Monday: Unknown (None)
        self.assertIsNone(aligned[trading_times[0]])
        # Tuesday: 10.0
        self.assertEqual(aligned[trading_times[1]], 10.0)
        # Wednesday: 10.0 (Safely forward filled from Tuesday)
        self.assertEqual(aligned[trading_times[2]], 10.0)
        # Thursday: 20.0 (New data arrived)
        self.assertEqual(aligned[trading_times[3]], 20.0)

if __name__ == "__main__":
    unittest.main()
