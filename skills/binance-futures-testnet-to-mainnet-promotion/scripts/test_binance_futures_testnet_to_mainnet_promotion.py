import unittest

from binance_futures_testnet_to_mainnet_promotion import (
    DEFAULT_MAINNET_HOSTS,
    DEFAULT_TESTNET_HOSTS,
    Environment,
    ExchangeConfig,
    MainnetPromotionManager,
    PromotionError,
)


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.testnet_config = ExchangeConfig(
            api_key="test_key",
            api_secret="test_secret",
            base_url="https://testnet.binancefuture.com",
            environment=Environment.TESTNET,
        )
        self.mainnet_config = ExchangeConfig(
            api_key="main_key",
            api_secret="main_secret",
            base_url="https://fapi.binance.com",
            environment=Environment.MAINNET,
        )
        self.manager = MainnetPromotionManager(
            self.testnet_config, self.mainnet_config, allow_live_promotion=True
        )
        self.valid_strategy = {
            "leverage": 3,
            "capital_risk_pct": 0.01,
            "hard_stop_loss_enabled": True,
        }


class TestConstruction(BaseCase):
    def test_initialization_invalid_env(self):
        with self.assertRaises(ValueError):
            MainnetPromotionManager(self.mainnet_config, self.mainnet_config)

    def test_shared_api_key_across_environments_rejected(self):
        """Binance issues testnet and mainnet keys separately; a shared key means one
        leg is misconfigured — possibly running 'testnet' against real capital."""
        shared = ExchangeConfig(
            "main_key", "other_secret", "https://testnet.binancefuture.com",
            Environment.TESTNET,
        )
        with self.assertRaises(ValueError):
            MainnetPromotionManager(shared, self.mainnet_config)

    def test_shared_api_secret_across_environments_rejected(self):
        shared = ExchangeConfig(
            "other_key", "main_secret", "https://testnet.binancefuture.com",
            Environment.TESTNET,
        )
        with self.assertRaises(ValueError):
            MainnetPromotionManager(shared, self.mainnet_config)

    def test_invalid_risk_ceilings_rejected(self):
        for kwargs in (
            {"max_leverage_limit": 0},
            {"max_leverage_limit": 2.5},
            {"max_capital_risk_pct": 0},
            {"max_capital_risk_pct": 2},          # 2 == 200%, the "percent vs fraction" slip
            {"max_capital_risk_pct": float("nan")},
            {"max_capital_risk_pct": "0.02"},     # string from an unparsed env var
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    MainnetPromotionManager(
                        self.testnet_config, self.mainnet_config, **kwargs
                    )


class TestCredentialRedaction(BaseCase):
    def test_repr_never_discloses_secret(self):
        rendered = repr(self.mainnet_config)
        self.assertNotIn("main_secret", rendered)
        self.assertNotIn("main_key", rendered)
        self.assertIn("fapi.binance.com", rendered)

    def test_str_never_discloses_secret(self):
        self.assertNotIn("main_secret", str(self.mainnet_config))

    def test_repr_inside_container_never_discloses_secret(self):
        """Containers call repr() on their members — the common accidental-leak path."""
        self.assertNotIn("main_secret", repr({"cfg": self.mainnet_config}))


class TestConnectivityVerification(BaseCase):
    def test_valid_configs_pass(self):
        self.assertTrue(self.manager.verify_api_connectivity(self.mainnet_config))
        self.assertTrue(self.manager.verify_api_connectivity(self.testnet_config))

    def test_plain_http_rejected(self):
        cfg = ExchangeConfig("key", "secret", "http://fapi.binance.com", Environment.MAINNET)
        self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_missing_credentials_rejected(self):
        cfg = ExchangeConfig("", "secret", "https://fapi.binance.com", Environment.MAINNET)
        self.assertFalse(self.manager.verify_api_connectivity(cfg))
        cfg = ExchangeConfig("key", "", "https://fapi.binance.com", Environment.MAINNET)
        self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_testnet_config_pointing_at_mainnet_host_rejected(self):
        """Regression: the pre-fix implementation accepted any https:// URL, so a
        TESTNET-labelled config aimed at fapi.binance.com passed and every
        'paper' order it justified was in fact a real leveraged order."""
        cfg = ExchangeConfig(
            "key", "secret", "https://fapi.binance.com", Environment.TESTNET
        )
        self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_mainnet_config_pointing_at_testnet_host_rejected(self):
        cfg = ExchangeConfig(
            "key", "secret", "https://testnet.binancefuture.com", Environment.MAINNET
        )
        self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_lookalike_host_suffix_rejected(self):
        """Exact-host match, not startswith/endswith: a suffix comparison would
        accept an attacker-controlled domain that merely contains the real host."""
        for url in (
            "https://fapi.binance.com.attacker.example",
            "https://evil-fapi.binance.com.co",
            "https://attacker.example/fapi.binance.com",
            "https://user:pw@attacker.example",
        ):
            with self.subTest(url=url):
                cfg = ExchangeConfig("key", "secret", url, Environment.MAINNET)
                self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_host_matching_is_case_insensitive_and_port_tolerant(self):
        cfg = ExchangeConfig(
            "key", "secret", "https://FAPI.Binance.COM:443/fapi/v1", Environment.MAINNET
        )
        self.assertTrue(self.manager.verify_api_connectivity(cfg))

    def test_malformed_base_url_rejected(self):
        for url in ("", "   ", "fapi.binance.com", "not a url", "https://"):
            with self.subTest(url=url):
                cfg = ExchangeConfig("key", "secret", url, Environment.MAINNET)
                self.assertFalse(self.manager.verify_api_connectivity(cfg))

    def test_coin_margined_hosts_recognised(self):
        self.assertIn("dapi.binance.com", DEFAULT_MAINNET_HOSTS)
        self.assertIn("demo-dapi.binance.com", DEFAULT_TESTNET_HOSTS)

    def test_custom_host_allowlist_is_honoured(self):
        manager = MainnetPromotionManager(
            self.testnet_config,
            self.mainnet_config,
            allow_live_promotion=True,
            mainnet_hosts=frozenset({"papi.binance.com"}),
        )
        self.assertFalse(manager.verify_api_connectivity(self.mainnet_config))
        cfg = ExchangeConfig("k", "s", "https://papi.binance.com", Environment.MAINNET)
        self.assertTrue(manager.verify_api_connectivity(cfg))


class TestRiskParameterValidation(BaseCase):
    def test_valid_parameters_pass(self):
        self.assertTrue(self.manager.validate_risk_parameters(self.valid_strategy))

    def test_leverage_boundary(self):
        """Exactly at the ceiling passes; one step over fails."""
        at_limit = dict(self.valid_strategy, leverage=self.manager.max_leverage_limit)
        self.assertTrue(self.manager.validate_risk_parameters(at_limit))
        over_limit = dict(self.valid_strategy, leverage=self.manager.max_leverage_limit + 1)
        self.assertFalse(self.manager.validate_risk_parameters(over_limit))

    def test_capital_risk_boundary(self):
        at_limit = dict(self.valid_strategy, capital_risk_pct=self.manager.max_capital_risk_pct)
        self.assertTrue(self.manager.validate_risk_parameters(at_limit))
        over_limit = dict(self.valid_strategy, capital_risk_pct=0.0200001)
        self.assertFalse(self.manager.validate_risk_parameters(over_limit))

    def test_missing_required_keys_rejected(self):
        """Regression: the pre-fix implementation defaulted a missing 'leverage' to 0,
        so a params dict with a typo'd key passed the leverage gate outright."""
        for key in ("leverage", "capital_risk_pct", "hard_stop_loss_enabled"):
            with self.subTest(missing=key):
                params = {k: v for k, v in self.valid_strategy.items() if k != key}
                self.assertFalse(self.manager.validate_risk_parameters(params))

    def test_typo_key_does_not_pass(self):
        params = {"levarage": 50, "capital_risk_pct": 0.01, "hard_stop_loss_enabled": True}
        self.assertFalse(self.manager.validate_risk_parameters(params))

    def test_nan_values_rejected(self):
        """Regression: NaN > limit is False, so a naive comparison passes NaN through."""
        self.assertFalse(
            self.manager.validate_risk_parameters(
                dict(self.valid_strategy, capital_risk_pct=float("nan"))
            )
        )
        self.assertFalse(
            self.manager.validate_risk_parameters(
                dict(self.valid_strategy, capital_risk_pct=float("inf"))
            )
        )

    def test_non_numeric_and_non_positive_values_rejected(self):
        for params in (
            dict(self.valid_strategy, leverage="3"),
            dict(self.valid_strategy, leverage=3.5),
            dict(self.valid_strategy, leverage=True),
            dict(self.valid_strategy, leverage=0),
            dict(self.valid_strategy, leverage=-1),
            dict(self.valid_strategy, capital_risk_pct="0.01"),
            dict(self.valid_strategy, capital_risk_pct=0),
            dict(self.valid_strategy, capital_risk_pct=-0.01),
        ):
            with self.subTest(params=params):
                self.assertFalse(self.manager.validate_risk_parameters(params))

    def test_stop_loss_flag_must_be_boolean_true(self):
        """Regression: a truthiness check passes the string 'false' from a naive
        env-var/config loader, disabling the exchange-level stop-loss gate."""
        for value in ("false", "true", 1, "yes", [1]):
            with self.subTest(value=value):
                params = dict(self.valid_strategy, hard_stop_loss_enabled=value)
                self.assertFalse(self.manager.validate_risk_parameters(params))
        self.assertFalse(
            self.manager.validate_risk_parameters(
                dict(self.valid_strategy, hard_stop_loss_enabled=False)
            )
        )


class TestPromotionGate(BaseCase):
    def test_promote_to_mainnet_success(self):
        active_config = self.manager.promote_to_mainnet(self.valid_strategy)
        self.assertEqual(active_config.environment, Environment.MAINNET)
        self.assertTrue(self.manager.is_promoted)

    def test_promotion_blocked_without_explicit_authorization(self):
        """Default-deny: an automated caller must not reach live capital without an
        explicit operator decision."""
        manager = MainnetPromotionManager(self.testnet_config, self.mainnet_config)
        self.assertFalse(manager.allow_live_promotion)
        with self.assertRaises(PromotionError):
            manager.promote_to_mainnet(self.valid_strategy)
        self.assertFalse(manager.is_promoted)

    def test_repeat_promotion_revalidates_new_parameters(self):
        """Regression: the pre-fix implementation short-circuited on the _promoted
        flag and returned the mainnet config for ANY later parameter set, including
        an over-leveraged one."""
        self.manager.promote_to_mainnet(self.valid_strategy)
        self.assertTrue(self.manager.is_promoted)
        over_leveraged = dict(self.valid_strategy, leverage=50)
        with self.assertRaises(PromotionError):
            self.manager.promote_to_mainnet(over_leveraged)

    def test_repeat_promotion_with_same_valid_parameters_is_stable(self):
        first = self.manager.promote_to_mainnet(self.valid_strategy)
        second = self.manager.promote_to_mainnet(self.valid_strategy)
        self.assertIs(first, second)
        self.assertTrue(self.manager.is_promoted)

    def test_promote_to_mainnet_failure_on_leverage(self):
        invalid_strategy = dict(self.valid_strategy, leverage=20)
        with self.assertRaises(PromotionError):
            self.manager.promote_to_mainnet(invalid_strategy)
        self.assertFalse(self.manager.is_promoted)

    def test_pre_flight_rejects_mislabelled_testnet_config(self):
        """The testnet leg is checked too: a testnet config aimed at a mainnet host
        invalidates the very track record being used to justify promotion."""
        bad_testnet = ExchangeConfig(
            "test_key", "test_secret", "https://fapi.binance.com", Environment.TESTNET
        )
        manager = MainnetPromotionManager(
            bad_testnet, self.mainnet_config, allow_live_promotion=True
        )
        self.assertFalse(manager.run_pre_flight_checks(self.valid_strategy))
        with self.assertRaises(PromotionError):
            manager.promote_to_mainnet(self.valid_strategy)


if __name__ == "__main__":
    unittest.main()
