"""
air-gapped-signing-workflow-for-cold-storage: Enforces physical separation between
transaction coordination (online) and private key signing (offline/air-gapped).
"""
import dataclasses
import hashlib
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class UnsignedPayload:
    destination_address: str
    amount: float
    network: str
    nonce: int
    
    def to_qr_code_data(self) -> str:
        """Simulates exporting data via QR Code to cross the air gap."""
        return json.dumps(dataclasses.asdict(self))


@dataclasses.dataclass
class SignedPayload:
    original_payload_hash: str
    signature: str
    signed_by_offline_vault: bool


class OfflineAirGappedSigner:
    """
    Represents the secure offline vault. It has NO internet connection.
    It holds the private key and performs 'Clear Signing' verification.
    """
    def __init__(self, vault_private_key: str):
        self._private_key = vault_private_key

    def _verify_clear_signing(self, payload: UnsignedPayload) -> bool:
        """
        Simulates displaying the transaction on a trusted hardware screen.
        In an institutional setup, a human or strict policy checks this.
        """
        if payload.amount <= 0:
            logger.error("Vault rejected: Invalid amount.")
            return False
        if not payload.destination_address.startswith("0x"):
            logger.error("Vault rejected: Invalid destination format.")
            return False
        return True

    def sign_qr_payload(self, qr_data: str) -> Optional[SignedPayload]:
        """
        Reads the payload via camera (QR) or SD Card, verifies it, and signs.
        """
        try:
            data_dict = json.loads(qr_data)
            payload = UnsignedPayload(**data_dict)
        except Exception:
            logger.error("Vault rejected: Malformed QR data.")
            return None

        if not self._verify_clear_signing(payload):
            return None

        # Generate a deterministic hash of the payload
        payload_string = f"{payload.destination_address}:{payload.amount}:{payload.network}:{payload.nonce}"
        payload_hash = hashlib.sha256(payload_string.encode()).hexdigest()

        # Simulate cryptographic signing using the isolated private key
        signature = hashlib.sha256(f"{payload_hash}:{self._private_key}".encode()).hexdigest()
        
        logger.info(f"Vault securely signed payload for {payload.amount} to {payload.destination_address}")
        
        return SignedPayload(
            original_payload_hash=payload_hash,
            signature=signature,
            signed_by_offline_vault=True
        )


class OnlineCoordinator:
    """
    Represents the internet-connected trading server that builds transactions
    and broadcasts them, but NEVER holds the private key.
    """
    def __init__(self):
        self.nonce_counter = 0

    def create_unsigned_transfer(self, address: str, amount: float) -> UnsignedPayload:
        self.nonce_counter += 1
        return UnsignedPayload(
            destination_address=address,
            amount=amount,
            network="ETH",
            nonce=self.nonce_counter
        )

    def broadcast_to_network(self, signed_payload: SignedPayload) -> bool:
        """
        Verifies the payload is signed by the vault before broadcasting to the blockchain.
        """
        if not signed_payload or not signed_payload.signed_by_offline_vault:
            logger.error("Broadcast failed: Missing or invalid offline signature.")
            return False
            
        logger.info(f"Broadcasting transaction with signature {signed_payload.signature[:8]}...")
        return True
