"""Unit tests for b3-brazil-exchange-api-integration.

Coverage:
1. The SBE gap-recovery constraint (the skill's central validated rule).
2. CompID whitelist — regression for a validator that accepted any string
   containing an underscore, including control characters.
3. IP validation, including multicast enforcement and int-address rejection.
4. Connection state machine, including the previously unreachable FAILED state.
5. Credential redaction and thread safety.
"""
import logging
import re
import threading
import unittest

from b3_brazil_exchange_api_integration import (
    B3ConfigurationError,
    B3ConnectionConfig,
    B3ConnectionError,
    B3ConnectionState,
    B3IntegrationEngine,
    B3ProtocolSuite,
)

logging.getLogger("b3_brazil_exchange_api_integration").setLevel(logging.CRITICAL)


def make_config(**overrides):
    kwargs = dict(
        comp_id="B3_TRADER_1",
        password="secret_cert",
        order_entry_ip="192.168.1.10",
        market_data_multicast_ip="239.1.1.1",
        protocol_suite=B3ProtocolSuite.LEGACY_FIX_FAST,
        enable_application_gap_recovery=False,
    )
    kwargs.update(overrides)
    return B3ConnectionConfig(**kwargs)


class TestSbeGapRecoveryConstraint(unittest.TestCase):
    """B3 Binary UMDF (SBE) has no TCP recovery channel for gap filling."""

    def test_sbe_without_gap_recovery_is_rejected(self):
        with self.assertRaises(B3ConfigurationError) as ctx:
            B3IntegrationEngine(
                make_config(
                    protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
                    enable_application_gap_recovery=False,
                )
            )
        self.assertIn("no TCP recovery channel", str(ctx.exception))

    def test_sbe_with_gap_recovery_is_accepted(self):
        engine = B3IntegrationEngine(
            make_config(
                protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
                enable_application_gap_recovery=True,
            )
        )
        self.assertEqual(engine.config.protocol_suite, B3ProtocolSuite.MODERN_BINARY_SBE)

    def test_legacy_without_gap_recovery_is_allowed(self):
        # Legacy FIX/FAST UMDF publishes TCP Replayer / Snapshot Recovery, so
        # application-level recovery is not forced here.
        engine = B3IntegrationEngine(make_config(enable_application_gap_recovery=False))
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)

    def test_error_is_a_valueerror_for_legacy_callers(self):
        with self.assertRaises(ValueError):
            B3IntegrationEngine(
                make_config(
                    protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
                    enable_application_gap_recovery=False,
                )
            )


class TestCompIdValidation(unittest.TestCase):
    """Regression: the previous validator only checked characters when the
    CompID contained no underscore, so 'A_!@#$%' and 'A_\\nB' were accepted."""

    def test_accepts_valid_ids(self):
        for cid in ("B3_TRADER_1", "ABCDEFGHIJKL", "A", "trader01", "_"):
            with self.subTest(cid=cid):
                self.assertEqual(make_config(comp_id=cid).comp_id, cid)

    def test_rejects_special_characters_even_with_underscore(self):
        # Every one of these was ACCEPTED by the pre-fix validator.
        for cid in ("B3-TRADER_1", "A_!@#$%", "A_ B", "A_\nB", "A_\x00B", "A_;DROP"):
            with self.subTest(cid=cid):
                with self.assertRaises(B3ConfigurationError):
                    make_config(comp_id=cid)

    def test_rejects_special_characters_without_underscore(self):
        for cid in ("B3-TRADER", "A B", "A.B"):
            with self.subTest(cid=cid):
                with self.assertRaises(B3ConfigurationError):
                    make_config(comp_id=cid)

    def test_length_boundaries(self):
        self.assertEqual(make_config(comp_id="A" * 12).comp_id, "A" * 12)
        with self.assertRaises(B3ConfigurationError):
            make_config(comp_id="A" * 13)
        with self.assertRaises(B3ConfigurationError):
            make_config(comp_id="")

    def test_non_string_rejected(self):
        for bad in (None, 12345, ["ID"]):
            with self.subTest(bad=bad):
                with self.assertRaises(B3ConfigurationError):
                    make_config(comp_id=bad)

    def test_pattern_is_overridable(self):
        # The default whitelist is this skill's conservative choice, not a
        # quoted B3 rule, so callers must be able to widen it.
        cfg = make_config(comp_id="B3-TRADER", comp_id_pattern=re.compile(r"^[\w\-]{1,20}$"))
        self.assertEqual(cfg.comp_id, "B3-TRADER")

    def test_error_message_reveals_control_characters(self):
        with self.assertRaises(B3ConfigurationError) as ctx:
            make_config(comp_id="A_\nB")
        self.assertIn("\\n", str(ctx.exception))


class TestIpValidation(unittest.TestCase):
    def test_rejects_malformed_addresses(self):
        with self.assertRaises(B3ConfigurationError):
            make_config(order_entry_ip="999.999.999.999")
        with self.assertRaises(B3ConfigurationError):
            make_config(market_data_multicast_ip="invalid.ip.address")

    def test_rejects_integer_address(self):
        # IPv4Address(12345) silently yields 0.0.48.57; a mistyped config must fail.
        with self.assertRaises(B3ConfigurationError):
            make_config(order_entry_ip=12345)

    def test_market_data_must_be_multicast_by_default(self):
        with self.assertRaises(B3ConfigurationError) as ctx:
            make_config(market_data_multicast_ip="192.168.1.50")
        self.assertIn("multicast", str(ctx.exception))

    def test_unicast_market_data_allowed_when_explicitly_opted_out(self):
        cfg = make_config(
            market_data_multicast_ip="192.168.1.50",
            require_multicast_market_data=False,
        )
        self.assertEqual(cfg.market_data_multicast_ip, "192.168.1.50")

    def test_order_entry_must_not_be_multicast(self):
        with self.assertRaises(B3ConfigurationError) as ctx:
            make_config(order_entry_ip="239.1.1.1")
        self.assertIn("point-to-point", str(ctx.exception))

    def test_accepts_valid_pair(self):
        cfg = make_config()
        self.assertEqual(cfg.order_entry_ip, "192.168.1.10")
        self.assertEqual(cfg.market_data_multicast_ip, "239.1.1.1")


class TestProtocolSuiteValidation(unittest.TestCase):
    def test_rejects_raw_string_protocol_suite(self):
        with self.assertRaises(B3ConfigurationError):
            make_config(protocol_suite="MODERN_BINARY_SBE")

    def test_engine_rejects_non_config(self):
        with self.assertRaises(B3ConfigurationError):
            B3IntegrationEngine({"comp_id": "X"})


class TestConnectionStateMachine(unittest.TestCase):
    def test_default_connector_reaches_connected(self):
        engine = B3IntegrationEngine(make_config())
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)
        self.assertTrue(engine.connect())
        self.assertEqual(engine.state, B3ConnectionState.CONNECTED)
        self.assertTrue(engine.disconnect())
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)

    def test_connect_is_idempotent(self):
        engine = B3IntegrationEngine(make_config())
        engine.connect()
        self.assertTrue(engine.connect())
        self.assertEqual(engine.state, B3ConnectionState.CONNECTED)

    def test_disconnect_is_idempotent(self):
        engine = B3IntegrationEngine(make_config())
        self.assertTrue(engine.disconnect())
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)

    def test_failing_connector_reaches_failed_state(self):
        # FAILED was previously unreachable dead code.
        def boom(config):
            raise OSError("connection refused")

        engine = B3IntegrationEngine(make_config(), connector=boom)
        with self.assertRaises(B3ConnectionError):
            engine.connect()
        self.assertEqual(engine.state, B3ConnectionState.FAILED)

    def test_recovery_from_failed_state(self):
        attempts = []

        def flaky(config):
            attempts.append(1)
            if len(attempts) == 1:
                raise OSError("first attempt fails")

        engine = B3IntegrationEngine(make_config(), connector=flaky)
        with self.assertRaises(B3ConnectionError):
            engine.connect()
        self.assertEqual(engine.state, B3ConnectionState.FAILED)
        self.assertTrue(engine.connect())
        self.assertEqual(engine.state, B3ConnectionState.CONNECTED)

    def test_connector_receives_config(self):
        seen = []
        engine = B3IntegrationEngine(make_config(), connector=seen.append)
        engine.connect()
        self.assertIs(seen[0], engine.config)

    def test_failing_disconnector_still_disconnects(self):
        def boom(config):
            raise OSError("socket already closed")

        engine = B3IntegrationEngine(make_config(), disconnector=boom)
        engine.connect()
        self.assertTrue(engine.disconnect())
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)


class TestCredentialHandling(unittest.TestCase):
    def test_password_absent_from_repr(self):
        cfg = make_config(password="super_secret_value")
        self.assertNotIn("super_secret_value", repr(cfg))
        self.assertIn("comp_id", repr(cfg))

    def test_password_still_readable_by_the_connector(self):
        cfg = make_config(password="super_secret_value")
        self.assertEqual(cfg.password, "super_secret_value")


class TestConfigImmutability(unittest.TestCase):
    """Validation runs once, so the config must not be editable afterwards."""

    def test_cannot_mutate_validated_config(self):
        cfg = make_config()
        for attr, value in (
            ("comp_id", "BAD ID"),
            ("order_entry_ip", "999.999.999.999"),
            ("enable_application_gap_recovery", True),
        ):
            with self.subTest(attr=attr):
                with self.assertRaises(Exception):
                    setattr(cfg, attr, value)
        self.assertEqual(cfg.comp_id, "B3_TRADER_1")

    def test_engine_config_cannot_be_downgraded_after_validation(self):
        engine = B3IntegrationEngine(
            make_config(
                protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
                enable_application_gap_recovery=True,
            )
        )
        with self.assertRaises(Exception):
            engine.config.enable_application_gap_recovery = False
        self.assertTrue(engine.config.enable_application_gap_recovery)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_connect_disconnect_leaves_valid_state(self):
        engine = B3IntegrationEngine(make_config())
        errors = []

        def churn():
            try:
                for _ in range(500):
                    engine.connect()
                    engine.disconnect()
            except Exception as exc:  # noqa: BLE001 - test asserts none occur
                errors.append(exc)

        threads = [threading.Thread(target=churn) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertIn(
            engine.state,
            (B3ConnectionState.CONNECTED, B3ConnectionState.DISCONNECTED),
        )


if __name__ == "__main__":
    unittest.main()
