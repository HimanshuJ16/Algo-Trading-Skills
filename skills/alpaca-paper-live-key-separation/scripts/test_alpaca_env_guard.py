"""
Unit tests for alpaca-paper-live-key-separation skill.

Covers:
1.  Paper mode configuration validation against paper base URL.
2.  Rejection of paper credentials paired with live endpoint URL.
3.  Live mode initialization veto without ALLOW_LIVE_TRADING=true env flag.
4.  Account probe environment verification against the configured mode.
5.  Order submission guard execution.
6.  Live mode with paper URL mismatch rejection.
7.  Live mode with paper key prefix rejection.
8.  Non-tradable account status rejection.
9.  Paper probe success path.
10. Live probe mismatch (LIVE mode, account is paper).
11. Empty credential rejection via AlpacaConfig.
12. Unrecognised environment values refused rather than silently authorised.
13. Config immutability (frozen dataclass).
14. guard_order with and without an account probe function.
15. Order parameter validation in the veto gate.
16. Order-blocking account flags (trading_blocked / account_blocked /
    trade_suspended_by_user).
17. Environment resolution from the fields the real API actually returns.
"""
import math
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
        # Snapshot the process environment so a test that sets or clears
        # ALLOW_LIVE_TRADING cannot leak that state into another test.
        _snapshot = dict(os.environ)

        def _restore():
            os.environ.clear()
            os.environ.update(_snapshot)

        self.addCleanup(_restore)

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

    def test_lookalike_domain_rejected(self):
        """A look-alike host that merely contains the live URL must not pass."""
        flawed = AlpacaConfig(
            environment=TradingEnvironment.LIVE,
            key_id="AK1234567890",
            secret_key="SECRET",
            base_url="https://api.alpaca.markets.attacker.example",
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_base_url_case_and_trailing_slash_normalised(self):
        """Hostnames are case-insensitive; casing must not cause a spurious veto."""
        cfg = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url="HTTPS://PAPER-API.Alpaca.Markets/",
        )
        self.assertTrue(self.mgr.validate_config(cfg))

    def test_live_mode_blocked_without_env_flag(self):
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(self.live_config)

    def test_live_mode_allowed_with_env_flag(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        self.assertTrue(self.mgr.validate_config(self.live_config))

    def test_live_flag_tolerates_surrounding_whitespace(self):
        """A trailing newline from a .env loader must not silently block live mode."""
        os.environ["ALLOW_LIVE_TRADING"] = " true\n"
        self.assertTrue(self.mgr.validate_config(self.live_config))

    def test_live_flag_rejects_non_true_values(self):
        for value in ("false", "1", "yes", "TRUE_ISH", ""):
            with self.subTest(value=value):
                os.environ["ALLOW_LIVE_TRADING"] = value
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.validate_config(self.live_config)

    def test_live_mode_with_paper_url_rejection(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        flawed = AlpacaConfig(
            environment=TradingEnvironment.LIVE,
            key_id="AK9876543210",
            secret_key="SECRET",
            base_url=PAPER_BASE_URL,
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_live_mode_with_paper_key_rejection(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        flawed = AlpacaConfig(
            environment=TradingEnvironment.LIVE,
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url=LIVE_BASE_URL,
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.validate_config(flawed)

    def test_unknown_key_prefix_is_not_rejected(self):
        """Only the *opposite* environment's prefix is a veto; unknown formats pass."""
        cfg = AlpacaConfig(
            environment=TradingEnvironment.PAPER,
            key_id="CKQQQQQQQQQQ",
            secret_key="SECRET",
            base_url=PAPER_BASE_URL,
        )
        self.assertTrue(self.mgr.validate_config(cfg))

    # --- Environment coercion / fail-closed behaviour ---

    def test_environment_accepts_plain_string(self):
        """TradingEnvironment is a str Enum; YAML/env configs supply plain strings."""
        cfg = AlpacaConfig(
            environment="paper",
            key_id="PK1234567890",
            secret_key="SECRET",
            base_url=PAPER_BASE_URL,
        )
        self.assertIs(cfg.environment, TradingEnvironment.PAPER)
        self.assertTrue(self.mgr.validate_config(cfg))

    def test_unrecognised_environment_rejected(self):
        """Regression: an unrecognised environment must never be authorised.

        Previously such a value matched neither the PAPER nor the LIVE branch and
        fell through to `return True`, so guard_order authorised an order against
        the live endpoint with live keys and no ALLOW_LIVE_TRADING flag set.
        """
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        for bad in ("SANDBOX", "", None, 123, object()):
            with self.subTest(environment=bad):
                with self.assertRaises(ValueError):
                    AlpacaConfig(
                        environment=bad,
                        key_id="AK9876543210",
                        secret_key="SECRET",
                        base_url=LIVE_BASE_URL,
                    )

    def test_foreign_str_enum_environment_rejected(self):
        """A same-valued member of a *different* str Enum must not be accepted."""
        from enum import Enum

        class OtherEnv(str, Enum):
            SANDBOX = "SANDBOX"

        with self.assertRaises(ValueError):
            AlpacaConfig(
                environment=OtherEnv.SANDBOX,
                key_id="AK9876543210",
                secret_key="SECRET",
                base_url=LIVE_BASE_URL,
            )

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

    # --- Environment resolution from real API fields ---

    def test_resolve_environment_from_is_paper_flag(self):
        resolve = AlpacaEnvironmentManager.resolve_account_environment
        self.assertIs(resolve({"is_paper": True}), TradingEnvironment.PAPER)
        self.assertIs(resolve({"is_paper": False}), TradingEnvironment.LIVE)

    def test_resolve_environment_from_paper_only_status(self):
        self.assertIs(
            AlpacaEnvironmentManager.resolve_account_environment({"status": "PAPER_ONLY"}),
            TradingEnvironment.PAPER,
        )

    def test_resolve_environment_from_paper_account_number(self):
        self.assertIs(
            AlpacaEnvironmentManager.resolve_account_environment(
                {"status": "ACTIVE", "account_number": "PA3ABCDEF12"}
            ),
            TradingEnvironment.PAPER,
        )

    def test_resolve_environment_undeterminable_for_bare_live_payload(self):
        """A real live /v2/account payload carries no environment discriminator."""
        self.assertIsNone(
            AlpacaEnvironmentManager.resolve_account_environment(
                {"status": "ACTIVE", "account_number": "928374651"}
            )
        )

    # --- Account probe ---

    def test_account_probe_paper_success(self):
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": True})
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_realistic_paper_payload_succeeds(self):
        """Regression: the real API returns no `is_paper`, and paper must still pass.

        The previous implementation treated a missing `is_paper` as live, which
        vetoed every genuine paper deployment at startup.
        """
        mock_account = Mock(
            return_value={
                "status": "ACTIVE",
                "account_number": "PA3ABCDEF12",
                "trading_blocked": False,
                "account_blocked": False,
                "trade_suspended_by_user": False,
            }
        )
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_mismatch_detection(self):
        """Paper mode but account is live."""
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": False})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    def test_account_probe_live_mismatch_detection(self):
        """Live mode but account is paper."""
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": True})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.live_config, mock_account)

    def test_account_probe_live_mode_rejects_paper_account_number(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        mock_account = Mock(
            return_value={"status": "ACTIVE", "account_number": "PA3ABCDEF12"}
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.live_config, mock_account)

    def test_account_probe_non_active_status_rejected(self):
        mock_account = Mock(return_value={"status": "ACCOUNT_CLOSED", "is_paper": True})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.paper_config, mock_account)

    def test_account_probe_paper_only_status_allowed_in_paper_mode(self):
        """PAPER_ONLY is a real Alpaca AccountStatus and is tradable on paper."""
        mock_account = Mock(return_value={"status": "PAPER_ONLY"})
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_paper_only_status_rejected_in_live_mode(self):
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        mock_account = Mock(return_value={"status": "PAPER_ONLY"})
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.probe_account(self.live_config, mock_account)

    def test_account_probe_missing_status_rejected(self):
        """Regression: a missing status previously defaulted to ACTIVE (fail-open)."""
        for payload in ({"is_paper": True}, {"status": None, "is_paper": True}, {"status": "  "}):
            with self.subTest(payload=payload):
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))

    def test_account_probe_rejects_order_blocking_flags(self):
        """Alpaca documents these flags as 'not allowed to place orders'."""
        for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
            with self.subTest(flag=flag):
                payload = {"status": "ACTIVE", "is_paper": True, flag: True}
                with self.assertRaises(EnvironmentMismatchError) as ctx:
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))
                self.assertIn(flag, str(ctx.exception))

    def test_account_probe_honours_string_typed_is_paper(self):
        """A string 'false' is an explicit live signal, not an absent one."""
        for value in ("false", "False", " FALSE "):
            with self.subTest(value=value):
                payload = {"status": "ACTIVE", "is_paper": value}
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))

    def test_account_probe_accepts_string_typed_true_is_paper(self):
        mock_account = Mock(return_value={"status": "ACTIVE", "is_paper": "true"})
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_rejects_uninterpretable_is_paper(self):
        """Present but unreadable is a corrupt discriminator, not a missing one."""
        for value in ("maybe", {}, [], 7):
            with self.subTest(value=value):
                payload = {"status": "ACTIVE", "is_paper": value}
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))

    def test_account_probe_honours_string_typed_blocking_flags(self):
        """A string 'true' on a blocking flag must still veto."""
        for value in ("true", "True", "1", "unexpected"):
            with self.subTest(value=value):
                payload = {"status": "ACTIVE", "is_paper": True, "trading_blocked": value}
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))

    def test_account_probe_string_false_blocking_flag_does_not_veto(self):
        mock_account = Mock(
            return_value={"status": "ACTIVE", "is_paper": True, "trading_blocked": "false"}
        )
        self.assertTrue(self.mgr.probe_account(self.paper_config, mock_account))

    def test_account_probe_undetermined_environment_allowed_by_default(self):
        """Bare live payload: separation rests on the already-verified base URL."""
        os.environ["ALLOW_LIVE_TRADING"] = "true"
        mock_account = Mock(return_value={"status": "ACTIVE", "account_number": "928374651"})
        self.assertTrue(self.mgr.probe_account(self.live_config, mock_account))

    def test_account_probe_undetermined_environment_vetoed_when_evidence_required(self):
        strict = AlpacaEnvironmentManager(require_environment_evidence=True)
        mock_account = Mock(return_value={"status": "ACTIVE", "account_number": "928374651"})
        with self.assertRaises(EnvironmentMismatchError):
            strict.probe_account(self.paper_config, mock_account)

    def test_account_probe_non_mapping_response_rejected(self):
        """A non-dict response must raise the documented error, not AttributeError."""
        for payload in (None, "ACTIVE", ["status"], 42):
            with self.subTest(payload=payload):
                with self.assertRaises(EnvironmentMismatchError):
                    self.mgr.probe_account(self.paper_config, Mock(return_value=payload))

    def test_account_probe_exception_wrapped(self):
        mock_account = Mock(side_effect=ConnectionError("Network error"))
        with self.assertRaises(EnvironmentMismatchError) as ctx:
            self.mgr.probe_account(self.paper_config, mock_account)
        self.assertIsInstance(ctx.exception.__cause__, ConnectionError)

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

    def test_guard_order_rejects_invalid_quantity(self):
        for qty in (0, -1, -0.5, float("nan"), float("inf"), True, "10", None):
            with self.subTest(qty=qty):
                with self.assertRaises(ValueError):
                    self.mgr.guard_order(
                        self.paper_config, symbol="AAPL", qty=qty, side="buy"
                    )

    def test_guard_order_rejects_invalid_symbol(self):
        for symbol in ("", "   ", None, 123):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    self.mgr.guard_order(
                        self.paper_config, symbol=symbol, qty=10, side="buy"
                    )

    def test_guard_order_rejects_invalid_side(self):
        for side in ("", "long", "BUY_TO_OPEN", None, 1):
            with self.subTest(side=side):
                with self.assertRaises(ValueError):
                    self.mgr.guard_order(
                        self.paper_config, symbol="AAPL", qty=10, side=side
                    )

    def test_guard_order_accepts_case_insensitive_side(self):
        for side in ("buy", "BUY", "Sell"):
            with self.subTest(side=side):
                self.assertTrue(
                    self.mgr.guard_order(
                        self.paper_config, symbol="AAPL", qty=10, side=side
                    )
                )

    def test_guard_order_accepts_fractional_quantity(self):
        self.assertTrue(
            self.mgr.guard_order(
                self.paper_config, symbol="AAPL", qty=0.001, side="buy"
            )
        )

    def test_guard_order_validates_config_before_order_parameters(self):
        """A live-mode veto must fire even when the order itself is malformed."""
        os.environ.pop("ALLOW_LIVE_TRADING", None)
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.guard_order(self.live_config, symbol="", qty=-5, side="nonsense")

    def test_guard_order_blocked_account_vetoes_order(self):
        mock_account = Mock(
            return_value={"status": "ACTIVE", "is_paper": True, "trading_blocked": True}
        )
        with self.assertRaises(EnvironmentMismatchError):
            self.mgr.guard_order(
                self.paper_config, symbol="AAPL", qty=10, side="buy",
                get_account_fn=mock_account,
            )


if __name__ == "__main__":
    unittest.main()
