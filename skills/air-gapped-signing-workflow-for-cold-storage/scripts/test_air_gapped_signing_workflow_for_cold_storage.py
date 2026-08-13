import json
import unittest

from air_gapped_signing_workflow_for_cold_storage import (
    OfflineAirGappedSigner,
    OnlineCoordinator,
    SignedPayload,
    UnsignedPayload,
)


ADDRESS = "0x" + "a" * 40


class TestAirGappedSigningWorkflow(unittest.TestCase):
    def setUp(self):
        self.vault = OfflineAirGappedSigner("super_secret_air_gapped_key")
        self.coordinator = OnlineCoordinator(self.vault.verification_key)

    def test_successful_air_gapped_workflow_and_replay_rejection(self):
        unsigned = self.coordinator.create_unsigned_transfer(ADDRESS, "50.000000000000000001")
        signed = self.vault.sign_qr_payload(unsigned.to_qr_code_data())
        self.assertIsNotNone(signed)
        self.assertTrue(self.coordinator.broadcast_to_network(signed))
        self.assertFalse(self.coordinator.broadcast_to_network(signed))

    def test_canonical_serialization_and_hash_are_stable(self):
        payload = UnsignedPayload(ADDRESS, "1.20", "ETH", 1)
        self.assertEqual(payload.to_qr_code_data(), '{"amount":"1.20","destination_address":"' + ADDRESS + '","network":"ETH","nonce":1,"version":1}')
        self.assertEqual(payload, UnsignedPayload.from_qr_code_data(payload.to_qr_code_data()))
        self.assertEqual(payload.payload_hash(), UnsignedPayload.from_qr_code_data(payload.to_qr_code_data()).payload_hash())

    def test_vault_rejects_invalid_payloads(self):
        for address, amount in [("MALICIOUS_ADDRESS", "1000"), ("0x123", "1"), (ADDRESS, "0"), (ADDRESS, "NaN")]:
            with self.subTest(address=address, amount=amount):
                self.assertIsNone(self.vault.sign_qr_payload(json.dumps({"destination_address": address, "amount": amount, "network": "ETH", "nonce": 1, "version": 1})))

    def test_malformed_and_extra_fields_are_rejected(self):
        self.assertIsNone(self.vault.sign_qr_payload("not-json"))
        data = json.loads(UnsignedPayload(ADDRESS, "1", "ETH", 1).to_qr_code_data())
        data["unexpected"] = True
        self.assertIsNone(self.vault.sign_qr_payload(json.dumps(data)))

    def test_tampered_signature_and_payload_are_rejected(self):
        unsigned = self.coordinator.create_unsigned_transfer(ADDRESS, "5")
        signed = self.vault.sign_qr_payload(unsigned.to_qr_code_data())
        tampered = SignedPayload(signed.unsigned_payload, signed.original_payload_hash, "0" * 64, signed.signer_key_id)
        self.assertFalse(self.coordinator.broadcast_to_network(tampered))
        changed = json.loads(signed.unsigned_payload)
        changed["amount"] = "6"
        mismatched = SignedPayload(json.dumps(changed), signed.original_payload_hash, signed.signature, signed.signer_key_id)
        self.assertFalse(self.coordinator.broadcast_to_network(mismatched))

    def test_unknown_payload_and_forged_signer_are_rejected(self):
        other = OfflineAirGappedSigner("other-key")
        payload = UnsignedPayload(ADDRESS, "3", "ETH", 99)
        signed = other.sign_qr_payload(payload.to_qr_code_data())
        self.assertFalse(self.coordinator.broadcast_to_network(signed))

    def test_network_nonce_and_version_validation(self):
        for kwargs in (
            {"network": "BTC"},
            {"nonce": 0},
            {"version": 2},
        ):
            values = {"destination_address": ADDRESS, "amount": "1", "network": "ETH", "nonce": 1, "version": 1}
            values.update(kwargs)
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    UnsignedPayload(**values)


if __name__ == "__main__":
    unittest.main()
