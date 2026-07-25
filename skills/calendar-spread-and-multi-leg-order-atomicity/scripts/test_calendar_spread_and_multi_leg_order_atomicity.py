import unittest
from calendar_spread_and_multi_leg_order_atomicity import Configuration, CalendarSpreadAndMultiLegOrderAtomicityEngine

class TestCalendarSpreadAndMultiLegOrderAtomicity(unittest.TestCase):
    def test_default_execution(self):
        engine = CalendarSpreadAndMultiLegOrderAtomicityEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution(self):
        engine = CalendarSpreadAndMultiLegOrderAtomicityEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")
        
    def test_empty_data(self):
        engine = CalendarSpreadAndMultiLegOrderAtomicityEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

if __name__ == '__main__':
    unittest.main()
