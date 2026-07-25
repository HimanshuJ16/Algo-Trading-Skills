import unittest
from vault_secrets_manager import VaultSecretsManager

class TestVaultSecretsManager(unittest.TestCase):

    def setUp(self):
        self.manager = VaultSecretsManager(vault_addr="https://vault.internal:8200", environment="prod")
        # Inject some mock data
        self.manager._inject_mock_secret("prod/binance/api_keys", {"key": "PROD_KEY", "secret": "PROD_SEC"})
        self.manager._inject_mock_secret("dev/binance/api_keys", {"key": "DEV_KEY", "secret": "DEV_SEC"})

    def test_unauthenticated_access_denied(self):
        with self.assertRaises(PermissionError):
            self.manager.get_secret("prod/binance/api_keys")

    def test_approle_login(self):
        self.manager.login_approle("role_123", "secret_456")
        self.assertTrue(self.manager.is_authenticated)
        self.assertIsNotNone(self.manager.client_token)

    def test_successful_secret_retrieval_and_caching(self):
        self.manager.login_approle("role_123", "secret_456")
        
        # First read (cache miss)
        keys = self.manager.get_secret("prod/binance/api_keys")
        self.assertEqual(keys["key"], "PROD_KEY")
        
        # Second read (cache hit)
        # We can verify it hits the cache by deleting it from the backend
        del self.manager._mock_vault_backend["prod/binance/api_keys"]
        keys_cached = self.manager.get_secret("prod/binance/api_keys")
        self.assertEqual(keys_cached["secret"], "PROD_SEC")

    def test_environment_isolation_security(self):
        self.manager.login_approle("role_123", "secret_456")
        
        # A bot running in 'prod' environment tries to read 'dev' keys
        with self.assertRaises(PermissionError) as context:
            self.manager.get_secret("dev/binance/api_keys")
            
        self.assertIn("Environment mismatch", str(context.exception))

if __name__ == '__main__':
    unittest.main()
