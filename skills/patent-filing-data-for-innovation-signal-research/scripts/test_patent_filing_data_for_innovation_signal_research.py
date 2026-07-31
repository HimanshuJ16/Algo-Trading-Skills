import unittest
import pandas as pd
from patent_filing_data_for_innovation_signal_research import (
    PatentFilingDataForInnovationSignalResearchEngine,
    SignalResult,
    PatentFilingRecord,
    PatentInnovationReport
)

class TestPatentFilingDataForInnovationSignalResearch(unittest.TestCase):

    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine()

    def test_empty_data_legacy(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data_legacy(self):
        df = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

    def test_compute_patent_innovation_signals(self):
        records = [
            PatentFilingRecord("AAPL", "2023-01-01", "2024-01-01", "US1001", forward_citations=50),
            PatentFilingRecord("AAPL", "2023-02-01", "2024-02-01", "US1002", forward_citations=30),
            PatentFilingRecord("MSFT", "2023-01-15", "2024-01-15", "US2001", forward_citations=5)
        ]
        report = self.engine.compute_patent_innovation_signals(records)

        self.assertEqual(report.status, "SIGNALS_GENERATED")
        self.assertEqual(report.total_assets, 2)
        self.assertEqual(report.top_innovator, "AAPL")
        self.assertGreater(report.z_scores["AAPL"], 0.0)
        self.assertLess(report.z_scores["MSFT"], 0.0)

if __name__ == '__main__':
    unittest.main()
