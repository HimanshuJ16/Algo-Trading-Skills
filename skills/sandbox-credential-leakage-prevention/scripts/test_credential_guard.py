"""
Unit tests for sandbox-credential-leakage-prevention skill.
"""
import unittest
from credential_guard import CredentialEnvironmentGuard, SecurityViolationError, TradingEnvironment


class TestCredentialEnvironmentGuard(unittest.TestCase):

    def test_valid_sandbox_request(self):
        guard = CredentialEnvironmentGuard(TradingEnvironment.SANDBOX)
        # Valid Alpaca paper key to paper URL
        self.assertTrue(guard.validate_request_boundary(
            broker_name="alpaca",
            api_key="PK_MOCK_123456",
            target_url="https://paper-api.alpaca.markets/v2/orders",
        ))

    def test_sandbox_key_calling_production_url_fails(self):
        guard = CredentialEnvironmentGuard(TradingEnvironment.SANDBOX)

        # Sandbox mode calling live URL -> SecurityViolationError
        with self.assertRaises(SecurityViolationError) as ctx:
            guard.validate_request_boundary(
                broker_name="alpaca",
                api_key="PK_MOCK_123456",
                target_url="https://api.alpaca.markets/v2/orders",
            )
        self.assertIn("ENDPOINT LEAK DETECTED", str(ctx.exception))

    def test_production_mode_with_sandbox_key_fails(self):
        guard = CredentialEnvironmentGuard(TradingEnvironment.PRODUCTION)

        # Production mode using paper key -> SecurityViolationError
        with self.assertRaises(SecurityViolationError) as ctx:
            guard.validate_request_boundary(
                broker_name="alpaca",
                api_key="PK_MOCK_123456",
                target_url="https://api.alpaca.markets/v2/orders",
            )
        self.assertIn("CREDENTIAL LEAK DETECTED", str(ctx.exception))

    def test_valid_production_request(self):
        guard = CredentialEnvironmentGuard(TradingEnvironment.PRODUCTION)
        self.assertTrue(guard.validate_request_boundary(
            broker_name="alpaca",
            api_key="AK_LIVE_998877",
            target_url="https://api.alpaca.markets/v2/orders",
        ))


if __name__ == "__main__":
    unittest.main()
