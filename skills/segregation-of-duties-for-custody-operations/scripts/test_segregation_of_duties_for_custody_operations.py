import unittest
from segregation_of_duties_for_custody_operations import SegregationOfDutiesForCustodyOperationsConfig, SegregationOfDutiesForCustodyOperationsEngine

class TestSegregationOfDutiesForCustodyOperations(unittest.TestCase):
    def test_execute_true(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(SegregationOfDutiesForCustodyOperationsConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = SegregationOfDutiesForCustodyOperationsEngine(SegregationOfDutiesForCustodyOperationsConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
