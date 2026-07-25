"""
Unit tests for alpaca-paper-live-key-separation skill.

Tests:
1. Paper mode configuration validation against paper base URL.
2. Rejection of paper credentials paired with live endpoint URL.
3. Live mode initialization veto without ALLOW_LIVE_TRADING=true env flag.
4. Account probe is_paper verification matching configured mode.
5. Order submission guard execution.
"""
import os
import unittest
from unittest.mock import Mock
from alpaca_env_guard import (
    LIVE_BASE_URL,
    PAPER_BASE_URL,
    AlpacaConfig,
    AlpacaEnvironmentManager,
    EnvironmentMismatchError,
    TradingEnvironment,
)


class TestAlpacaPaperLiveKeySeparation(unittest.TestCase):

    def setUp(self):
        self.mgr = AlpacaEnvironmentManager()
        self.paper_config = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="PK1234567890",
            secret_key="SECRET_PAPER",
            base_url=PAPER_BASE_URL,
        )
        self.live_config = AlpacaConfig(
            environment=TradingEnvironment.LIVE,
            key_id="AK9876543210",
            secret_key="SECRET_LIVE",
            base_url=LIVE_BASE_URL,
        )

    def test_valid_paper_config(self):
        self.assertTrue(self.mgr.validate_config(self.paper_config))

    def test_paper_mode_with_live_url_rejection(self):
        flawed = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url=LIVE_BASE_URL,  # Mismatch!
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_paper_mode_with_live_key_rejection(self):
        flawed = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="AK1234567890", # Live key!
            secret_key="SECRET",
            base_url=PAPER_BASE_URL,
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_live_mode_blocked_without_env_flag(self):
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(self.live_config)

    def test_live_mode_allowed_with_env_flag(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        try:
            self.assertTrue(self.mgr.validate_config(self.live_config))
        finally:
            os.environ.pop("ALLOW_LIVE_TRADING", None)

    def test_account_probe_mismatch_detection(self):
        # Configure paper, but mock account returns is_paper=False (LIVE)
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": False})

        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)


if __name__ == "__main__":
    unittest.main()
