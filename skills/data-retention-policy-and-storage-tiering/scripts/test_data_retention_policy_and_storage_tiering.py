import unittest
from data_retention_policy_and_storage_tiering import (
    DataRetentionPolicyEngine, MarketDatasetInfo, DataRetentionAuditReport
)

class TestDataRetentionPolicyEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataRetentionPolicyEngine()

    def test_hot_to_warm_parquet_transition_saves_cost(self):
        # 100,000 GB (100 TB) tick dataset, age = 120 days, currently in HOT_NVME ($0.20/GB = $20,000/mo)
        # Should transition to WARM_PARQUET_S3 ($0.023/GB = $2,300/mo) -> Save $17,700/mo!
        dataset = MarketDatasetInfo(
            dataset_id="L2_TICK_2025_Q1",
            data_type="L2_ORDER_BOOK",
            size_gb=100_000.0,
            age_days=120,
            current_tier="HOT_NVME",
            regulatory_retention_years=6.0
        )
        rec = self.engine.evaluate_dataset_lifecycle(dataset)
        
        self.assertEqual(rec.recommended_tier, "WARM_PARQUET_S3")
        self.assertEqual(rec.current_monthly_cost_usd, 20_000.0)
        self.assertEqual(rec.recommended_monthly_cost_usd, 2_300.0)
        self.assertEqual(rec.monthly_cost_savings_usd, 17_700.0)

    def test_old_data_transition_to_cold_glacier(self):
        # 50,000 GB dataset, age = 500 days (1.37 years)
        # Should transition to COLD_GLACIER_INSTANT ($0.004/GB = $200/mo)
        dataset = MarketDatasetInfo(
            dataset_id="HISTORICAL_TICKS_2024",
            data_type="L3_TICK",
            size_gb=50_000.0,
            age_days=500,
            current_tier="WARM_PARQUET_S3",
            regulatory_retention_years=6.0
        )
        rec = self.engine.evaluate_dataset_lifecycle(dataset)
        
        self.assertEqual(rec.recommended_tier, "COLD_GLACIER_INSTANT")
        self.assertEqual(rec.recommended_monthly_cost_usd, 200.0)

if __name__ == '__main__':
    unittest.main()
