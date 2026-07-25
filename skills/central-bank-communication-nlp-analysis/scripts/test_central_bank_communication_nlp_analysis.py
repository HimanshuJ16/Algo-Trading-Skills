import unittest
from central_bank_communication_nlp_analysis import CentralBankCommunicationNlpAnalysis, CentralBankCommunicationNlpAnalysisConfig

class TestCentralBankCommunicationNlpAnalysis(unittest.TestCase):
    def test_init(self):
        config = CentralBankCommunicationNlpAnalysisConfig()
        obj = CentralBankCommunicationNlpAnalysis(config)
        self.assertIsNotNone(obj)

    def test_process(self):
        config = CentralBankCommunicationNlpAnalysisConfig()
        obj = CentralBankCommunicationNlpAnalysis(config)
        self.assertTrue(obj.process())

if __name__ == '__main__':
    unittest.main()
