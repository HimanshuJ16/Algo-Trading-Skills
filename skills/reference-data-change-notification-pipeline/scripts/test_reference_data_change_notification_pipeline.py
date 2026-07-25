
import unittest
from reference_data_change_notification_pipeline import ReferenceDataChangeNotificationPipelineEngine, ReferenceDataChangeNotificationPipelineConfig

class TestReferenceDataChangeNotificationPipeline(unittest.TestCase):
    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine(ReferenceDataChangeNotificationPipelineConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(ReferenceDataChangeNotificationPipelineConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
