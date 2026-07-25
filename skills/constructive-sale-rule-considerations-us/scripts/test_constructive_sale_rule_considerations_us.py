import unittest
from constructive_sale_rule_considerations_us import ConstructiveSaleRuleConsiderationsUsEngine, Record

class TestConstructiveSaleRuleConsiderationsUsEngine(unittest.TestCase):
    def test_initialization(self):
        engine = ConstructiveSaleRuleConsiderationsUsEngine()
        self.assertEqual(len(engine.records), 0)

    def test_add_record(self):
        engine = ConstructiveSaleRuleConsiderationsUsEngine()
        engine.add_record(Record("1", 10.0))
        self.assertEqual(len(engine.records), 1)

    def test_process(self):
        engine = ConstructiveSaleRuleConsiderationsUsEngine()
        engine.add_record(Record("1", 10.0))
        engine.add_record(Record("2", 20.0))
        self.assertEqual(engine.process(), 30.0)

if __name__ == '__main__':
    unittest.main()
