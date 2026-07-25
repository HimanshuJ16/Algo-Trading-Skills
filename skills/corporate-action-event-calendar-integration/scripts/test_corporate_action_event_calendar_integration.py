
import unittest
from corporate_action_event_calendar_integration import CorporateActionEventCalendarIntegrationEngine, CorporateActionEventCalendarIntegrationConfig

class TestCorporateActionEventCalendarIntegration(unittest.TestCase):
    def setUp(self):
        self.engine = CorporateActionEventCalendarIntegrationEngine(CorporateActionEventCalendarIntegrationConfig())
        
    def test_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")
        
    def test_disabled(self):
        engine = CorporateActionEventCalendarIntegrationEngine(CorporateActionEventCalendarIntegrationConfig(enabled=False))
        self.assertEqual(engine.process("test"), "")

if __name__ == '__main__':
    unittest.main()
