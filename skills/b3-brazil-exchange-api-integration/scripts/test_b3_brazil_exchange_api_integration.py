import unittest
from b3_brazil_exchange_api_integration import (
    B3ConnectionConfig,
    B3IntegrationEngine,
    B3ProtocolSuite,
    B3ConnectionState
)

class TestB3IntegrationEngine(unittest.TestCase):
    
    def test_valid_legacy_connection(self):
        # Legacy FIX/FAST usually relies on standard TCP or has a native recovery feed
        config = B3ConnectionConfig(
            comp_id="B3_TRADER_1",
            password="secret_cert",
            order_entry_ip="192.168.1.10",
            market_data_multicast_ip="239.1.1.1",
            protocol_suite=B3ProtocolSuite.LEGACY_FIX_FAST,
            enable_application_gap_recovery=False
        )
        engine = B3IntegrationEngine(config)
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)
        
        res = engine.connect()
        self.assertTrue(res)
        self.assertEqual(engine.state, B3ConnectionState.CONNECTED)
        
        engine.disconnect()
        self.assertEqual(engine.state, B3ConnectionState.DISCONNECTED)

    def test_invalid_modern_sbe_configuration(self):
        # Modern SBE via UDP without application gap recovery must fail validation
        config = B3ConnectionConfig(
            comp_id="B3_HFT_1",
            password="secret_cert",
            order_entry_ip="192.168.1.20",
            market_data_multicast_ip="239.2.2.2",
            protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
            enable_application_gap_recovery=False # This triggers the failure
        )
        with self.assertRaises(ValueError):
            B3IntegrationEngine(config)

    def test_valid_modern_sbe_configuration(self):
        # Modern SBE with gap recovery is valid
        config = B3ConnectionConfig(
            comp_id="B3_HFT_1",
            password="secret_cert",
            order_entry_ip="192.168.1.20",
            market_data_multicast_ip="239.2.2.2",
            protocol_suite=B3ProtocolSuite.MODERN_BINARY_SBE,
            enable_application_gap_recovery=True # Correctly enabled
        )
        engine = B3IntegrationEngine(config)
        self.assertEqual(engine.config.protocol_suite, B3ProtocolSuite.MODERN_BINARY_SBE)

if __name__ == '__main__':
    unittest.main()
