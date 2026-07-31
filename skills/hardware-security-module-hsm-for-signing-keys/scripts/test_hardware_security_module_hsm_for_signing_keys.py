import unittest
from hardware_security_module_hsm_for_signing_keys import (
    HsmSigningManagerEngine, HsmKeyMetaData, HsmSignatureRequest, HsmSigningAuditReport
)

class TestHsmSigningManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HsmSigningManagerEngine()
        self.meta = self.engine.generate_hardware_key("CUSTODY_HOT_01", algorithm="SECP256K1", slot_id=1)

    def test_hsm_key_generation_is_non_exportable(self):
        self.assertEqual(self.meta.key_alias, "CUSTODY_HOT_01")
        self.assertFalse(self.meta.is_extractable)
        self.assertEqual(self.meta.fips_certification, "FIPS_140_2_LEVEL_3")

        # Attempting private key export MUST raise PermissionError
        with self.assertRaises(PermissionError):
            self.engine.attempt_export_private_key("CUSTODY_HOT_01")

    def test_sign_transaction_payload_success(self):
        payload = b"TRANSFER 10.0 BTC TO 3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"
        req = HsmSignatureRequest(
            key_alias="CUSTODY_HOT_01",
            payload_bytes=payload,
            caller_identity="algo_execution_bot_01",
            caller_role="OPERATOR"
        )
        report = self.engine.sign_transaction_payload(req)

        self.assertEqual(report.status, "SIGNATURE_SUCCESS")
        self.assertTrue(report.is_signature_valid)
        self.assertFalse(report.is_key_extractable)
        self.assertGreater(len(report.signature_hex), 0)

    def test_unauthorized_role_denies_signature(self):
        payload = b"TRANSFER 100.0 ETH TO 0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
        req = HsmSignatureRequest(
            key_alias="CUSTODY_HOT_01",
            payload_bytes=payload,
            caller_identity="unauthorized_read_only_auditor",
            caller_role="AUDITOR" # Auditors cannot execute signatures!
        )
        with self.assertRaises(PermissionError):
            self.engine.sign_transaction_payload(req)

if __name__ == '__main__':
    unittest.main()
