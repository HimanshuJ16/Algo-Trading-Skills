import unittest
import time
from model_version_manager import (
    ModelVersionManagerEngine, ModelVersion, RollbackTriggerConfig, LivePerformanceTelemetry, ModelVersionReport
)

class TestModelVersionManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelVersionManagerEngine()

        self.hash_v1 = ModelVersionManagerEngine.compute_sha256(b"v1_model_bytes_payload")
        self.hash_v2 = ModelVersionManagerEngine.compute_sha256(b"v2_model_bytes_payload")

        self.ver_1 = ModelVersion(
            model_id="ML_ALPHA_101",
            version="v1.0.0",
            sha256_hash=self.hash_v1,
            training_dataset_id="DS_2025_Q4",
            sharpe_ratio=2.1,
            max_drawdown_pct=10.0,
            status="PRODUCTION",
            is_active=False,
            registered_at_epoch=1740000000.0
        )
        self.ver_2 = ModelVersion(
            model_id="ML_ALPHA_101",
            version="v1.1.0",
            sha256_hash=self.hash_v2,
            training_dataset_id="DS_2026_Q1",
            sharpe_ratio=2.4,
            max_drawdown_pct=11.0,
            status="PRODUCTION",
            is_active=True,
            registered_at_epoch=1750000000.0
        )

        self.engine.register_version(self.ver_1)
        self.engine.register_version(self.ver_2)

    def test_healthy_telemetry_no_rollback(self):
        # Drawdown = 8% <= 15% limit -> MODEL_VERSION_HEALTHY!
        cfg = RollbackTriggerConfig(max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0)
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", live_drawdown_pct=8.0, live_error_rate_pct=1.0, recent_sharpe=2.3)

        report = self.engine.audit_telemetry_and_rollback(cfg, telem)

        self.assertFalse(report.is_rollback_executed)
        self.assertEqual(report.status, "MODEL_VERSION_HEALTHY")
        self.assertEqual(report.active_version, "v1.1.0")

    def test_drawdown_breach_triggers_rollback_to_v1(self):
        # Drawdown = 18.5% > 15% limit -> ROLLBACK_SUCCESSFUL to v1.0.0!
        cfg = RollbackTriggerConfig(max_allowed_drawdown_pct=15.0, max_allowed_error_rate_pct=5.0)
        telem = LivePerformanceTelemetry("ML_ALPHA_101", "v1.1.0", live_drawdown_pct=18.5, live_error_rate_pct=1.0, recent_sharpe=0.4)

        report = self.engine.audit_telemetry_and_rollback(cfg, telem)

        self.assertTrue(report.is_rollback_executed)
        self.assertEqual(report.status, "ROLLBACK_SUCCESSFUL")
        self.assertEqual(report.active_version, "v1.0.0")
        self.assertEqual(report.previous_version, "v1.1.0")
        self.assertEqual(report.sha256_hash, self.hash_v1)

if __name__ == '__main__':
    unittest.main()
