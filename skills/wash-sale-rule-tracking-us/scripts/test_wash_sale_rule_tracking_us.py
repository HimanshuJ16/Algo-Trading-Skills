import unittest
from wash_sale_rule_tracking_us import WashSaleRuleTrackingUsEngine, Record

class TestWashSaleRuleTrackingUsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = WashSaleRuleTrackingUsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = WashSaleRuleTrackingUsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = WashSaleRuleTrackingUsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
