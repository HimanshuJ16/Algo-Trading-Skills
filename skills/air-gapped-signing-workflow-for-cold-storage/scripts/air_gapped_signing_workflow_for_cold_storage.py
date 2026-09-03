"""Air-gapped signing workflow for an Ethereum-style cold-storage transfer.

This module models the **trust boundary** between an online coordinator and an
offline signing vault, and the two things that boundary exists to guarantee:

1. The vault signs only what a human actually saw and approved (clear signing).
2. The coordinator broadcasts only an envelope cryptographically bound to an
   intent it issued itself, exactly once.

The adversary is the online coordinator. It is assumed to be internet-connected
and therefore compromisable, so every control that matters is enforced on the
offline side: the vault re-derives the display from the bytes it is about to
sign, applies its own policy, requires explicit human approval, and keeps its
own anti-replay ledger. Controls on the coordinator side are defence in depth
against operator error, not against a compromised coordinator.

What this module is NOT:

- It is not a wallet. The signature primitive here is a keyed HMAC, which is
  **symmetric**: the ``verification_key`` the coordinator holds is sufficient to
  forge any signature. That is the opposite of the property real custody needs
  and is acceptable only because this module never touches funds. Production
  systems sign with audited chain-native secp256k1 inside a hardware wallet or
  HSM, and the online side holds only a public key.
- It does not build, RLP-encode, or serialise an actual Ethereum transaction.
  ``UnsignedPayload`` is a transfer *intent*, not a signable transaction body.
- It holds no durable state. ``_issued``/``_consumed``/``_signed`` live in
  process memory; production requires a restart-safe store.
"""

import dataclasses
import enum
import hashlib
import hmac
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Dict, FrozenSet, Iterable, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SUPPORTED_NETWORK = "ETH"
_PAYLOAD_VERSION = 2
_MAX_NONCE = 2**64 - 1
_WEI_DECIMALS = 18
# An EVM transaction's `value` field is a 256-bit unsigned integer of wei, so an
# amount whose wei representation exceeds this cannot be expressed on-chain.
_MAX_WEI = 2**256 - 1
_MAX_WEI_DIGITS = len(str(_MAX_WEI))  # 78
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

_ENVELOPE_FIELDS = frozenset(
    {"unsigned_payload", "original_payload_hash", "signature", "signer_key_id"}
)
_PAYLOAD_FIELDS = frozenset(
    {"destination_address", "amount", "network", "chain_id", "nonce", "version"}
)


class AirGapSigningError(ValueError):
    """Raised when a payload or envelope cannot be parsed or validated.

    Raised rather than returned, because a payload the vault cannot understand
    must never be confused with a payload the vault declined to sign.
    """


@dataclasses.dataclass(frozen=True)
class UnsignedPayload:
    """A coordinator-issued transfer intent.

    ``chain_id`` is mandatory and carries the EIP-155 chain identifier. Binding
    the intent to a network *label* alone ("ETH") does not distinguish Ethereum
    mainnet from any other EVM chain that shares its address format, which is
    exactly the cross-chain replay EIP-155 exists to prevent.
    """

    destination_address: str
    amount: str
    network: str
    chain_id: int
    nonce: int
    version: int = _PAYLOAD_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.destination_address, str) or not _ADDRESS_RE.fullmatch(
            self.destination_address
        ):
            raise AirGapSigningError(
                "destination_address must be a 20-byte hexadecimal address"
            )
        if self.network != _SUPPORTED_NETWORK:
            raise AirGapSigningError("unsupported network")
        if self.version != _PAYLOAD_VERSION:
            # v1 payloads carried no chain_id and are therefore unbound to a
            # chain. Rejecting them is deliberate: silently upgrading one would
            # invent a chain the approver never saw.
            raise AirGapSigningError("unsupported payload version")
        if not _is_bounded_int(self.chain_id, 1, _MAX_NONCE):
            raise AirGapSigningError("chain_id must be a positive bounded integer")
        if not _is_bounded_int(self.nonce, 1, _MAX_NONCE):
            raise AirGapSigningError("nonce must be a positive bounded integer")
        object.__setattr__(self, "amount", _canonical_amount(self.amount))

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    def to_qr_code_data(self) -> str:
        """Serialise the exact intent deterministically for QR/SD transfer."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )

    @classmethod
    def from_qr_code_data(cls, qr_data: str) -> "UnsignedPayload":
        data = _decode_json_object(qr_data, _PAYLOAD_FIELDS, "payload")
        return cls(**data)

    def payload_hash(self) -> str:
        return hashlib.sha256(self.to_qr_code_data().encode("utf-8")).hexdigest()

    def clear_signing_display(self) -> str:
        """Render the human-readable text that must be approved before signing.

        Derived from the payload the vault is about to sign, so the approver
        cannot be shown one transfer while a different one is signed. In the
        February 2025 Bybit theft the signers' keys were never stolen; the
        interface they read described a transfer that was not the one they
        authorised.
        """
        return (
            "TRANSFER APPROVAL REQUIRED\n"
            f"  Destination: {self.destination_address}\n"
            f"  Amount:      {self.amount} {self.network}\n"
            f"  Chain ID:    {self.chain_id}\n"
            f"  Nonce:       {self.nonce}\n"
            f"  Payload SHA-256: {self.payload_hash()}"
        )


@dataclasses.dataclass(frozen=True)
class SignedPayload:
    """A signed envelope as it crosses the controlled medium back online.

    Every field arrives from untrusted media, so the types are validated here
    rather than at the point of use: an envelope carrying a non-string
    signature must fail closed, not raise ``TypeError`` out of a comparison.
    """

    unsigned_payload: str
    original_payload_hash: str
    signature: str
    signer_key_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.unsigned_payload, str) or not self.unsigned_payload:
            raise AirGapSigningError("unsigned_payload must be non-empty text")
        for field, value in (
            ("original_payload_hash", self.original_payload_hash),
            ("signature", self.signature),
        ):
            if not isinstance(value, str) or not _HEX64_RE.fullmatch(value):
                raise AirGapSigningError(f"{field} must be 64 lowercase hex characters")
        if not isinstance(self.signer_key_id, str) or not self.signer_key_id.strip():
            raise AirGapSigningError("signer_key_id must be a non-empty identifier")

    def to_transport_data(self) -> str:
        """Serialise the envelope for the return trip over QR/SD media."""
        return json.dumps(
            dataclasses.asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    @classmethod
    def from_transport_data(cls, transport_data: str) -> "SignedPayload":
        return cls(**_decode_json_object(transport_data, _ENVELOPE_FIELDS, "envelope"))


class BroadcastStatus(enum.Enum):
    """Outcome of a broadcast attempt.

    ``UNRESOLVED`` exists because a lost RPC response is not a failure. The node
    may have accepted the transaction before the response was lost, so the only
    honest state is *unknown*, to be settled against chain evidence. Collapsing
    it into a boolean is what turns one timeout into one double spend.
    """

    REJECTED = "rejected"
    ACCEPTED = "accepted"
    UNRESOLVED = "unresolved"


@dataclasses.dataclass(frozen=True)
class BroadcastResult:
    """Result of ``OnlineCoordinator.broadcast_to_network``.

    Deliberately not boolean-convertible. ``if result:`` would make
    ``UNRESOLVED`` read as failure at every call site, reintroducing the exact
    conflation this type exists to prevent. Compare ``result.status``.
    """

    status: BroadcastStatus
    reason: str
    payload_hash: Optional[str] = None
    transaction_reference: Optional[str] = None

    def __bool__(self) -> "bool":
        # A plain object is always truthy, which would make `if result:` silently
        # treat a REJECTED result as success. Refuse the conversion loudly.
        raise TypeError(
            "BroadcastResult has no truth value; compare result.status against "
            "BroadcastStatus explicitly (UNRESOLVED is neither success nor failure)"
        )

    @property
    def is_accepted(self) -> bool:
        return self.status is BroadcastStatus.ACCEPTED

    @property
    def needs_reconciliation(self) -> bool:
        return self.status is BroadcastStatus.UNRESOLVED


def _rejected(reason: str, payload_hash: Optional[str] = None) -> "BroadcastResult":
    return BroadcastResult(BroadcastStatus.REJECTED, reason, payload_hash)


def _is_bounded_int(value: Any, low: int, high: int) -> bool:
    """``bool`` is an ``int`` subclass; ``True`` must not pass as a nonce of 1."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _canonical_amount(amount: Any) -> str:
    """Validate an amount and return its canonical fixed-point string form."""
    if not isinstance(amount, str):
        raise AirGapSigningError("amount must be a decimal string")
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AirGapSigningError("amount must be a decimal string") from exc
    if not value.is_finite() or value <= 0:
        raise AirGapSigningError("amount must be finite and positive")
    _, digits, exponent = value.as_tuple()
    if exponent < -_WEI_DECIMALS:
        raise AirGapSigningError("amount must use at most 18 decimals")
    # Bound the magnitude before converting, so a huge exponent cannot be turned
    # into a multi-megabyte integer just to be rejected afterwards. uint256 max
    # is 78 digits, so its most significant digit sits at position 77.
    if value.adjusted() + _WEI_DECIMALS > _MAX_WEI_DIGITS - 1:
        raise AirGapSigningError("amount exceeds the uint256 wei range")
    # Computed exactly from the digit tuple: Decimal arithmetic would round to
    # the context precision and could misjudge an amount at the uint256 edge.
    wei = int("".join(map(str, digits))) * 10 ** (exponent + _WEI_DECIMALS)
    if wei > _MAX_WEI:
        raise AirGapSigningError("amount exceeds the uint256 wei range")
    return format(value, "f")


def _decode_json_object(
    raw: str, expected_fields: FrozenSet[str], label: str
) -> Dict[str, Any]:
    """Strictly decode a JSON object with an exact field set.

    An exact-set comparison rejects both missing and unexpected fields. Ignoring
    unknown fields would let media carry data past the approver's display.
    """
    if not isinstance(raw, str):
        raise AirGapSigningError(f"{label} data must be text")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise AirGapSigningError(f"malformed {label} data") from exc
    if not isinstance(data, dict) or set(data) != set(expected_fields):
        raise AirGapSigningError(f"{label} fields are invalid")
    return data


class OfflineAirGappedSigner:
    """Offline vault boundary; no network connection is modelled or permitted.

    The vault, not the coordinator, is the enforcement point. It applies its own
    policy, requires explicit human approval of a display it derives from the
    bytes it will sign, and refuses to sign the same intent twice.
    """

    def __init__(
        self,
        vault_private_key: str,
        key_id: str = "vault-primary",
        approval_callback: Optional[Callable[[str], bool]] = None,
        expected_chain_id: Optional[int] = None,
        max_amount: Optional[str] = None,
        allowed_destinations: Optional[Iterable[str]] = None,
        enforce_monotonic_nonce: bool = True,
    ):
        if not isinstance(vault_private_key, str) or not vault_private_key:
            raise AirGapSigningError("vault_private_key is required")
        if not isinstance(key_id, str) or not key_id.strip():
            raise AirGapSigningError("key_id must be a non-empty identifier")
        if expected_chain_id is not None and not _is_bounded_int(
            expected_chain_id, 1, _MAX_NONCE
        ):
            raise AirGapSigningError("expected_chain_id must be a positive integer")
        self._private_key = vault_private_key
        self.key_id = key_id
        self._approval_callback = approval_callback
        self._expected_chain_id = expected_chain_id
        self._max_amount = None if max_amount is None else Decimal(_canonical_amount(max_amount))
        self._allowed_destinations = (
            None
            if allowed_destinations is None
            else {addr.lower() for addr in allowed_destinations}
        )
        self._enforce_monotonic_nonce = enforce_monotonic_nonce
        self._signed_hashes: Set[str] = set()
        self._highest_signed_nonce = 0
        # Symmetric, and therefore forgeable by whoever holds it. See the module
        # docstring: this is a test seam, not a public key.
        self.verification_key = hashlib.sha256(
            vault_private_key.encode("utf-8")
        ).hexdigest()

    def _passes_policy(self, payload: UnsignedPayload) -> bool:
        """Apply vault-local policy before a human is ever asked to approve."""
        if self._expected_chain_id is not None and payload.chain_id != self._expected_chain_id:
            logger.error(
                "Vault rejected chain_id=%s; this vault signs only chain_id=%s",
                payload.chain_id,
                self._expected_chain_id,
            )
            return False
        if (
            self._allowed_destinations is not None
            and payload.destination_address.lower() not in self._allowed_destinations
        ):
            logger.error("Vault rejected destination outside the allowlist")
            return False
        if self._max_amount is not None and Decimal(payload.amount) > self._max_amount:
            logger.error("Vault rejected amount above the vault policy ceiling")
            return False
        return True

    def _passes_replay_checks(self, payload: UnsignedPayload, payload_hash: str) -> bool:
        """Reject re-signing, independently of any coordinator-side ledger.

        The coordinator is the assumed adversary, so its replay protection
        cannot be relied on. Without this, a compromised coordinator can harvest
        unlimited signatures over an intent a human approved once.
        """
        if payload_hash in self._signed_hashes:
            logger.error("Vault rejected an intent it has already signed")
            return False
        if self._enforce_monotonic_nonce and payload.nonce <= self._highest_signed_nonce:
            logger.error(
                "Vault rejected nonce=%s at or below the highest signed nonce=%s",
                payload.nonce,
                self._highest_signed_nonce,
            )
            return False
        return True

    def _obtain_human_approval(self, payload: UnsignedPayload) -> bool:
        """Fail closed unless an approver explicitly returns ``True``.

        A vault with no approval seam wired is a blind-signing oracle, so the
        default is denial. The callback is handed the display derived from this
        exact payload; approving anything else is not possible from here.
        """
        if self._approval_callback is None:
            logger.error(
                "Vault denied signing: no approval_callback wired, refusing to blind sign"
            )
            return False
        try:
            decision = self._approval_callback(payload.clear_signing_display())
        except Exception:  # noqa: BLE001 - an approver that errors has not approved
            logger.exception("Vault denied signing: approval callback raised")
            return False
        # Strict identity: a truthy sentinel or stub must not count as approval.
        if decision is not True:
            logger.error("Vault denied signing: approver did not approve")
            return False
        return True

    def sign_qr_payload(self, qr_data: str) -> Optional[SignedPayload]:
        """Validate, display, obtain approval, and sign. ``None`` on any refusal."""
        try:
            payload = UnsignedPayload.from_qr_code_data(qr_data)
        except AirGapSigningError:
            logger.error("Vault rejected malformed QR data")
            return None
        payload_hash = payload.payload_hash()
        if not self._passes_policy(payload):
            return None
        if not self._passes_replay_checks(payload, payload_hash):
            return None
        if not self._obtain_human_approval(payload):
            return None
        signature = hmac.new(
            self.verification_key.encode("ascii"),
            payload_hash.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        self._signed_hashes.add(payload_hash)
        self._highest_signed_nonce = max(self._highest_signed_nonce, payload.nonce)
        logger.info("Vault signed approved payload nonce=%s", payload.nonce)
        return SignedPayload(
            payload.to_qr_code_data(), payload_hash, signature, self.key_id
        )


class OnlineCoordinator:
    """Online intent and broadcast boundary; never holds the vault private key."""

    def __init__(
        self,
        verification_key: Optional[str] = None,
        signer_key_id: str = "vault-primary",
        starting_nonce: int = 0,
        chain_id: int = 1,
        broadcast_adapter: Optional[Callable[[UnsignedPayload], str]] = None,
    ):
        if verification_key is not None and (
            not isinstance(verification_key, str) or not verification_key.isascii()
        ):
            raise AirGapSigningError("verification_key must be ASCII text")
        if not _is_bounded_int(starting_nonce, 0, _MAX_NONCE - 1):
            raise AirGapSigningError("starting_nonce must be a bounded integer")
        if not _is_bounded_int(chain_id, 1, _MAX_NONCE):
            raise AirGapSigningError("chain_id must be a positive bounded integer")
        self.nonce_counter = starting_nonce
        self.verification_key = verification_key
        self.signer_key_id = signer_key_id
        self.chain_id = chain_id
        self._broadcast_adapter = broadcast_adapter
        self._issued: Dict[str, UnsignedPayload] = {}
        self._invalidated: Set[str] = set()
        self._consumed: Set[str] = set()
        self._unresolved: Set[str] = set()

    def create_unsigned_transfer(self, address: str, amount: Any) -> UnsignedPayload:
        """Issue a validated intent, consuming a nonce only once it is valid.

        The counter advances after construction succeeds. Incrementing first
        burns a nonce on every rejected address, and a gap in a chain nonce
        sequence stalls every later transaction from that account.
        """
        payload = UnsignedPayload(
            address, str(amount), _SUPPORTED_NETWORK, self.chain_id, self.nonce_counter + 1
        )
        self.nonce_counter += 1
        self._issued[payload.payload_hash()] = payload
        return payload

    def invalidate_intent(self, payload_hash: str) -> bool:
        """Void an issued intent whose media was lost, damaged, or quarantined.

        The nonce is not reused: the replacement intent takes a fresh one, so a
        recovered copy of the old media can never be broadcast.
        """
        if payload_hash not in self._issued or payload_hash in self._consumed:
            return False
        self._invalidated.add(payload_hash)
        logger.warning("Intent invalidated payload_hash=%s", payload_hash)
        return True

    @property
    def unresolved_payload_hashes(self) -> FrozenSet[str]:
        """Payloads dispatched to the adapter whose outcome is still unknown."""
        return frozenset(self._unresolved)

    def resolve_unresolved(self, payload_hash: str, landed_on_chain: bool) -> bool:
        """Close out an unresolved broadcast against authoritative chain evidence.

        ``landed_on_chain`` must come from querying the chain, never from
        assuming the earlier timeout meant failure. The payload stays consumed
        either way; a transfer that genuinely never landed needs a *new* intent
        with a new nonce, not a resubmission of the old envelope.
        """
        if payload_hash not in self._unresolved:
            return False
        self._unresolved.discard(payload_hash)
        logger.warning(
            "Unresolved broadcast reconciled payload_hash=%s landed=%s",
            payload_hash,
            landed_on_chain,
        )
        return True

    def _verify_envelope(
        self, signed_payload: SignedPayload
    ) -> Tuple[BroadcastResult, Optional[UnsignedPayload]]:
        """Verify binding, provenance, freshness and signature. Fail closed."""
        try:
            payload = UnsignedPayload.from_qr_code_data(signed_payload.unsigned_payload)
        except AirGapSigningError:
            return _rejected("malformed signed payload"), None
        payload_hash = payload.payload_hash()
        if payload_hash != signed_payload.original_payload_hash:
            return _rejected("payload binding mismatch"), None
        if payload_hash not in self._issued:
            return _rejected("intent was not issued here"), None
        if payload.chain_id != self.chain_id:
            return _rejected("chain id mismatch"), None
        if payload_hash in self._invalidated:
            return _rejected("intent was invalidated"), None
        if signed_payload.signer_key_id != self.signer_key_id:
            return _rejected("unknown signer key id"), None
        if payload_hash in self._consumed:
            return _rejected("payload already submitted", payload_hash), None
        expected = hmac.new(
            self.verification_key.encode("ascii"),
            payload_hash.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signed_payload.signature):
            return _rejected("signature verification failed"), None
        return (
            BroadcastResult(BroadcastStatus.ACCEPTED, "verified", payload_hash),
            payload,
        )

    def broadcast_to_network(
        self, signed_payload: Optional[SignedPayload]
    ) -> BroadcastResult:
        """Verify a returned envelope and submit it exactly once.

        The payload is marked consumed *before* the adapter is called, so a
        crash or timeout mid-dispatch can never be retried into a second
        submission. Any adapter failure yields ``UNRESOLVED``, never
        ``REJECTED``: the node may have accepted the transaction before the
        response was lost.
        """
        if signed_payload is None:
            return _rejected("no signed payload")
        if not isinstance(signed_payload, SignedPayload):
            return _rejected("envelope has the wrong type")
        if not self.verification_key:
            return _rejected("no verification key configured")
        if self._broadcast_adapter is None:
            return _rejected("no broadcast adapter configured")

        verdict, payload = self._verify_envelope(signed_payload)
        if payload is None:
            logger.error("Broadcast rejected: %s", verdict.reason)
            return verdict

        payload_hash = payload.payload_hash()
        self._consumed.add(payload_hash)
        try:
            reference = self._broadcast_adapter(payload)
        except Exception:  # noqa: BLE001 - a lost response is unknown, not failed
            self._unresolved.add(payload_hash)
            logger.exception(
                "Broadcast unresolved payload_hash=%s; reconcile against chain state",
                payload_hash,
            )
            return BroadcastResult(
                BroadcastStatus.UNRESOLVED, "adapter raised; outcome unknown", payload_hash
            )
        if not isinstance(reference, str) or not reference.strip():
            self._unresolved.add(payload_hash)
            logger.error(
                "Broadcast unresolved payload_hash=%s; adapter returned no reference",
                payload_hash,
            )
            return BroadcastResult(
                BroadcastStatus.UNRESOLVED, "adapter returned no reference", payload_hash
            )
        logger.info("Broadcast accepted for payload nonce=%s", payload.nonce)
        return BroadcastResult(
            BroadcastStatus.ACCEPTED, "submitted", payload_hash, reference
        )
