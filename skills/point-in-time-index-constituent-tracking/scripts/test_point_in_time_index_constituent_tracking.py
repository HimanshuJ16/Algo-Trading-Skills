
import unittest
from point_in_time_index_constituent_tracking import PointInTimeIndexConstituentTrackingEngine, PointInTimeIndexConstituentTrackingConfig

class TestPointInTimeIndexConstituentTracking(unittest.TestCase):
    def setUp(self):
        self.engine = PointInTimeIndexConstituentTrackingEngine(PointInTimeIndexConstituentTrackingConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = PointInTimeIndexConstituentTrackingEngine(PointInTimeIndexConstituentTrackingConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
