import unittest
from config_drift_detector import ConfigurationDriftDetector, ConfigAuditReport

class TestConfigurationDriftDetector(unittest.TestCase):

    def setUp(self):
        self.detector = ConfigurationDriftDetector(allowed_overrides={"api_url", "log_level", "port"})
        
        self.golden_baseline = {
            "strategy": {
                "max_order_usd": 100000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }

    def test_identical_configs_pass(self):
        report = self.detector.audit(self.golden_baseline, self.golden_baseline)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 0)

    def test_allowed_override_passes(self):
        target_config = {
            "strategy": {
                "max_order_usd": 100000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://staging.api.exchange.com", # Allowed override
                "log_level": "DEBUG",                          # Allowed override
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 0)
        self.assertEqual(report.allowed_override_count, 2)

    def test_critical_drift_fails(self):
        # max_order_usd changed from 100,000 to 1,000,000 (Critical risk parameter mismatch)
        target_config = {
            "strategy": {
                "max_order_usd": 1000000,
                "risk_stop_loss_pct": 0.02
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)

    def test_missing_key_fails(self):
        # risk_stop_loss_pct missing in target config
        target_config = {
            "strategy": {
                "max_order_usd": 100000
            },
            "system": {
                "api_url": "https://prod.api.exchange.com",
                "log_level": "INFO",
                "threads": 8
            }
        }
        report = self.detector.audit(self.golden_baseline, target_config)
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.critical_drift_count, 1)

if __name__ == '__main__':
    unittest.main()
