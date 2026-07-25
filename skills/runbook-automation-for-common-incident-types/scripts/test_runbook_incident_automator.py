import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unittest
from runbook_incident_automator import RunbookIncidentAutomator, Config

class TestRunbookIncidentAutomator(unittest.TestCase):
    def test_init(self):
        obj = RunbookIncidentAutomator(Config("test"))
        self.assertEqual(obj.config.name, "test")

    def test_process(self):
        obj = RunbookIncidentAutomator()
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
