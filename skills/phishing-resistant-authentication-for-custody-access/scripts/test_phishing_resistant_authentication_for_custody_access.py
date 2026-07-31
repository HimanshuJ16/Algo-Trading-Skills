import unittest
import time
from phishing_resistant_authentication_for_custody_access import (
    PhishingResistantAuthenticationForCustodyAccessConfig,
    PhishingResistantAuthenticationForCustodyAccessEngine,
    WebAuthnAssertion,
    AuthPolicyConfig,
    AuthVerificationReport
)

class TestPhishingResistantAuthenticationForCustodyAccess(unittest.TestCase):

    def test_execute_true_legacy(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(enabled=True)
        )
        self.assertTrue(engine.execute())

    def test_execute_false_legacy(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine(
            PhishingResistantAuthenticationForCustodyAccessConfig(enabled=False)
        )
        self.assertFalse(engine.execute())

    def test_valid_webauthn_authentication(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine()
        assertion = WebAuthnAssertion(
            user_id="custody_admin_1",
            client_origin="https://custody.firm.com",
            rp_id="custody.firm.com",
            challenge="abc123challenge",
            user_present=True,
            user_verified=True
        )
        report = engine.verify_assertion(assertion)
        self.assertEqual(report.status, "AUTH_SUCCESSFUL")
        self.assertTrue(report.is_origin_valid)
        self.assertTrue(report.is_user_verified)

    def test_origin_mismatch_phishing_rejection(self):
        engine = PhishingResistantAuthenticationForCustodyAccessEngine()
        # Simulated Evilginx2 phished domain origin
        assertion = WebAuthnAssertion(
            user_id="custody_admin_1",
            client_origin="https://cust0dy-firm.com",
            rp_id="custody.firm.com",
            challenge="abc123challenge",
            user_present=True,
            user_verified=True
        )
        report = engine.verify_assertion(assertion)
        self.assertEqual(report.status, "ORIGIN_MISMATCH_PHISHING_ATTEMPT")
        self.assertFalse(report.is_origin_valid)

if __name__ == '__main__':
    unittest.main()
