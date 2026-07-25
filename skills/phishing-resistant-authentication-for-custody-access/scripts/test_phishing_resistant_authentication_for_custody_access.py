import unittest
from phishing_resistant_authentication_for_custody_access import PhishingResistantAuthenticationForCustodyAccessConfig, PhishingResistantAuthenticationForCustodyAccessEngine

class TestPhishingResistantAuthenticationForCustodyAccess(unittest.TestCase):
    def test_execute_true(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(PhishingResistantAuthenticationForCustodyAccessConfig(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(PhishingResistantAuthenticationForCustodyAccessConfig(enabled=False))
        self.assertFalse(engine.execute())

if __name__ == '__main__':
    unittest.main()
