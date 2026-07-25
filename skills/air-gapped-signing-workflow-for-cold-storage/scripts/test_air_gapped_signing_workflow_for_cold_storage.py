import unittest
from air_gapped_signing_workflow_for_cold_storage import (
    OnlineCoordinator, 
    OfflineAirGappedSigner, 
    UnsignedPayload
)

class TestAirGappedSigningWorkflow(unittest.TestCase):
    def setUp(self):
        self.coordinator = OnlineCoordinator()
        self.vault = OfflineAirGappedSigner(vault_private_key="super_secret_air_gapped_key")

    def test_successful_air_gapped_workflow(self):
        # 1. Coordinator creates unsigned payload (Online)
        unsigned = self.coordinator.create_unsigned_transfer("0xABC123", 50.0)
        
        # 2. Transfer via QR code (Air Gap)
        qr_data = unsigned.to_qr_code_data()
        
        # 3. Vault scans QR, verifies clear signing, and signs (Offline)
        signed = self.vault.sign_qr_payload(qr_data)
        self.assertIsNotNone(signed)
        self.assertTrue(signed.signed_by_offline_vault)
        
        # 4. Coordinator receives signed payload and broadcasts (Online)
        success = self.coordinator.broadcast_to_network(signed)
        self.assertTrue(success)

    def test_vault_rejects_blind_signing_tamper(self):
        # Attacker tampers with the destination address in the online environment
        unsigned = self.coordinator.create_unsigned_transfer("MALICIOUS_ADDRESS", 1000.0)
        qr_data = unsigned.to_qr_code_data()
        
        # Vault should reject it during "Clear Signing" display step (missing 0x format)
        signed = self.vault.sign_qr_payload(qr_data)
        self.assertIsNone(signed)
        
        # Broadcast fails
        success = self.coordinator.broadcast_to_network(signed)
        self.assertFalse(success)

if __name__ == '__main__':
    unittest.main()
