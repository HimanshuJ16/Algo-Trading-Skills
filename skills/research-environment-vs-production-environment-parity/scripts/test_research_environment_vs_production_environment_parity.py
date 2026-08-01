import unittest
from research_environment_vs_production_environment_parity import (
    ResearchEnvironmentVsProductionEnvironmentParity,
    ResearchEnvironmentVsProductionEnvironmentParityConfig,
    ResearchEnvironmentVsProductionEnvironmentParityEngine,
    EnvironmentSnapshot, EnvironmentParityReport
)

class TestResearchEnvironmentVsProductionEnvironmentParity(unittest.TestCase):

    def test_legacy_init(self):
        config = ResearchEnvironmentVsProductionEnvironmentParityConfig()
        obj = ResearchEnvironmentVsProductionEnvironmentParity(config)
        self.assertIsNotNone(obj)

    def test_legacy_process(self):
        config = ResearchEnvironmentVsProductionEnvironmentParityConfig()
        obj = ResearchEnvironmentVsProductionEnvironmentParity(config)
        self.assertTrue(obj.process())

class TestParityEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()
        self.base_env = EnvironmentSnapshot(
            env_type="RESEARCH",
            python_version="3.10.11",
            package_versions={"numpy": "1.24.3", "pandas": "2.0.1"},
            float_precision="float64",
            feature_definitions={"rsi": "hash_123", "macd": "hash_456"}
        )

    def test_perfect_parity(self):
        prod_env = EnvironmentSnapshot(
            env_type="PRODUCTION",
            python_version="3.10.11",
            package_versions={"numpy": "1.24.3", "pandas": "2.0.1"},
            float_precision="float64",
            feature_definitions={"rsi": "hash_123", "macd": "hash_456"}
        )
        signals = [(1.5000, 1.5001), (2.1000, 2.1000)]
        report = self.engine.audit_environment_parity(self.base_env, prod_env, signals)
        self.assertEqual(report.status, "PARITY_VERIFIED")
        self.assertTrue(report.is_parity_achieved)
        self.assertEqual(report.critical_discrepancies, 0)

    def test_feature_mismatch_breach(self):
        prod_env = EnvironmentSnapshot(
            env_type="PRODUCTION",
            python_version="3.10.11",
            package_versions={"numpy": "1.24.3", "pandas": "2.0.1"},
            float_precision="float64",
            feature_definitions={"rsi": "hash_123", "macd": "hash_DIFFERENT"}
        )
        report = self.engine.audit_environment_parity(self.base_env, prod_env)
        self.assertEqual(report.status, "PARITY_BREACHED")
        self.assertFalse(report.is_parity_achieved)
        self.assertEqual(report.critical_discrepancies, 1)
        self.assertEqual(report.discrepancies[0].item_name, "macd")

    def test_signal_output_skew_breach(self):
        prod_env = EnvironmentSnapshot(
            env_type="PRODUCTION",
            python_version="3.10.11",
            package_versions={"numpy": "1.24.3", "pandas": "2.0.1"},
            float_precision="float64",
            feature_definitions={"rsi": "hash_123", "macd": "hash_456"}
        )
        # 1.50 vs 1.60 -> >0.1% diff
        signals = [(1.5000, 1.6000)]
        report = self.engine.audit_environment_parity(self.base_env, prod_env, signals)
        self.assertEqual(report.status, "PARITY_BREACHED")
        self.assertTrue(any(d.component == "SIGNAL_OUTPUT" for d in report.discrepancies))

if __name__ == '__main__':
    unittest.main()
