import unittest
from model_family_selector import (
    ModelFamilySelectorEngine, DatasetSpec, ModelFamilyTradeoffReport
)

class TestModelFamilySelectorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelFamilySelectorEngine()

    def test_tabular_data_with_strict_governance_recommends_gbdt(self):
        # Tabular data, 200 us latency, strict SR 11-7 compliance -> RECOMMEND LIGHTGBM/XGBOOST!
        spec = DatasetSpec(
            modality="TABULAR_ENGINEERED", sample_size_rows=100_000, feature_count=50,
            latency_budget_us=200.0, regulatory_compliance="STRICT_SR11_7_MIFID2"
        )
        report = self.engine.evaluate_model_family_tradeoffs(spec)

        self.assertEqual(report.recommended_model_family, "RECOMMEND_LIGHTGBM_XGBOOST")
        self.assertGreater(report.gbdt_overall_score_0_to_10, report.neural_net_overall_score_0_to_10)

    def test_raw_tick_sequence_recommends_neural_network(self):
        # Raw tick sequence, 20 ms latency, internal research -> RECOMMEND NEURAL NETWORK!
        spec = DatasetSpec(
            modality="RAW_HIGH_FREQUENCY_TICKS", sample_size_rows=5_000_000, feature_count=10,
            latency_budget_us=20_000.0, regulatory_compliance="INTERNAL_RESEARCH"
        )
        report = self.engine.evaluate_model_family_tradeoffs(spec)

        self.assertEqual(report.recommended_model_family, "RECOMMEND_NEURAL_NETWORK_LSTM_TRANSFORMER")
        self.assertGreater(report.neural_net_overall_score_0_to_10, report.gbdt_overall_score_0_to_10)

if __name__ == '__main__':
    unittest.main()
