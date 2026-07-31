import unittest
import time
from training_freshness_sla import (
    TrainingFreshnessSlaEngine, FreshnessSlaConfig, DatasetMetadataPayload, FreshnessSlaReport
)

class TestTrainingFreshnessSlaEngine(unittest.TestCase):

    def setUp(self):
        self.engine = TrainingFreshnessSlaEngine()
        self.now = 1750000000.0 # Epoch timestamp anchor

    def test_sla_compliant_fresh_dataset(self):
        # Latest record 5 hours ago <= 24h target SLA -> SLA_COMPLIANT!
        cfg = FreshnessSlaConfig("ALPHA_MODEL_01", "EQUITY_DAILY_BARS", target_sla_hours=24.0)
        meta = DatasetMetadataPayload("EQUITY_DAILY_BARS", latest_record_timestamp_epoch=self.now - 18000.0, current_system_timestamp_epoch=self.now, total_record_count=10000)

        report = self.engine.evaluate_training_freshness_sla(cfg, meta)

        self.assertTrue(report.is_sla_compliant)
        self.assertEqual(report.status, "SLA_COMPLIANT")
        self.assertEqual(report.recommended_governance_action, "PROCEED_NORMAL")
        self.assertEqual(report.data_lag_hours, 5.0)

    def test_sla_critical_breach_halts_retraining(self):
        # Latest record 52 hours ago > 48h breach limit -> SLA_BREACH_CRITICAL!
        cfg = FreshnessSlaConfig("ALPHA_MODEL_01", "EQUITY_DAILY_BARS", breach_sla_hours=48.0, action_on_breach="HALT_MODEL_RETRAINING")
        meta = DatasetMetadataPayload("EQUITY_DAILY_BARS", latest_record_timestamp_epoch=self.now - 187200.0, current_system_timestamp_epoch=self.now, total_record_count=10000)

        report = self.engine.evaluate_training_freshness_sla(cfg, meta)

        self.assertFalse(report.is_sla_compliant)
        self.assertEqual(report.status, "SLA_BREACH_CRITICAL")
        self.assertEqual(report.recommended_governance_action, "HALT_MODEL_RETRAINING")
        self.assertEqual(report.data_lag_hours, 52.0)

if __name__ == '__main__':
    unittest.main()
