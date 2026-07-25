import unittest
from factor_research_multiple_testing_correction import FactorResearchMultipleTestingCorrection, FactorResearchMultipleTestingCorrectionConfig

class TestFactorResearchMultipleTestingCorrection(unittest.TestCase):
    def test_init(self):
        config = FactorResearchMultipleTestingCorrectionConfig()
        obj = FactorResearchMultipleTestingCorrection(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = FactorResearchMultipleTestingCorrectionConfig()
        obj = FactorResearchMultipleTestingCorrection(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
