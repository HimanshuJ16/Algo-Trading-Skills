import unittest
from binance_futures_testnet_to_mainnet_promotion import (
    ExchangeConfig,
    Environment,
    MainnetPromotionManager,
    PromotionError
)

class TestMainnetPromotionManager(unittest.TestCase):
    def setUp(self):
        self.testnet_config = ExchangeConfig(
            api_key="test_key",
            api_secret="test_secret",
            base_url="https://testnet.binancefuture.com",
            environment=Environment.TESTNET
        )
        self.mainnet_config = ExchangeConfig(
            api_key="main_key",
            api_secret="main_secret",
            base_url="https://fapi.binance.com",
            environment=Environment.MAINNET
        )
        self.manager = MainnetPromotionManager(self.testnet_config, self.mainnet_config)
        self.valid_strategy = {
            "leverage": 3,
            "capital_risk_pct": 0.01,
            "hard_stop_loss_enabled": True
        }

    def test_initialization_invalid_env(self):
        with self.assertRaises(ValueError):
            MainnetPromotionManager(self.mainnet_config, self.mainnet_config)

    def test_verify_api_connectivity(self):
        self.assertTrue(self.manager.verify_api_connectivity(self.mainnet_config))
        
        invalid_config = ExchangeConfig("key", "secret", "http://fapi.binance.com", Environment.MAINNET)
        self.assertFalse(self.manager.verify_api_connectivity(invalid_config))
        
        missing_key_config = ExchangeConfig("", "secret", "https://fapi.binance.com", Environment.MAINNET)
        self.assertFalse(self.manager.verify_api_connectivity(missing_key_config))

    def test_validate_risk_parameters(self):
        self.assertTrue(self.manager.validate_risk_parameters(self.valid_strategy))
        
        invalid_leverage = dict(self.valid_strategy, leverage=10)
        self.assertFalse(self.manager.validate_risk_parameters(invalid_leverage))
        
        invalid_risk = dict(self.valid_strategy, capital_risk_pct=0.05)
        self.assertFalse(self.manager.validate_risk_parameters(invalid_risk))
        
        no_stop_loss = dict(self.valid_strategy, hard_stop_loss_enabled=False)
        self.assertFalse(self.manager.validate_risk_parameters(no_stop_loss))

    def test_promote_to_mainnet_success(self):
        active_config = self.manager.promote_to_mainnet(self.valid_strategy)
        self.assertEqual(active_config.environment, Environment.MAINNET)
        self.assertTrue(self.manager.is_promoted)
        
        # Test idempotency
        active_config = self.manager.promote_to_mainnet(self.valid_strategy)
        self.assertEqual(active_config.environment, Environment.MAINNET)

    def test_promote_to_mainnet_failure(self):
        invalid_strategy = dict(self.valid_strategy, leverage=20)
        with self.assertRaises(PromotionError):
            self.manager.promote_to_mainnet(invalid_strategy)
        self.assertFalse(self.manager.is_promoted)

if __name__ == '__main__':
    unittest.main()
