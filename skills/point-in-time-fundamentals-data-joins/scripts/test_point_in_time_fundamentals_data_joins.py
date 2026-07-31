import unittest
from point_in_time_fundamentals_data_joins import (
    Config, Engine, PointInTimeFundamentalsDataJoinsEngine,
    FundamentalFilingRecord, PITQuery, PITFundamentalsReport
)

class TestPointInTimeFundamentalsDataJoins(unittest.TestCase):

    def setUp(self):
        self.config = Config("test_engine")
        self.legacy_engine = Engine(self.config)
        self.pit_engine = PointInTimeFundamentalsDataJoinsEngine(self.config)

    def test_legacy_init_and_process(self):
        self.assertEqual(self.legacy_engine.config.name, "test_engine")
        self.assertEqual(self.legacy_engine.process(100), 100)

    def test_pit_as_of_join_blocks_future_filing(self):
        # Q4 earnings event on 2022-12-31, but filed on 2023-02-15
        self.pit_engine.insert_filings([
            FundamentalFilingRecord("AAPL", "eps", 1.50, period_end_date="2022-12-31", filing_date="2023-02-15")
        ])

        # Query on Jan 15 (Before filing date) -> Should be NO_DATA
        query_jan = PITQuery("AAPL", "eps", as_of_date="2023-01-15")
        report_jan = self.pit_engine.execute_pit_join(query_jan)
        self.assertEqual(report_jan.status, "NO_DATA_AVAILABLE_AS_OF_DATE")
        self.assertIsNone(report_jan.matched_record_value)
        self.assertTrue(report_jan.restatement_leakage_blocked)

        # Query on March 01 (After filing date) -> Should return $1.50
        query_mar = PITQuery("AAPL", "eps", as_of_date="2023-03-01")
        report_mar = self.pit_engine.execute_pit_join(query_mar)
        self.assertEqual(report_mar.status, "RECORD_FOUND_PIT_VALID")
        self.assertEqual(report_mar.matched_record_value, 1.50)

    def test_pit_restatement_isolation(self):
        # Original filing $1.50 on Feb 15; Restatement $1.20 on Aug 10
        self.pit_engine.insert_filings([
            FundamentalFilingRecord("AAPL", "eps", 1.50, period_end_date="2022-12-31", filing_date="2023-02-15", revision_number=0),
            FundamentalFilingRecord("AAPL", "eps", 1.20, period_end_date="2022-12-31", filing_date="2023-08-10", revision_number=1),
        ])

        # Query on May 01 (Between original & restatement) -> Must return original $1.50
        report_may = self.pit_engine.execute_pit_join(PITQuery("AAPL", "eps", as_of_date="2023-05-01"))
        self.assertEqual(report_may.matched_record_value, 1.50)
        self.assertEqual(report_may.revision_number, 0)

        # Query on Sept 01 (After restatement) -> Must return restated $1.20
        report_sept = self.pit_engine.execute_pit_join(PITQuery("AAPL", "eps", as_of_date="2023-09-01"))
        self.assertEqual(report_sept.matched_record_value, 1.20)
        self.assertEqual(report_sept.revision_number, 1)

if __name__ == '__main__':
    unittest.main()
