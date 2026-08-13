"""Reference air-gapped signing workflow for an Ethereum-style custody payload.

This module models custody boundaries and verification behavior for education and
unit testing. Its deterministic signature primitive is not a replacement for an
audited wallet, HSM, or chain-native cryptographic implementation.
"""
import dataclasses
import hashlib
import hmac
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)
_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SUPPORTED_NETWORK = "ETH"
_MAX_NONCE = 2**63 - 1


@dataclasses.dataclass(frozen=True)
class UnsignedPayload:
    destination_address: str
    amount: str
    network: str
    nonce: int
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.destination_address, str) or not _ADDRESS_RE.fullmatch(self.destination_address):
            raise ValueError("destination_address must be a 20-byte hexadecimal address")
        if self.network != _SUPPORTED_NETWORK:
            raise ValueError("unsupported network")
        if self.version != 1:
            raise ValueError("unsupported payload version")
        if not isinstance(self.nonce, int) or isinstance(self.nonce, bool) or not 1 <= self.nonce <= _MAX_NONCE:
            raise ValueError("nonce must be a positive bounded integer")
        try:
            value = Decimal(self.amount)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("amount must be a decimal string") from exc
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -18:
            raise ValueError("amount must be finite, positive, and use at most 18 decimals")
        object.__setattr__(self, "amount", format(value, "f"))

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def to_qr_code_data(self) -> str:
        """Serialize the exact intent deterministically for QR/SD transfer."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    @classmethod
    def from_qr_code_data(cls, qr_data: str) -> "UnsignedPayload":
        if not isinstance(qr_data, str):
            raise ValueError("QR data must be text")
        try:
            data = json.loads(qr_data)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("malformed QR data") from exc
        if not isinstance(data, dict) or set(data) != {"amount", "destination_address", "network", "nonce", "version"}:
            raise ValueError("payload fields are invalid")
        return cls(**data)

    def payload_hash(self) -> str:
        return hashlib.sha256(self.to_qr_code_data().encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class SignedPayload:
    unsigned_payload: str
    original_payload_hash: str
    signature: str
    signer_key_id: str


class OfflineAirGappedSigner:
    """Offline vault boundary; no network connection is modeled or permitted."""

    def __init__(self, vault_private_key: str, key_id: str = "vault-primary"):
        if not isinstance(vault_private_key, str) or not vault_private_key:
            raise ValueError("vault_private_key is required")
        self._private_key = vault_private_key
        self.key_id = key_id
        # Public token is only a test seam; production systems must use audited public-key crypto.
        self.verification_key = hashlib.sha256(vault_private_key.encode("utf-8")).hexdigest()

    def _verify_clear_signing(self, payload: UnsignedPayload) -> bool:
        """Apply the trusted display/policy gate before signing."""
        try:
            Decimal(payload.amount)
            valid = (
                payload.network == _SUPPORTED_NETWORK
                and _ADDRESS_RE.fullmatch(payload.destination_address) is not None
                and Decimal(payload.amount) > 0
                and 1 <= payload.nonce <= _MAX_NONCE
            )
        except (InvalidOperation, TypeError, ValueError):
            valid = False
        if not valid:
            logger.error("Vault rejected clear-signing policy for nonce=%s", getattr(payload, "nonce", None))
        return valid

    def sign_qr_payload(self, qr_data: str) -> Optional[SignedPayload]:
        try:
            payload = UnsignedPayload.from_qr_code_data(qr_data)
        except ValueError:
            logger.error("Vault rejected malformed QR data")
            return None
        if not self._verify_clear_signing(payload):
            return None
        canonical = payload.to_qr_code_data()
        payload_hash = payload.payload_hash()
        signature = hmac.new(self.verification_key.encode("ascii"), payload_hash.encode("ascii"), hashlib.sha256).hexdigest()
        logger.info("Vault signed approved payload nonce=%s", payload.nonce)
        return SignedPayload(canonical, payload_hash, signature, self.key_id)


class OnlineCoordinator:
    """Online intent and broadcast boundary; never stores the vault private key."""

    def __init__(self, verification_key: Optional[str] = None, signer_key_id: str = "vault-primary", starting_nonce: int = 0):
        self.nonce_counter = starting_nonce
        self.verification_key = verification_key
        self.signer_key_id = signer_key_id
        self._issued: Dict[str, UnsignedPayload] = {}
        self._broadcast: set[str] = set()

    def create_unsigned_transfer(self, address: str, amount: Any) -> UnsignedPayload:
        self.nonce_counter += 1
        payload = UnsignedPayload(address, str(amount), _SUPPORTED_NETWORK, self.nonce_counter)
        self._issued[payload.payload_hash()] = payload
        return payload

    def broadcast_to_network(self, signed_payload: Optional[SignedPayload]) -> bool:
        if signed_payload is None or not self.verification_key:
            logger.error("Broadcast failed: missing verification context")
            return False
        try:
            payload = UnsignedPayload.from_qr_code_data(signed_payload.unsigned_payload)
        except ValueError:
            logger.error("Broadcast failed: malformed signed payload")
            return False
        payload_hash = payload.payload_hash()
        if payload_hash != signed_payload.original_payload_hash or payload_hash not in self._issued:
            logger.error("Broadcast failed: payload binding mismatch")
            return False
        if signed_payload.signer_key_id != self.signer_key_id or payload_hash in self._broadcast:
            logger.error("Broadcast failed: unknown signer or replayed payload")
            return False
        expected = hmac.new(self.verification_key.encode("ascii"), payload_hash.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signed_payload.signature):
            logger.error("Broadcast failed: signature verification failed")
            return False
        self._broadcast.add(payload_hash)
        logger.info("Broadcast accepted for payload nonce=%s", payload.nonce)
        return True
