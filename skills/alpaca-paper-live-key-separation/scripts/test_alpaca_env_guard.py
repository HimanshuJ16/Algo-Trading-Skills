"""
Unit tests for alpaca-paper-live-key-separation skill.

Tests:
1. Paper mode configuration validation against paper base URL.
2. Rejection of paper credentials paired with live endpoint URL.
3. Live mode initialization veto without ALLOW_LIVE_TRADING=true env flag.
4. Account probe is_paper verification matching configured mode.
5. Order submission guard execution.
6. Live mode with paper URL mismatch rejection.
7. Live mode with paper key prefix rejection.
8. Non-ACTIVE account status rejection.
9. Paper probe success path.
10. Live probe mismatch (LIVE mode, account is paper).
11. Empty credential rejection via AlpacaConfig.
12. is_paper missing from API response treated as live (fail-safe).
13. Config immutability (frozen dataclass).
14. guard_order with account probe function.
15. guard_order without account probe function.
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

    # --- Config validation ---

    def test_valid_paper_config(self):
        self.assertTrue(self.mgr.validate_config(self.paper_config))

    def test_paper_mode_with_live_url_rejection(self):
        flawed = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url=LIVE_BASE_URL,
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_paper_mode_with_live_key_rejection(self):
        flawed = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="AK1234567890",
            secret_key="SECRET",
            base_url=PAPER_BASE_URL,
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_paper_mode_with_arbitrary_url_rejection(self):
        """Paper mode must positively match PAPER_BASE_URL — arbitrary URLs rejected."""
        flawed = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url="https://some-other-api.alpaca.markets",
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

    def test_live_mode_with_paper_url_rejection(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        try:
            flawed = AlpacaConfig(
                environment=TradingEnvironment.LIVE,
                key_id="AK9876543210",
                secret_key="SECRET",
                base_url=PAPER_BASE_URL,
            )
            with self.assertRaises(EnvironmentMismatchError):
                self.mgr.validate_config(flawed)
        finally:
            os.environ.pop("ALLOW_LIVE_TRADING", None)

    def test_live_mode_with_paper_key_rejection(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        try:
            flawed = AlpacaConfig(
                environment=TradingEnvironment.LIVE,
                key_id="PK1234567890",
                secret_key="SECRET",
                base_url=LIVE_BASE_URL,
            )
            with self.assertRaises(EnvironmentMismatchError):
                self.mgr.validate_config(flawed)
        finally:
            os.environ.pop("ALLOW_LIVE_TRADING", None)

    # --- Empty credential validation ---

    def test_empty_key_id_rejected(self):
        with self.assertRaises(ValueError):
            AlpacaConfig(
                environment=TradingEnvironment.PAPER,
                key_id="",
                secret_key="SECRET",
                base_url=PAPER_BASE_URL,
            )

    def test_whitespace_key_id_rejected(self):
        with self.assertRaises(ValueError):
            AlpacaConfig(
                environment=TradingEnvironment.PAPER,
                key_id="   ",
                secret_key="SECRET",
                base_url=PAPER_BASE_URL,
            )

    def test_empty_secret_key_rejected(self):
        with self.assertRaises(ValueError):
            AlpacaConfig(
                environment=TradingEnvironment.PAPER,
                key_id="PK1234567890",
                secret_key="",
                base_url=PAPER_BASE_URL,
            )

    def test_empty_base_url_rejected(self):
        with self.assertRaises(ValueError):
            AlpacaConfig(
                environment=TradingEnvironment.PAPER,
                key_id="PK1234567890",
                secret_key="SECRET",
                base_url="",
            )

    # --- Config immutability ---

    def test_config_is_frozen(self):
        with self.assertRaises(Exception):
            self.paper_config.key_id = "AK0000000000"

    # --- Account probe ---

    def test_account_probe_paper_success(self):
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": True})
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_mismatch_detection(self):
        """Paper mode but account is live."""
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": False})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    def test_account_probe_live_mismatch_detection(self):
        """Live mode but account is paper."""
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        try:
            mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": True})
            with self.assertRaises(EnvironmentMismatchError):
                self.mgr.probe_account(self.live_config, mock_account)
        finally:
            os.environ.pop("ALLOW_LIVE_TRADING", None)

    def test_account_probe_non_active_status_rejected(self):
        mock_account = Mock(return_value={"status": "ACCOUNT_CLOSED", "is_paper": True})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    def test_account_probe_is_paper_missing_defaults_to_live(self):
        """Fail-safe: missing is_paper field treated as live, vetoed in paper mode."""
        mock_account = Mock(return_value={"status": "ACTIVE"})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    def test_account_probe_live_success_with_missing_is_paper(self):
        """Fail-safe: missing is_paper treated as live — valid for live mode."""
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        try:
            mock_account = Mock(return_value={"status": "ACTIVE"})
            self.assertTrue(self.mgr.probe_account(self.live_config, mock_account))
        finally:
            os.environ.pop("ALLOW_LIVE_TRADING", None)

    def test_account_probe_exception_wrapped(self):
        mock_account = Mock(side_effect=ConnectionError("Network error"))
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    # --- Order guard ---

    def test_guard_order_without_account_probe(self):
        self.assertTrue(
            self.mgr.guard_order(
                self.paper_config, symbol="AAPL", qty=10, side="buy"
            )
        )

    def test_guard_order_with_account_probe(self):
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": True})
        self.assertTrue(
            self.mgr.guard_order(
                self.paper_config, symbol="AAPL", qty=10, side="buy",
                get_account_fn=mock_account,
            )
        )

    def test_guard_order_live_blocked_without_env_flag(self):
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.guard_order(
                self.live_config, symbol="AAPL", qty=10, side="buy"
            )


if __name__ == "__main__":
    unittest.main()
