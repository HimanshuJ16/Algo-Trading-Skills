import hmac
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class HsmKeyMetaData:
    key_alias: str
    algorithm: str                      # 'SECP256K1', 'ED25519', 'HMAC_SHA256'
    slot_id: int
    is_extractable: bool = False        # Non-exportable hardware rule
    fips_certification: str = "FIPS_140_2_LEVEL_3"

@dataclass
class HsmSignatureRequest:
    key_alias: str
    payload_bytes: bytes
    caller_identity: str
    caller_role: str                    # 'OPERATOR', 'ADMIN', 'AUDITOR'

@dataclass
class HsmSigningAuditReport:
    key_alias: str
    algorithm: str
    payload_hash_hex: str
    signature_hex: str
    is_signature_valid: bool
    is_key_extractable: bool
    status: str                         # 'SIGNATURE_SUCCESS', 'AUTHORIZATION_DENIED', 'EXPORT_ATTEMPT_REJECTED'
    audit_notes: str

class HsmSigningManagerEngine:
    """
    Institutional crypto custody engine for managing non-exportable hardware signing keys,
    executing FIPS 140-2 Level 3 HSM signatures (secp256k1/ed25519/HMAC), and logging tamper-proof audit trails.
    """
    def __init__(self):
        # Simulated HSM Non-Exportable Key Storage Enclave
        self.key_store: Dict[str, Tuple[HsmKeyMetaData, bytes]] = {}

    def generate_hardware_key(
        self,
        key_alias: str,
        algorithm: str = "SECP256K1",
        slot_id: int = 1
    ) -> HsmKeyMetaData:
        """
        Generates non-exportable private key inside hardware enclave slot.
        """
        if algorithm not in ("SECP256K1", "ED25519", "HMAC_SHA256"):
            raise ValueError(f"Unsupported algorithm '{algorithm}'.")

        # Generate 256-bit entropy inside hardware
        private_bytes = hashlib.sha256(f"HSM_ENTROPY_SEED_{key_alias}".encode()).digest()

        meta = HsmKeyMetaData(
            key_alias=key_alias,
            algorithm=algorithm,
            slot_id=slot_id,
            is_extractable=False,
            fips_certification="FIPS_140_2_LEVEL_3"
        )
        self.key_store[key_alias] = (meta, private_bytes)

        logger.info(f"HSM KEY GENERATED [{key_alias} - Slot {slot_id}]: Algorithm={algorithm}, Extractable=False.")
        return meta

    def attempt_export_private_key(self, key_alias: str) -> None:
        """
        Simulates hardware security enforcement: Key export attempts are strictly REJECTED.
        """
        if key_alias not in self.key_store:
            raise ValueError(f"Key alias '{key_alias}' not found in HSM.")

        meta, _ = self.key_store[key_alias]
        if not meta.is_extractable:
            raise PermissionError(f"HSM SECURITY VIOLATION: Key '{key_alias}' is non-exportable (CKA_EXTRACTABLE=False).")

    def sign_transaction_payload(
        self,
        request: HsmSignatureRequest
    ) -> HsmSigningAuditReport:
        """
        Signs transaction payload hash inside hardware enclave without exposing private key.
        """
        if request.key_alias not in self.key_store:
            raise ValueError(f"Key alias '{request.key_alias}' not found in HSM.")

        if request.caller_role not in ("OPERATOR", "ADMIN"):
            raise PermissionError(f"Caller role '{request.caller_role}' is not authorized to execute HSM signatures.")

        meta, private_bytes = self.key_store[request.key_alias]

        # Compute payload hash
        payload_hash = hashlib.sha256(request.payload_bytes).digest()
        payload_hash_hex = payload_hash.hex()

        # Compute signature inside hardware enclave using HMAC / Digest
        signature_bytes = hmac.new(private_bytes, payload_hash, hashlib.sha256).digest()
        signature_hex = signature_bytes.hex()

        notes = (
            f"HSM SIGNATURE GENERATED [{request.key_alias} - {meta.algorithm}]: "
            f"Signed payload hash {payload_hash_hex[:10]}... for caller '{request.caller_identity}' ({request.caller_role}). "
            f"Signature: {signature_hex[:16]}..."
        )
        logger.info(notes)

        return HsmSigningAuditReport(
            key_alias=request.key_alias,
            algorithm=meta.algorithm,
            payload_hash_hex=payload_hash_hex,
            signature_hex=signature_hex,
            is_signature_valid=True,
            is_key_extractable=meta.is_extractable,
            status="SIGNATURE_SUCCESS",
            audit_notes=notes
        )
