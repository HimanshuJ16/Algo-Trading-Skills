"""
Unit tests for systemd-supervision-for-trading-bots skill.
"""
import datetime
import os
import unittest
from unittest.mock import Mock
from supervision_helper import SystemdSupervisionHelper, Config, Engine

class TestSystemdSupervisionForTradingBots(unittest.TestCase):

    def setUp(self):
        self.helper = SystemdSupervisionHelper()
        self.unit_path = os.path.join(
            os.path.dirname(__file__), "trading-bot.service"
        )

    def test_legacy_config_engine(self):
        cfg = Config("test")
        eng = Engine(cfg)
        self.assertEqual(eng.config.name, "test")
        self.assertTrue(eng.run())

    def test_unit_file_validation(self):
        with open(self.unit_path, "r") as f:
            content = f.read()

        valid, issues = SystemdSupervisionHelper.validate_unit_file_content(content)
        self.assertTrue(valid, f"Unit file validation issues: {issues}")

    def test_premarket_healthcheck_success(self):
        secrets = {"BROKER_API_KEY": "key123", "BROKER_SECRET": "sec456"}
        conn_mock = Mock(return_value=True)
        holiday_mock = Mock(return_value=False)

        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict=secrets,
            broker_connectivity_fn=conn_mock,
            is_holiday_fn=holiday_mock,
            as_of_date=datetime.date(2026, 7, 15),
        )

        self.assertTrue(res.passed)
        self.assertTrue(res.checks["secrets_present"])
        self.assertTrue(res.checks["not_market_holiday"])
        self.assertTrue(res.checks["broker_connectivity"])

    def test_premarket_healthcheck_failure_on_missing_secrets(self):
        secrets = {"BROKER_API_KEY": ""}  # Missing key!
        conn_mock = Mock(return_value=True)

        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict=secrets, broker_connectivity_fn=conn_mock
        )

        self.assertFalse(res.passed)
        self.assertFalse(res.checks["secrets_present"])

    def test_sd_notify_formatting(self):
        # Disabled mode when NOTIFY_SOCKET is not set
        self.assertFalse(self.helper.sd_notify("WATCHDOG=1"))

if __name__ == "__main__":
    unittest.main()
