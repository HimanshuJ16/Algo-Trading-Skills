"""
hardware-security-module-hsm-for-signing-keys: PKCS#11 key-attribute
non-exportability auditor, signing authorisation policy engine, signing-input
domain guard, secp256k1 low-S normaliser and hash-chained signing audit log.

WHAT THIS MODULE IS NOT
-----------------------
It is not an HSM, it holds no key material, and it computes no signatures. It
wraps a caller-supplied ``signer`` callable that is expected to be a real
PKCS#11 ``C_Sign`` binding (python-pkcs11, PyKCS11, a CloudHSM/YubiHSM SDK).
The engine decides whether the request is allowed, whether the signing input is
in the right domain for the declared algorithm, whether what came back is
well-formed, and records the attempt - successes AND denials - in a
tamper-evident chained log.

The pre-2.0 version of this module derived a "private key" as
``sha256(b"HSM_ENTROPY_SEED_" + alias)`` and returned an HMAC of the payload as
the "signature", while hard-coding ``is_signature_valid=True``. Every one of
those keys was reconstructible by anyone who knew the alias, and every such
signature was forgeable offline. Simulating key material in a custody helper is
not a safe simplification, so this module now refuses to hold any.

Design rule: every check FAILS CLOSED. An unknown algorithm, an unrecognised
signing-input encoding, a digest of the wrong length, a signature of the wrong
length, or an unknown caller role produces an exception plus an audit record -
never a silent pass. A custody control that returns "fine" because it did not
understand its input manufactures false assurance about irreversible transfers.

Standards behind the checks (full citations in ../references/standards.md):
  - PKCS#11 v3.1 attributes: CKA_SENSITIVE blocks reading the key value via
    C_GetAttributeValue; CKA_EXTRACTABLE=False blocks wrapping via C_WrapKey.
    They are different protections and BOTH are required. CKA_NEVER_EXTRACTABLE
    and CKA_ALWAYS_SENSITIVE are the read-only attestations that the key was
    never exposed at any point in its life.
  - PKCS#11 CKM_ECDSA takes a pre-computed hash and truncates input longer than
    the base point order; its output is r||s zero-padded and concatenated - not
    DER. Ed25519 (pure, RFC 8032) signs the MESSAGE, not a digest.
  - BIP-146 / EIP-2: an ECDSA signature over secp256k1 with s > n/2 is
    malleable; Bitcoin treats it as non-standard and Ethereum rejects it.
  - CMVP: FIPS 140-2 validations move to the Historical List; current vendor
    modules are FIPS 140-3 Level 3.
"""
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import logging
import threading
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Exceptions
#
# Each subclasses the builtin the pre-2.0 API raised, so callers written
# against `except PermissionError` / `except ValueError` keep working.
# --------------------------------------------------------------------------
class HsmCustodyError(Exception):
    """Base class for every custody-policy failure raised by this module."""


class HsmKeyNotFoundError(HsmCustodyError, ValueError):
    """The referenced key alias is not registered with this engine."""


class HsmKeyAlreadyRegisteredError(HsmCustodyError, ValueError):
    """
    Re-registering an existing alias was attempted.

    Registration is deliberately not idempotent-by-overwrite: in a custody
    system, silently replacing the metadata of a live signing key hides a key
    swap, and on a real HSM the equivalent mistake (generating over an existing
    label) destroys the only key that can sign for existing addresses.
    """


class HsmAuthorizationError(HsmCustodyError, PermissionError):
    """The caller is not authorised to request this signature."""


class HsmPolicyViolationError(HsmCustodyError, PermissionError):
    """A custody policy was violated (key export, disabled key, wrong domain)."""


class HsmSignerError(HsmCustodyError, RuntimeError):
    """The underlying PKCS#11 signer failed or returned a malformed signature."""


# --------------------------------------------------------------------------
# Enumerations and constants
# --------------------------------------------------------------------------
class SigningAlgorithm(str, Enum):
    """Values match the pre-2.0 string literals so existing configs still load."""
    SECP256K1 = "SECP256K1"
    ED25519 = "ED25519"
    HMAC_SHA256 = "HMAC_SHA256"


class SigningInputEncoding(str, Enum):
    """
    What the bytes handed to the HSM actually are.

    The engine NEVER hashes on the caller's behalf. Declaring the encoding is
    mandatory because the same 32 bytes mean different things to different
    chains, and re-hashing an already-computed digest produces a perfectly
    valid signature over the wrong transaction.
    """
    SHA256_DIGEST = "SHA256_DIGEST"        # single SHA-256
    SHA256D_DIGEST = "SHA256D_DIGEST"      # double SHA-256, Bitcoin sighash
    KECCAK256_DIGEST = "KECCAK256_DIGEST"  # Keccak-256, Ethereum sighash
    RAW_MESSAGE = "RAW_MESSAGE"            # unhashed message (pure Ed25519)


class RiskLevel(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SigningStatus(str, Enum):
    """
    Every terminal state of a signing or export attempt.

    Pre-2.0 this enumeration existed only as a comment on a field hard-coded to
    SIGNATURE_SUCCESS; denials raised and were never recorded. Denials are
    exactly the events an auditor samples for, so each one now produces a
    record before the exception propagates.
    """
    SIGNATURE_SUCCESS = "SIGNATURE_SUCCESS"
    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    EXPORT_ATTEMPT_REJECTED = "EXPORT_ATTEMPT_REJECTED"
    KEY_NOT_FOUND = "KEY_NOT_FOUND"
    KEY_DISABLED = "KEY_DISABLED"
    INPUT_DOMAIN_VIOLATION = "INPUT_DOMAIN_VIOLATION"
    SIGNER_FAILED = "SIGNER_FAILED"
    MALFORMED_SIGNATURE = "MALFORMED_SIGNATURE"


#: Order of the secp256k1 base point (SEC 2 v2.0 section 2.4.1). Cross-checked
#: in the test suite against the low-S upper bound published in BIP-146.
SECP256K1_ORDER: int = (
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
)
#: BIP-146 / EIP-2 boundary: s must be <= n/2 or the signature is malleable.
SECP256K1_HALF_ORDER: int = SECP256K1_ORDER // 2

#: PKCS#11 returns ECDSA as r||s, each zero-padded to the byte length of the
#: base point order (32 bytes for secp256k1) - 64 bytes, NOT DER. Ed25519 per
#: RFC 8032 is R||S = 64 bytes. HMAC-SHA256 is a 32-byte tag.
EXPECTED_SIGNATURE_LENGTHS: Dict[str, int] = {
    SigningAlgorithm.SECP256K1.value: 64,
    SigningAlgorithm.ED25519.value: 64,
    SigningAlgorithm.HMAC_SHA256.value: 32,
}

#: Exact byte length required for each digest encoding. CKM_ECDSA truncates
#: input longer than the order length, so an over-long digest is silently
#: signed as a different value; an under-length digest is equally wrong.
DIGEST_LENGTHS: Dict[str, int] = {
    SigningInputEncoding.SHA256_DIGEST.value: 32,
    SigningInputEncoding.SHA256D_DIGEST.value: 32,
    SigningInputEncoding.KECCAK256_DIGEST.value: 32,
}

#: Which signing-input encodings each algorithm may legitimately receive.
#: ECDSA (CKM_ECDSA) takes a digest and must never be handed a raw message.
#: Pure Ed25519 (RFC 8032 / CKM_EDDSA without the prehash parameter) signs the
#: MESSAGE - handing it a digest signs the digest, not the transaction.
ALLOWED_INPUT_ENCODINGS: Dict[str, Tuple[str, ...]] = {
    SigningAlgorithm.SECP256K1.value: (
        SigningInputEncoding.SHA256_DIGEST.value,
        SigningInputEncoding.SHA256D_DIGEST.value,
        SigningInputEncoding.KECCAK256_DIGEST.value,
    ),
    SigningAlgorithm.ED25519.value: (
        SigningInputEncoding.RAW_MESSAGE.value,
    ),
    SigningAlgorithm.HMAC_SHA256.value: (
        SigningInputEncoding.SHA256_DIGEST.value,
        SigningInputEncoding.SHA256D_DIGEST.value,
        SigningInputEncoding.KECCAK256_DIGEST.value,
        SigningInputEncoding.RAW_MESSAGE.value,
    ),
}

#: FIPS 140-3 is the only standard CMVP still issues certificates against.
CURRENT_FIPS_VALIDATIONS: Set[str] = {
    "FIPS_140_3_LEVEL_3",
    "FIPS_140_3_LEVEL_4",
}
#: Accepted but sunsetting - see FIPS_140_2_PROGRAM_HISTORICAL_EPOCH.
LEGACY_FIPS_VALIDATIONS: Set[str] = {
    "FIPS_140_2_LEVEL_3",
    "FIPS_140_2_LEVEL_4",
}
#: 2026-09-22T00:00:00Z. CMVP moves ALL remaining FIPS 140-2 certificates to
#: the Historical List on this date. Individual certificates sunset EARLIER
#: (five years after validation) - AWS CloudHSM hsm1.medium cert #4218 moved on
#: 2026-01-04 - so a per-key `fips_historical_epoch` taken from the module's own
#: CMVP entry always overrides this program-wide backstop.
FIPS_140_2_PROGRAM_HISTORICAL_EPOCH: float = 1_790_035_200.0


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class HsmKeyMetaData:
    """
    Attributes read back FROM the HSM for a key - never key material.

    The four PKCS#11 booleans are kept distinct on purpose. `sensitive` and
    `extractable` block different attacks (reading the value vs wrapping it
    out), and the `never_*`/`always_*` pair is the only evidence that the key
    was not exposed before the current attribute values were set.
    """
    key_alias: str
    algorithm: str
    slot_id: int
    sensitive: bool = True                    # CKA_SENSITIVE
    extractable: bool = False                 # CKA_EXTRACTABLE
    never_extractable: bool = True            # CKA_NEVER_EXTRACTABLE
    always_sensitive: bool = True             # CKA_ALWAYS_SENSITIVE
    fips_certification: str = "FIPS_140_3_LEVEL_3"
    fips_certificate_number: Optional[str] = None
    fips_historical_epoch: Optional[float] = None

    @property
    def is_extractable(self) -> bool:
        """Pre-2.0 field name, retained so existing callers keep reading."""
        return self.extractable


@dataclass(frozen=True)
class HsmAuditFinding:
    key_alias: str
    risk_level: RiskLevel
    issue: str
    remediation: str


@dataclass
class HsmSignatureRequest:
    """
    One signing request.

    `signing_input` is handed to the HSM verbatim. `input_encoding` declares
    what it is; the engine validates that declaration against the key's
    algorithm and never transforms the bytes.
    """
    key_alias: str
    signing_input: bytes
    input_encoding: str
    caller_identity: str
    caller_role: str
    request_id: Optional[str] = None


@dataclass(frozen=True)
class HsmSigningAuditRecord:
    """
    One tamper-evident log entry.

    `record_hash` covers the record's own content AND `previous_record_hash`,
    so altering or deleting any earlier entry invalidates every later one.
    This is tamper-EVIDENT, not tamper-proof: an attacker who can rewrite the
    whole list can recompute the chain. Ship records to append-only external
    storage (WORM bucket, SIEM) for tamper resistance.
    """
    sequence: int
    event_time_epoch: float
    key_alias: str
    algorithm: str
    input_encoding: str
    signing_input_sha256_hex: str
    caller_identity: str
    caller_role: str
    status: str
    detail: str
    signature_hex: Optional[str]
    previous_record_hash: str
    record_hash: str


@dataclass(frozen=True)
class HsmSigningAuditReport:
    """
    Outcome of a successful signing request.

    There is deliberately no `is_signature_valid` field. Verifying an ECDSA or
    Ed25519 signature requires the public key and curve arithmetic this
    dependency-free module does not perform, and the pre-2.0 field was
    hard-coded True regardless of what happened. `is_signature_well_formed`
    states only what was actually checked: that the length matches the
    algorithm (and, for secp256k1, that r and s are in range).
    """
    key_alias: str
    algorithm: str
    input_encoding: str
    signing_input_sha256_hex: str
    signature_hex: str
    is_signature_well_formed: bool
    is_low_s_normalized: Optional[bool]      # None when not applicable
    was_low_s_normalization_applied: bool
    is_key_extractable: bool
    status: str
    audit_notes: str
    record: HsmSigningAuditRecord


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def normalize_secp256k1_low_s(signature: bytes) -> Tuple[bytes, bool]:
    """
    Enforce the BIP-146 / EIP-2 low-S rule on a raw 64-byte r||s signature.

    (r, s) and (r, n - s) are both valid ECDSA signatures over secp256k1, so a
    high-S signature is malleable: a third party can rewrite it, changing the
    Bitcoin txid without touching the private key. Bitcoin treats high-S as
    non-standard and Ethereum rejects transactions with s > n/2 outright.
    PKCS#11 does not require an HSM to return low-S, so normalise on the way
    out rather than assuming the device did it.

    Returns (normalized_signature, was_changed). Raises on anything that is not
    a 64-byte signature, or on r/s outside [1, n-1], rather than passing a
    malformed signature through to a broadcast path.
    """
    if not isinstance(signature, (bytes, bytearray)):
        raise TypeError(f"signature must be bytes, got {type(signature).__name__}.")
    if len(signature) != 64:
        raise ValueError(
            f"Expected a 64-byte raw r||s secp256k1 signature, got {len(signature)} "
            "bytes. PKCS#11 CKM_ECDSA returns r and s zero-padded to the order "
            "length and concatenated; a ~70-72 byte value is DER and must be "
            "decoded first."
        )
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    if not 1 <= r < SECP256K1_ORDER:
        raise ValueError("secp256k1 signature component r is outside [1, n-1].")
    if not 1 <= s < SECP256K1_ORDER:
        raise ValueError("secp256k1 signature component s is outside [1, n-1].")

    if s > SECP256K1_HALF_ORDER:
        return signature[:32] + (SECP256K1_ORDER - s).to_bytes(32, "big"), True
    return bytes(signature), False


def is_low_s(signature: bytes) -> bool:
    """True when a raw 64-byte secp256k1 signature already satisfies s <= n/2."""
    _, changed = normalize_secp256k1_low_s(signature)
    return not changed


def _require_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}.")
    stripped = value.strip()
    if not stripped:
        raise ValueError(
            f"{field_name} must be a non-empty, non-whitespace string. An "
            "unattributed key or caller cannot be audited."
        )
    return stripped


def _hash_record(payload: Dict[str, object]) -> str:
    """SHA-256 over a canonical JSON encoding, so the chain is reproducible."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


#: A real PKCS#11 C_Sign binding: given the key's metadata and the exact bytes
#: to sign, return the signature. It must never return key material.
SignerCallable = Callable[[HsmKeyMetaData, bytes], bytes]

#: Genesis value for the audit chain.
_GENESIS_HASH = "0" * 64

_RISK_ORDER: Dict[RiskLevel, int] = {
    RiskLevel.CRITICAL: 0,
    RiskLevel.HIGH: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 3,
}


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------
class HsmSigningManagerEngine:
    """
    Authorisation, input-domain and audit-trail control plane in front of a
    PKCS#11 signing device.

    Thread-safe: this skill's stated use is concurrent trading threads sharing
    HSM sessions, so the key registry and the audit chain are guarded by a
    re-entrant lock. Sequence numbers and chain links are therefore monotonic
    and gap-free under concurrency.
    """

    #: Roles permitted to request a signature. ADMIN is excluded by default: a
    #: role that both administers key attributes and authorises transfers has
    #: no segregation of duties. Override explicitly if your mandate says
    #: otherwise - this default is a policy choice, not a regulatory rule.
    DEFAULT_SIGNING_ROLES: Tuple[str, ...] = ("OPERATOR",)

    def __init__(
        self,
        allowed_signing_roles: Optional[Sequence[str]] = None,
        enforce_low_s: bool = True,
    ) -> None:
        roles = (
            tuple(allowed_signing_roles)
            if allowed_signing_roles is not None
            else self.DEFAULT_SIGNING_ROLES
        )
        if not roles:
            raise ValueError(
                "allowed_signing_roles must not be empty; an engine that "
                "authorises nobody silently blocks every transfer."
            )
        self.allowed_signing_roles: Tuple[str, ...] = tuple(
            _require_non_blank(role, "role").upper() for role in roles
        )
        self.enforce_low_s = bool(enforce_low_s)

        self._lock = threading.RLock()
        self._keys: Dict[str, HsmKeyMetaData] = {}
        self._disabled: Dict[str, str] = {}
        self._audit_log: List[HsmSigningAuditRecord] = []

    # -- registration ------------------------------------------------------
    def register_hardware_key(
        self,
        key_alias: str,
        algorithm: str = SigningAlgorithm.SECP256K1.value,
        slot_id: int = 0,
        sensitive: bool = True,
        extractable: bool = False,
        never_extractable: bool = True,
        always_sensitive: bool = True,
        fips_certification: str = "FIPS_140_3_LEVEL_3",
        fips_certificate_number: Optional[str] = None,
        fips_historical_epoch: Optional[float] = None,
    ) -> HsmKeyMetaData:
        """
        Record the attributes of a key that ALREADY EXISTS inside the HSM.

        This replaces the pre-2.0 `generate_hardware_key`, which fabricated key
        material in process memory. Key generation happens on the device
        (`C_GenerateKeyPair` with CKA_SENSITIVE=True, CKA_EXTRACTABLE=False);
        the values passed here must be read back from the device with
        `C_GetAttributeValue`, not asserted by hand.

        Raises HsmKeyAlreadyRegisteredError on a duplicate alias.
        """
        alias = _require_non_blank(key_alias, "key_alias")
        if algorithm not in EXPECTED_SIGNATURE_LENGTHS:
            raise ValueError(
                f"Unsupported algorithm '{algorithm}'. Supported: "
                f"{sorted(EXPECTED_SIGNATURE_LENGTHS)}."
            )
        if not isinstance(slot_id, int) or isinstance(slot_id, bool) or slot_id < 0:
            raise ValueError(
                f"slot_id must be a non-negative int, got {slot_id!r}. PKCS#11 "
                "slot IDs are CK_SLOT_ID (unsigned)."
            )
        known_validations = CURRENT_FIPS_VALIDATIONS | LEGACY_FIPS_VALIDATIONS
        if fips_certification not in known_validations:
            raise ValueError(
                f"Unrecognised fips_certification '{fips_certification}'. Use a "
                f"value from {sorted(known_validations)}, taken from the module's "
                "CMVP certificate rather than the vendor datasheet."
            )

        with self._lock:
            if alias in self._keys:
                raise HsmKeyAlreadyRegisteredError(
                    f"Key alias '{alias}' is already registered. Re-registering "
                    "would silently swap a live signing key; deregister "
                    "deliberately or use a new alias."
                )
            meta = HsmKeyMetaData(
                key_alias=alias,
                algorithm=algorithm,
                slot_id=slot_id,
                sensitive=bool(sensitive),
                extractable=bool(extractable),
                never_extractable=bool(never_extractable),
                always_sensitive=bool(always_sensitive),
                fips_certification=fips_certification,
                fips_certificate_number=fips_certificate_number,
                fips_historical_epoch=fips_historical_epoch,
            )
            self._keys[alias] = meta

        logger.info(
            "HSM key registered: alias=%s slot=%d algorithm=%s sensitive=%s extractable=%s",
            alias, slot_id, algorithm, meta.sensitive, meta.extractable,
        )
        return meta

    def get_key(self, key_alias: str) -> HsmKeyMetaData:
        """Return registered metadata, or raise HsmKeyNotFoundError."""
        with self._lock:
            try:
                return self._keys[key_alias]
            except KeyError:
                raise HsmKeyNotFoundError(
                    f"Key alias '{key_alias}' is not registered with this engine."
                ) from None

    def disable_key(self, key_alias: str, reason: str, current_time_epoch: float) -> None:
        """
        Mark a key unusable for signing (rotation, suspected compromise).

        A compromised key that can still sign is the entire exposure; the
        pre-2.0 engine had no way to stop one.
        """
        meta = self.get_key(key_alias)
        detail = _require_non_blank(reason, "reason")
        with self._lock:
            self._disabled[meta.key_alias] = detail
            self._append_record(
                event_time_epoch=current_time_epoch,
                key_alias=meta.key_alias,
                algorithm=meta.algorithm,
                input_encoding="",
                signing_input_sha256_hex="",
                caller_identity="",
                caller_role="",
                status=SigningStatus.KEY_DISABLED.value,
                detail=f"Key disabled: {detail}",
                signature_hex=None,
            )
        logger.warning("HSM key disabled: alias=%s reason=%s", meta.key_alias, detail)

    # -- attribute audit ---------------------------------------------------
    def audit_key_attributes(
        self,
        key_alias: str,
        current_time_epoch: float,
    ) -> List[HsmAuditFinding]:
        """
        Check the key's PKCS#11 protection attributes and FIPS validation
        currency. Returns findings worst-first; an empty list means clean.
        """
        meta = self.get_key(key_alias)
        findings: List[HsmAuditFinding] = []

        if meta.extractable:
            findings.append(HsmAuditFinding(
                meta.key_alias, RiskLevel.CRITICAL,
                "CKA_EXTRACTABLE is True: the private key can be wrapped out of "
                "the HSM with C_WrapKey.",
                "Regenerate the key on-device with CKA_EXTRACTABLE=False. The "
                "attribute is one-way, so an extractable key cannot be repaired "
                "into a non-extractable one - and material that was extractable "
                "must be treated as already exposed.",
            ))
        if not meta.sensitive:
            findings.append(HsmAuditFinding(
                meta.key_alias, RiskLevel.CRITICAL,
                "CKA_SENSITIVE is False: the private key value can be read "
                "directly with C_GetAttributeValue.",
                "Regenerate the key with CKA_SENSITIVE=True. Non-extractable "
                "does not imply non-readable - the two block different attacks.",
            ))
        if not meta.never_extractable:
            findings.append(HsmAuditFinding(
                meta.key_alias, RiskLevel.HIGH,
                "CKA_NEVER_EXTRACTABLE is False: the key was extractable at some "
                "point, so a wrapped copy may exist outside the HSM.",
                "Treat the key as exposed. Generate fresh material on-device and "
                "migrate balances; today's attribute values say nothing about "
                "copies already taken.",
            ))
        if not meta.always_sensitive:
            findings.append(HsmAuditFinding(
                meta.key_alias, RiskLevel.HIGH,
                "CKA_ALWAYS_SENSITIVE is False: the key value was readable at "
                "some point in its life.",
                "Treat the key as exposed and rotate to freshly generated "
                "on-device material.",
            ))

        if meta.fips_certification in LEGACY_FIPS_VALIDATIONS:
            historical_epoch = meta.fips_historical_epoch
            if historical_epoch is None:
                historical_epoch = FIPS_140_2_PROGRAM_HISTORICAL_EPOCH
            if current_time_epoch >= historical_epoch:
                findings.append(HsmAuditFinding(
                    meta.key_alias, RiskLevel.HIGH,
                    f"{meta.fips_certification} validation is on the CMVP "
                    "Historical List as of the evaluation date.",
                    "Migrate to a FIPS 140-3 validated module (AWS CloudHSM "
                    "hsm2m.medium cert #4703, YubiHSM 2 FIPS cert #5302). CMVP "
                    "still supports historical modules for EXISTING systems, so "
                    "this is a migration finding, not an outage.",
                ))
            else:
                findings.append(HsmAuditFinding(
                    meta.key_alias, RiskLevel.MEDIUM,
                    f"{meta.fips_certification} validation is still active but "
                    "sunsetting; CMVP no longer issues FIPS 140-2 certificates.",
                    "Plan the FIPS 140-3 migration before the module's own "
                    "historical date - individual certificates sunset five years "
                    "after validation, ahead of the program-wide 2026-09-22 date.",
                ))
        elif meta.fips_certificate_number is None:
            findings.append(HsmAuditFinding(
                meta.key_alias, RiskLevel.LOW,
                "No CMVP certificate number recorded against the claimed "
                f"{meta.fips_certification} validation.",
                "Record the certificate number so the claim is auditable against "
                "the CMVP validated-modules list rather than a vendor datasheet.",
            ))

        return sorted(findings, key=lambda finding: _RISK_ORDER[finding.risk_level])

    def attempt_export_private_key(self, key_alias: str, current_time_epoch: float) -> None:
        """
        Always raises. Records the attempt first.

        This module never held key material, so there is nothing it could
        return; the method exists so that an export attempt - the single most
        interesting event in a custody log - leaves a record. It raises even
        when the key's attributes say it IS extractable, because a request to
        pull private key material into application memory is a policy failure
        regardless of whether the hardware would permit it.
        """
        meta = self.get_key(key_alias)
        if meta.extractable:
            detail = (
                f"Export of '{meta.key_alias}' refused. CKA_EXTRACTABLE=True on "
                "this key is itself a CRITICAL custody finding - the hardware "
                "would permit C_WrapKey."
            )
        else:
            detail = (
                f"HSM SECURITY VIOLATION: key '{meta.key_alias}' is non-exportable "
                "(CKA_EXTRACTABLE=False) and its value is unreadable "
                f"(CKA_SENSITIVE={meta.sensitive})."
            )
        with self._lock:
            self._append_record(
                event_time_epoch=current_time_epoch,
                key_alias=meta.key_alias,
                algorithm=meta.algorithm,
                input_encoding="",
                signing_input_sha256_hex="",
                caller_identity="",
                caller_role="",
                status=SigningStatus.EXPORT_ATTEMPT_REJECTED.value,
                detail=detail,
                signature_hex=None,
            )
        logger.error("HSM export attempt rejected: alias=%s", meta.key_alias)
        raise HsmPolicyViolationError(detail)

    # -- signing -----------------------------------------------------------
    def sign_transaction_payload(
        self,
        request: HsmSignatureRequest,
        signer: SignerCallable,
        current_time_epoch: float,
    ) -> HsmSigningAuditReport:
        """
        Authorise, delegate to the HSM, validate the result, and record it.

        `signer` is the caller's PKCS#11 C_Sign binding. It receives the key
        metadata and the exact bytes to sign - the engine transforms neither.

        Raises (after writing an audit record) on: unknown or disabled key, an
        unauthorised role, a signing input whose declared encoding is wrong for
        the algorithm or the wrong length, a signer that raises, or a signature
        whose length does not match the algorithm.
        """
        if not isinstance(request, HsmSignatureRequest):
            raise TypeError(
                f"request must be an HsmSignatureRequest, got {type(request).__name__}."
            )
        if not callable(signer):
            raise TypeError(
                "signer must be a callable PKCS#11 C_Sign binding taking "
                "(HsmKeyMetaData, bytes) and returning bytes."
            )

        identity = _require_non_blank(request.caller_identity, "caller_identity")
        role = _require_non_blank(request.caller_role, "caller_role").upper()
        alias = _require_non_blank(request.key_alias, "key_alias")

        if not isinstance(request.signing_input, (bytes, bytearray)):
            raise TypeError(
                "signing_input must be bytes; a str would be encoded with an "
                f"implicit charset. Got {type(request.signing_input).__name__}."
            )
        signing_input = bytes(request.signing_input)
        if not signing_input:
            raise ValueError("signing_input is empty; refusing to sign nothing.")
        input_digest_hex = hashlib.sha256(signing_input).hexdigest()

        # Key existence. Recorded before raising, so a probe for unknown
        # aliases is visible in the log rather than only in a stack trace.
        try:
            meta = self.get_key(alias)
        except HsmKeyNotFoundError as exc:
            self._record_denial(
                current_time_epoch, alias, "", request.input_encoding,
                input_digest_hex, identity, role,
                SigningStatus.KEY_NOT_FOUND.value, str(exc),
            )
            raise

        with self._lock:
            disabled_reason = self._disabled.get(meta.key_alias)
        if disabled_reason is not None:
            detail = (
                f"Key '{meta.key_alias}' is disabled and must not sign: {disabled_reason}"
            )
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm,
                request.input_encoding, input_digest_hex, identity, role,
                SigningStatus.KEY_DISABLED.value, detail,
            )
            raise HsmPolicyViolationError(detail)

        if role not in self.allowed_signing_roles:
            detail = (
                f"Caller role '{role}' is not authorised to request HSM signatures. "
                f"Authorised roles: {list(self.allowed_signing_roles)}."
            )
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm,
                request.input_encoding, input_digest_hex, identity, role,
                SigningStatus.AUTHORIZATION_DENIED.value, detail,
            )
            raise HsmAuthorizationError(detail)

        # Signing-input domain. Fails closed on an unrecognised encoding.
        encoding = request.input_encoding
        allowed = ALLOWED_INPUT_ENCODINGS[meta.algorithm]
        if encoding not in allowed:
            known_encodings = [member.value for member in SigningInputEncoding]
            if encoding not in known_encodings:
                detail = (
                    f"Unrecognised input_encoding '{encoding}'. Declare one of "
                    f"{known_encodings} - the engine will not guess what the "
                    "bytes are."
                )
            else:
                detail = (
                    f"input_encoding '{encoding}' is not valid for algorithm "
                    f"'{meta.algorithm}'; allowed: {list(allowed)}. CKM_ECDSA "
                    "signs a pre-computed digest, while pure Ed25519 signs the "
                    "message itself - swapping them signs the wrong value."
                )
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm, encoding,
                input_digest_hex, identity, role,
                SigningStatus.INPUT_DOMAIN_VIOLATION.value, detail,
            )
            raise HsmPolicyViolationError(detail)

        expected_digest_len = DIGEST_LENGTHS.get(encoding)
        if expected_digest_len is not None and len(signing_input) != expected_digest_len:
            detail = (
                f"input_encoding '{encoding}' requires exactly {expected_digest_len} "
                f"bytes, got {len(signing_input)}. CKM_ECDSA truncates input longer "
                "than the base point order, so an over-long value is silently "
                "signed as something else."
            )
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm, encoding,
                input_digest_hex, identity, role,
                SigningStatus.INPUT_DOMAIN_VIOLATION.value, detail,
            )
            raise HsmPolicyViolationError(detail)

        # Delegate to the device.
        try:
            signature = signer(meta, signing_input)
        except Exception as exc:  # noqa: BLE001 - surfaced as HsmSignerError below
            detail = f"PKCS#11 signer raised {type(exc).__name__}: {exc}"
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm, encoding,
                input_digest_hex, identity, role,
                SigningStatus.SIGNER_FAILED.value, detail,
            )
            raise HsmSignerError(
                detail + ". A failed C_Sign is NOT proof that nothing was signed - "
                "a timeout can lose the response to a completed operation. "
                "Reconcile against the device's own log before retrying."
            ) from exc

        expected_sig_len = EXPECTED_SIGNATURE_LENGTHS[meta.algorithm]
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != expected_sig_len:
            actual = (
                len(signature) if isinstance(signature, (bytes, bytearray))
                else type(signature).__name__
            )
            detail = (
                f"Signer returned {actual} where a {expected_sig_len}-byte "
                f"{meta.algorithm} signature was expected. PKCS#11 returns ECDSA "
                "as raw r||s, not DER - a ~70-72 byte value must be decoded first."
            )
            self._record_denial(
                current_time_epoch, meta.key_alias, meta.algorithm, encoding,
                input_digest_hex, identity, role,
                SigningStatus.MALFORMED_SIGNATURE.value, detail,
            )
            raise HsmSignerError(detail)
        signature = bytes(signature)

        low_s_state: Optional[bool] = None
        normalized = False
        if meta.algorithm == SigningAlgorithm.SECP256K1.value:
            try:
                candidate, normalized = normalize_secp256k1_low_s(signature)
            except ValueError as exc:
                detail = f"Malformed secp256k1 signature from signer: {exc}"
                self._record_denial(
                    current_time_epoch, meta.key_alias, meta.algorithm, encoding,
                    input_digest_hex, identity, role,
                    SigningStatus.MALFORMED_SIGNATURE.value, detail,
                )
                raise HsmSignerError(detail) from exc
            if self.enforce_low_s:
                signature = candidate
                low_s_state = True
            else:
                low_s_state = not normalized

        signature_hex = signature.hex()
        notes = (
            f"HSM signature issued: alias={meta.key_alias} algorithm={meta.algorithm} "
            f"encoding={encoding} input_sha256={input_digest_hex[:16]}... "
            f"caller={identity} ({role})"
        )
        if normalized:
            notes += " low_s_normalized=True"

        with self._lock:
            record = self._append_record(
                event_time_epoch=current_time_epoch,
                key_alias=meta.key_alias,
                algorithm=meta.algorithm,
                input_encoding=encoding,
                signing_input_sha256_hex=input_digest_hex,
                caller_identity=identity,
                caller_role=role,
                status=SigningStatus.SIGNATURE_SUCCESS.value,
                detail=notes,
                signature_hex=signature_hex,
            )
        logger.info(notes)

        return HsmSigningAuditReport(
            key_alias=meta.key_alias,
            algorithm=meta.algorithm,
            input_encoding=encoding,
            signing_input_sha256_hex=input_digest_hex,
            signature_hex=signature_hex,
            is_signature_well_formed=True,
            is_low_s_normalized=low_s_state,
            was_low_s_normalization_applied=normalized,
            is_key_extractable=meta.extractable,
            status=SigningStatus.SIGNATURE_SUCCESS.value,
            audit_notes=notes,
            record=record,
        )

    # -- audit chain -------------------------------------------------------
    @property
    def audit_log(self) -> Tuple[HsmSigningAuditRecord, ...]:
        with self._lock:
            return tuple(self._audit_log)

    def verify_audit_chain(self) -> bool:
        """
        Recompute every record hash and confirm the links. False means an entry
        was altered, inserted or removed after the fact.
        """
        with self._lock:
            previous = _GENESIS_HASH
            for index, record in enumerate(self._audit_log):
                if record.sequence != index or record.previous_record_hash != previous:
                    return False
                if _hash_record(self._record_payload(record, previous)) != record.record_hash:
                    return False
                previous = record.record_hash
        return True

    # -- internals ---------------------------------------------------------
    def _record_denial(
        self,
        event_time_epoch: float,
        key_alias: str,
        algorithm: str,
        input_encoding: str,
        signing_input_sha256_hex: str,
        caller_identity: str,
        caller_role: str,
        status: str,
        detail: str,
    ) -> HsmSigningAuditRecord:
        with self._lock:
            record = self._append_record(
                event_time_epoch=event_time_epoch,
                key_alias=key_alias,
                algorithm=algorithm,
                input_encoding=input_encoding,
                signing_input_sha256_hex=signing_input_sha256_hex,
                caller_identity=caller_identity,
                caller_role=caller_role,
                status=status,
                detail=detail,
                signature_hex=None,
            )
        logger.warning("HSM request denied [%s]: %s", status, detail)
        return record

    @staticmethod
    def _record_payload(
        record: HsmSigningAuditRecord,
        previous_hash: str,
    ) -> Dict[str, object]:
        return {
            "sequence": record.sequence,
            "event_time_epoch": record.event_time_epoch,
            "key_alias": record.key_alias,
            "algorithm": record.algorithm,
            "input_encoding": record.input_encoding,
            "signing_input_sha256_hex": record.signing_input_sha256_hex,
            "caller_identity": record.caller_identity,
            "caller_role": record.caller_role,
            "status": record.status,
            "detail": record.detail,
            "signature_hex": record.signature_hex,
            "previous_record_hash": previous_hash,
        }

    def _append_record(
        self,
        event_time_epoch: float,
        key_alias: str,
        algorithm: str,
        input_encoding: str,
        signing_input_sha256_hex: str,
        caller_identity: str,
        caller_role: str,
        status: str,
        detail: str,
        signature_hex: Optional[str],
    ) -> HsmSigningAuditRecord:
        """Append one chained record. Caller must hold self._lock."""
        if not isinstance(event_time_epoch, (int, float)) or isinstance(event_time_epoch, bool):
            raise TypeError(
                "current_time_epoch must be a number. The evaluation clock is "
                "passed in explicitly so audit records are reproducible."
            )
        previous = self._audit_log[-1].record_hash if self._audit_log else _GENESIS_HASH
        draft = HsmSigningAuditRecord(
            sequence=len(self._audit_log),
            event_time_epoch=float(event_time_epoch),
            key_alias=key_alias,
            algorithm=algorithm,
            input_encoding=input_encoding,
            signing_input_sha256_hex=signing_input_sha256_hex,
            caller_identity=caller_identity,
            caller_role=caller_role,
            status=status,
            detail=detail,
            signature_hex=signature_hex,
            previous_record_hash=previous,
            record_hash="",
        )
        record_hash = _hash_record(self._record_payload(draft, previous))
        record = HsmSigningAuditRecord(
            sequence=draft.sequence,
            event_time_epoch=draft.event_time_epoch,
            key_alias=draft.key_alias,
            algorithm=draft.algorithm,
            input_encoding=draft.input_encoding,
            signing_input_sha256_hex=draft.signing_input_sha256_hex,
            caller_identity=draft.caller_identity,
            caller_role=draft.caller_role,
            status=draft.status,
            detail=draft.detail,
            signature_hex=draft.signature_hex,
            previous_record_hash=previous,
            record_hash=record_hash,
        )
        self._audit_log.append(record)
        return record
