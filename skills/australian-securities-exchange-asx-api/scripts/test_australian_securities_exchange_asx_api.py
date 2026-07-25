import unittest
from australian_securities_exchange_asx_api import (
    AsxConnectionConfig,
    AsxIntegrationEngine,
    AsxProtocol,
    AsxConnectionState
)

class TestAsxIntegrationEngine(unittest.TestCase):
    
    def test_valid_fix_connection(self):
        config = AsxConnectionConfig(
            host="fix.cde.asx.com.au",
            port=443,
            comp_id="TESTCOMP1",
            protocol=AsxProtocol.FIX_5_0_SP2,
            is_alc_colocated=False,
            is_cde_environment=True
        )
        engine = AsxIntegrationEngine(config)
        self.assertEqual(engine.state, AsxConnectionState.DISCONNECTED)
        
        res = engine.connect()
        self.assertTrue(res)
        self.assertEqual(engine.state, AsxConnectionState.CONNECTED)
        
        engine.disconnect()
        self.assertEqual(engine.state, AsxConnectionState.DISCONNECTED)

    def test_invalid_ouch_topology(self):
        # Attempting to use OUCH without being in the ALC should raise a ValueError
        config = AsxConnectionConfig(
            host="ouch.cde.asx.com.au",
            port=12345,
            comp_id="HFTCOMP",
            protocol=AsxProtocol.OUCH,
            is_alc_colocated=False, # This triggers the failure
            is_cde_environment=True
        )
        with self.assertRaises(ValueError):
            AsxIntegrationEngine(config)

    def test_valid_itch_topology(self):
        # ITCH inside ALC is valid
        config = AsxConnectionConfig(
            host="itch.prod.asx.com.au",
            port=54321,
            comp_id="MKT_DATA",
            protocol=AsxProtocol.ITCH,
            is_alc_colocated=True,
            is_cde_environment=False
        )
        engine = AsxIntegrationEngine(config) # Should not raise
        self.assertEqual(engine.config.protocol, AsxProtocol.ITCH)

if __name__ == '__main__':
    unittest.main()
