import unittest
from factor_research_multiple_testing_correction import (
    FactorMultipleTestingCorrectionEngine, CandidateFactorTest, FactorMultipleTestingAuditReport
)

class TestFactorMultipleTestingCorrectionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FactorMultipleTestingCorrectionEngine(alpha_target=0.05, fdr_q_target=0.05, hlz_t_threshold=3.0)

    def test_multiple_testing_filters_false_discoveries(self):
        # 10 tested factors: 2 true alphas (t=3.8, t=3.2), 3 marginal noise factors (t=2.1, t=2.0, t=1.98), 5 clear noise factors (t=0.5)
        factors = [
            CandidateFactorTest("F01", "True_Alpha_1", raw_t_stat=3.80, raw_p_value=0.0001, sample_size=1000),
            CandidateFactorTest("F02", "True_Alpha_2", raw_t_stat=3.20, raw_p_value=0.0014, sample_size=1000),
            CandidateFactorTest("F03", "Noise_Marginal_1", raw_t_stat=2.10, raw_p_value=0.0350, sample_size=1000),
            CandidateFactorTest("F04", "Noise_Marginal_2", raw_t_stat=2.00, raw_p_value=0.0450, sample_size=1000),
            CandidateFactorTest("F05", "Noise_Marginal_3", raw_t_stat=1.98, raw_p_value=0.0480, sample_size=1000),
            CandidateFactorTest("F06", "Noise_1", raw_t_stat=0.50, raw_p_value=0.6170, sample_size=1000),
            CandidateFactorTest("F07", "Noise_2", raw_t_stat=0.40, raw_p_value=0.6890, sample_size=1000),
            CandidateFactorTest("F08", "Noise_3", raw_t_stat=0.30, raw_p_value=0.7640, sample_size=1000),
            CandidateFactorTest("F09", "Noise_4", raw_t_stat=0.20, raw_p_value=0.8410, sample_size=1000),
            CandidateFactorTest("F10", "Noise_5", raw_t_stat=0.10, raw_p_value=0.9200, sample_size=1000),
        ]

        report = self.engine.audit_and_correct_factors(factors)

        self.assertEqual(report.total_factors_tested, 10)
        self.assertEqual(report.raw_significant_count, 5) # Unadjusted p <= 0.05 accepts 5 factors
        self.assertEqual(report.hlz_t3_significant_count, 2) # Harvey-Liu-Zhu t>=3.0 accepts 2 factors
        self.assertEqual(report.bh_fdr_significant_count, 2) # BH FDR filters out false discoveries
        self.assertEqual(report.false_discoveries_filtered_count, 3)

if __name__ == '__main__':
    unittest.main()
