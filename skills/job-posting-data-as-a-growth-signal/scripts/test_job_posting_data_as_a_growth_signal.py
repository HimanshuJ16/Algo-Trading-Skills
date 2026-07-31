import unittest
from job_posting_data_as_a_growth_signal import (
    JobPostingSignalEngine, CompanyJobPostingSnapshot, JobPostingSignalReport
)

class TestJobPostingSignalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = JobPostingSignalEngine(ghost_job_stale_days_threshold=120.0)

    def test_tech_expansion_bullish_signal(self):
        # NVIDIA: 300 active postings (+100% vs 150 prev), 50% Engineering, Avg duration 35 days -> EXPANSION_BULLISH!
        snapshot = CompanyJobPostingSnapshot(
            ticker="NVDA", company_name="NVIDIA Corp", current_active_postings_count=300,
            previous_active_postings_count=150, engineering_postings_pct=0.50,
            sales_postings_pct=0.30, avg_posting_duration_days=35.0
        )
        report = self.engine.calculate_growth_score(snapshot)

        self.assertEqual(report.signal_classification, "EXPANSION_BULLISH")
        self.assertEqual(report.qoq_postings_growth_pct, 100.0)
        self.assertFalse(report.has_ghost_postings_penalty)
        self.assertGreaterEqual(report.corporate_growth_score, 0.80)

    def test_stale_ghost_job_penalty_and_contraction_signal(self):
        # Layoff firm: 50 active postings (-50% vs 100 prev), Avg duration 150 days (> 120) -> CONTRACTION_BEARISH!
        snapshot = CompanyJobPostingSnapshot(
            ticker="STALE_CO", company_name="Stale Retailer", current_active_postings_count=50,
            previous_active_postings_count=100, engineering_postings_pct=0.10,
            sales_postings_pct=0.10, avg_posting_duration_days=150.0
        )
        report = self.engine.calculate_growth_score(snapshot)

        self.assertEqual(report.signal_classification, "CONTRACTION_BEARISH")
        self.assertEqual(report.qoq_postings_growth_pct, -50.0)
        self.assertTrue(report.has_ghost_postings_penalty)

if __name__ == '__main__':
    unittest.main()
