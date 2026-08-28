"""
Unit tests for sandbox-credential-leakage-prevention skill.

Regression coverage is grouped by the defect it pins down. Every test in
``TestFailOpenRegressions`` passes against the current allow-list implementation
and fails against the previous substring deny-list, which approved each of these
requests.

Tests:
 1. Happy paths for Alpaca, Binance, and Saxo in both environments.
 2. Sandbox mode calling the live gateway is blocked.
 3. Production mode calling the sandbox gateway is blocked.
 4. Opposing-environment key prefix is blocked, case-insensitively.
 5. REGRESSION: a live URL carrying "paper" in its query is blocked.
 6. REGRESSION: production mode requires a positive endpoint match (typosquat
    and unrelated hosts are blocked, not merely "not-sandbox").
 7. REGRESSION: an unknown broker fails closed unless explicitly opted out of.
 8. Non-HTTPS, userinfo-bearing, host-less, and non-443 URLs are blocked.
 9. Saxo host-identical / path-separated environments, including path traversal.
10. Binance production market data on the .vision domain is not read as testnet.
11. Query strings and fragments are redacted out of exception messages.
12. Input validation on broker_name / api_key / target_url and environment type.
13. BrokerEnvironmentRules construction, normalisation, and endpoint parsing.
14. EndpointRule prefix matching does not accept sibling paths.
"""
import logging
import unittest

from credential_guard import (
    BROKER_RULES,
    BrokerEnvironmentRules,
    CredentialEnvironmentGuard,
    EndpointRule,
    SecurityViolationError,
    TradingEnvironment,
    iter_declared_endpoints,
)

ALPACA_PAPER = "https://paper-api.alpaca.markets/v2/orders"
ALPACA_LIVE = "https://api.alpaca.markets/v2/orders"


class GuardTestCase(unittest.TestCase):
    """Silences the module's advisory warnings so test output stays readable."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def sandbox(self, **kwargs):
        return CredentialEnvironmentGuard(TradingEnvironment.SANDBOX, **kwargs)

    def production(self, **kwargs):
        return CredentialEnvironmentGuard(TradingEnvironment.PRODUCTION, **kwargs)


class TestHappyPaths(GuardTestCase):

    def test_valid_sandbox_request(self):
        self.assertTrue(
            self.sandbox().validate_request_boundary("alpaca", "PK_MOCK_123456", ALPACA_PAPER)
        )

    def test_valid_production_request(self):
        self.assertTrue(
            self.production().validate_request_boundary("alpaca", "AK_LIVE_998877", ALPACA_LIVE)
        )

    def test_broker_name_is_case_insensitive(self):
        self.assertTrue(
            self.sandbox().validate_request_boundary("ALPACA", "PK_MOCK_1", ALPACA_PAPER)
        )

    def test_binance_alternate_production_hosts_accepted(self):
        """api1-api4 and api-gcp are documented production hosts, not just api.binance.com."""
        guard = self.production()
        for host in ("api.binance.com", "api-gcp.binance.com", "api3.binance.com"):
            with self.subTest(host=host):
                self.assertTrue(
                    guard.validate_request_boundary("binance", "k" * 64, f"https://{host}/api/v3/order")
                )

    def test_binance_futures_testnet_hosts_accepted(self):
        guard = self.sandbox()
        for host in ("testnet.binance.vision", "demo-fapi.binance.com", "testnet.binancefuture.com"):
            with self.subTest(host=host):
                self.assertTrue(
                    guard.validate_request_boundary("binance", "k" * 64, f"https://{host}/fapi/v1/order")
                )

    def test_saxo_sim_and_live_paths_accepted(self):
        self.assertTrue(self.sandbox().validate_request_boundary(
            "saxo", "tok", "https://gateway.saxobank.com/sim/openapi/port/v1/orders"))
        self.assertTrue(self.production().validate_request_boundary(
            "saxo", "tok", "https://gateway.saxobank.com/openapi/port/v1/orders"))


class TestEnvironmentCrossing(GuardTestCase):

    def test_sandbox_calling_production_url_fails(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary("alpaca", "PK_MOCK_123456", ALPACA_LIVE)
        self.assertIn("ENDPOINT LEAK DETECTED", str(ctx.exception))

    def test_production_calling_sandbox_url_fails(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary("alpaca", "AK_LIVE_1", ALPACA_PAPER)
        self.assertIn("ENDPOINT LEAK DETECTED", str(ctx.exception))

    def test_production_mode_with_sandbox_key_fails(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary("alpaca", "PK_MOCK_123456", ALPACA_LIVE)
        self.assertIn("CREDENTIAL LEAK DETECTED", str(ctx.exception))

    def test_sandbox_mode_with_production_key_fails(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary("alpaca", "AK_LIVE_1", ALPACA_PAPER)
        self.assertIn("CREDENTIAL LEAK DETECTED", str(ctx.exception))

    def test_key_prefix_match_is_case_insensitive(self):
        """A lower-cased 'ak_...' is still a production-shaped Alpaca key."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary("alpaca", "ak_live_1", ALPACA_PAPER)
        self.assertIn("CREDENTIAL LEAK DETECTED", str(ctx.exception))

    def test_unrecognised_key_prefix_warns_but_does_not_block(self):
        """The prefix convention is undocumented, so absence of one proves nothing."""
        logging.disable(logging.NOTSET)
        with self.assertLogs("credential_guard", level="WARNING") as logs:
            self.assertTrue(
                self.sandbox().validate_request_boundary("alpaca", "XX_UNKNOWN_1", ALPACA_PAPER)
            )
        self.assertTrue(any("heuristic" in line for line in logs.output))


class TestFailOpenRegressions(GuardTestCase):
    """Each of these was approved by the previous substring deny-list."""

    def test_live_url_with_paper_in_query_is_blocked(self):
        """The bare keyword 'paper' anywhere in the URL used to disable the live-gateway check."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary(
                "alpaca", "PK_1", "https://api.alpaca.markets/v2/orders?client_tag=paper")
        self.assertIn("ENDPOINT LEAK DETECTED", str(ctx.exception))

    def test_live_url_with_paper_in_path_is_blocked(self):
        with self.assertRaises(SecurityViolationError):
            self.sandbox().validate_request_boundary(
                "alpaca", "PK_1", "https://api.alpaca.markets/v2/paper/orders")

    def test_production_typosquat_host_is_blocked(self):
        """Production used to only check 'is not sandbox', never 'is production'."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://api.alpaca.markets.attacker.example/v2/orders")
        self.assertIn("not a recognised", str(ctx.exception))

    def test_production_unrelated_host_is_blocked(self):
        with self.assertRaises(SecurityViolationError):
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://attacker.example/v2/orders")

    def test_sandbox_host_named_in_query_of_foreign_host_is_blocked(self):
        with self.assertRaises(SecurityViolationError):
            self.sandbox().validate_request_boundary(
                "alpaca", "PK_1", "https://evil.example/?redirect=paper-api.alpaca.markets")

    def test_unknown_broker_fails_closed_in_sandbox(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary("zerodha", "AK_1", "https://api.kite.trade/orders")
        self.assertIn("no environment rules defined", str(ctx.exception))

    def test_unknown_broker_fails_closed_in_production(self):
        with self.assertRaises(SecurityViolationError):
            self.production().validate_request_boundary("zerodha", "PK_1", "https://paper.example/orders")

    def test_unknown_broker_passes_only_with_explicit_opt_out(self):
        guard = self.production(allow_unknown_brokers=True)
        self.assertTrue(guard.validate_request_boundary("zerodha", "k", "https://api.kite.trade/orders"))

    def test_binance_production_market_data_on_vision_domain_is_not_testnet(self):
        """data-api.binance.vision is production; testnet.binance.vision is not.

        The two share a registrable domain, so no substring of the domain
        separates them.
        """
        with self.assertRaises(SecurityViolationError):
            self.sandbox().validate_request_boundary(
                "binance", "k" * 64, "https://data-api.binance.vision/api/v3/klines")
        self.assertTrue(self.production().validate_request_boundary(
            "binance", "k" * 64, "https://data-api.binance.vision/api/v3/klines"))


class TestUrlHardening(GuardTestCase):

    def test_plaintext_http_is_blocked(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "http://api.alpaca.markets/v2/orders")
        self.assertIn("HTTPS", str(ctx.exception))

    def test_userinfo_in_url_is_blocked(self):
        """https://a@b/ reads as host 'a' but resolves to host 'b'."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://api.alpaca.markets@evil.example/v2/orders")
        self.assertIn("CREDENTIAL LEAK DETECTED", str(ctx.exception))

    def test_missing_hostname_is_blocked(self):
        with self.assertRaises(SecurityViolationError):
            self.production().validate_request_boundary("alpaca", "AK_1", "https:///v2/orders")

    def test_non_standard_port_is_blocked(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://api.alpaca.markets:8443/v2/orders")
        self.assertIn("port", str(ctx.exception))

    def test_explicit_443_is_accepted(self):
        self.assertTrue(self.production().validate_request_boundary(
            "alpaca", "AK_1", "https://api.alpaca.markets:443/v2/orders"))

    def test_malformed_port_is_blocked_not_crashed(self):
        with self.assertRaises(SecurityViolationError):
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://api.alpaca.markets:notaport/v2/orders")

    def test_uppercase_host_is_normalised(self):
        self.assertTrue(self.production().validate_request_boundary(
            "alpaca", "AK_1", "https://API.ALPACA.MARKETS/v2/orders"))

    def test_path_traversal_cannot_escape_sandbox_prefix(self):
        """/sim/openapi/../../openapi resolves to the LIVE Saxo path."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary(
                "saxo", "tok",
                "https://gateway.saxobank.com/sim/openapi/../../openapi/port/v1/orders")
        self.assertIn("ENDPOINT LEAK DETECTED", str(ctx.exception))

    def test_sibling_path_does_not_satisfy_prefix(self):
        """/simulator must not match the /sim/openapi prefix."""
        with self.assertRaises(SecurityViolationError):
            self.sandbox().validate_request_boundary(
                "saxo", "tok", "https://gateway.saxobank.com/simulator/openapi/x")


class TestSecretRedaction(GuardTestCase):

    def test_query_string_is_redacted_from_exception(self):
        """Binance signs REST calls with &signature=<hmac> in the query string."""
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary(
                "binance", "k" * 64,
                "https://api.binance.com/api/v3/order?symbol=BTCUSDT&signature=deadbeefsecret")
        message = str(ctx.exception)
        self.assertNotIn("deadbeefsecret", message)
        self.assertIn("<redacted>", message)

    def test_fragment_is_redacted_from_exception(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.production().validate_request_boundary(
                "alpaca", "AK_1", "https://attacker.example/v2/orders#tokensecret")
        self.assertNotIn("tokensecret", str(ctx.exception))

    def test_api_key_is_never_echoed_in_full(self):
        with self.assertRaises(SecurityViolationError) as ctx:
            self.sandbox().validate_request_boundary("alpaca", "AK_SUPER_SECRET_VALUE", ALPACA_PAPER)
        self.assertNotIn("SUPER_SECRET_VALUE", str(ctx.exception))


class TestInputValidation(GuardTestCase):

    def test_empty_api_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sandbox().validate_request_boundary("alpaca", "", ALPACA_PAPER)

    def test_whitespace_api_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sandbox().validate_request_boundary("alpaca", "   ", ALPACA_PAPER)

    def test_empty_broker_name_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sandbox().validate_request_boundary("", "PK_1", ALPACA_PAPER)

    def test_non_string_target_url_is_rejected(self):
        with self.assertRaises(ValueError):
            self.sandbox().validate_request_boundary("alpaca", "PK_1", None)

    def test_non_enum_environment_is_rejected(self):
        with self.assertRaises(TypeError):
            CredentialEnvironmentGuard("SANDBOX")


class TestRuleConstruction(GuardTestCase):

    def test_endpoint_shorthand_is_parsed_into_host_and_path(self):
        rule = EndpointRule.parse("gateway.saxobank.com/sim/openapi")
        self.assertEqual(rule.host, "gateway.saxobank.com")
        self.assertEqual(rule.path_prefix, "/sim/openapi")

    def test_endpoint_without_path_matches_any_path(self):
        rule = EndpointRule.parse("api.alpaca.markets")
        self.assertTrue(rule.matches("api.alpaca.markets", "/v2/orders"))
        self.assertFalse(rule.matches("other.example", "/v2/orders"))

    def test_endpoint_prefix_requires_segment_boundary(self):
        rule = EndpointRule.parse("gateway.saxobank.com/sim")
        self.assertTrue(rule.matches("gateway.saxobank.com", "/sim"))
        self.assertTrue(rule.matches("gateway.saxobank.com", "/sim/openapi"))
        self.assertFalse(rule.matches("gateway.saxobank.com", "/simulator"))

    def test_endpoint_spec_with_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            EndpointRule.parse("https://api.alpaca.markets")

    def test_rules_without_endpoints_are_rejected(self):
        with self.assertRaises(ValueError):
            BrokerEnvironmentRules(broker_name="ghost")

    def test_custom_rules_replace_defaults(self):
        rules = {
            "myvenue": BrokerEnvironmentRules(
                broker_name="MyVenue",
                sandbox_endpoints=["sim.myvenue.example"],
                production_endpoints=["api.myvenue.example"],
            )
        }
        guard = CredentialEnvironmentGuard(TradingEnvironment.SANDBOX, custom_rules=rules)
        self.assertTrue(guard.validate_request_boundary(
            "MyVenue", "tok", "https://sim.myvenue.example/orders"))
        with self.assertRaises(SecurityViolationError):
            guard.validate_request_boundary("MyVenue", "tok", "https://api.myvenue.example/orders")
        # Defaults are replaced, not merged, so alpaca is now unknown.
        with self.assertRaises(SecurityViolationError):
            guard.validate_request_boundary("alpaca", "PK_1", ALPACA_PAPER)

    def test_broker_name_is_normalised_to_lowercase(self):
        rule = BrokerEnvironmentRules(
            broker_name="  Alpaca  ", production_endpoints=["api.alpaca.markets"])
        self.assertEqual(rule.broker_name, "alpaca")

    def test_iter_declared_endpoints_covers_shipped_rules(self):
        rows = list(iter_declared_endpoints())
        self.assertIn(("alpaca", "PRODUCTION", "api.alpaca.markets"), rows)
        self.assertIn(("saxo", "SANDBOX", "gateway.saxobank.com/sim/openapi"), rows)
        self.assertEqual(len(rows), sum(
            len(r.sandbox_endpoints) + len(r.production_endpoints) for r in BROKER_RULES.values()))


if __name__ == "__main__":
    unittest.main()
