import unittest
import pandas as pd
from patent_filing_data_for_innovation_signal_research import PatentFilingDataForInnovationSignalResearchEngine, SignalResult

class TestPatentFilingDataForInnovationSignalResearch(unittest.TestCase):
    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine()

    def test_empty_data(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data(self):
        df = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

if __name__ == '__main__':
    unittest.main()
