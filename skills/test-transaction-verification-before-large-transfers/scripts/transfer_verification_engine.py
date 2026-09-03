"""test-transaction-verification-before-large-transfers: a policy gate that decides
whether a high-value crypto transfer has earned authorisation to be signed.

What this module is and is not
------------------------------
It is a **local, in-process policy gate that runs before your signing code**. It
performs no network I/O: it never queries an RPC node, never calls a custody API,
and never broadcasts anything. Every on-chain fact it reasons about is supplied by
the caller, who is responsible for having read it from a trusted source.

It is **not** the enforcer. Fireblocks, BitGo, Coinbase Custody or your Safe policy
module remain authoritative — if the custody platform refuses a transfer, that
refusal stands regardless of what this engine returned. The value of a local gate
is that it fails *before* a signed payload leaves your infrastructure, and that it
leaves an auditable record of why.

Why confirmation depth alone is not the control
-----------------------------------------------
This is the single most important thing to understand about test transfers, and
the reason this module requires more than block depth before it will approve.

A dust transaction sent to a *wrong* address — a typo, a clipboard-poisoned
address, an attacker-substituted deposit address — confirms to 12 blocks exactly
as reliably as one sent to the correct address. Confirmation depth proves only
that the network accepted a transfer to whatever address was in it. It proves
nothing whatsoever about that address belonging to the intended recipient.

The control that actually detects a wrong address is the **counterparty
confirming, out of band, that the funds arrived**. That is what CCSS v9 requires
in `1.05.8.1` Spend Verification: "Verification of fund destinations and amounts
is performed via Approved Communication Channels prior to the use of key
material." It is also the step Fireblocks describes in its own test-transfer
runbook — the operations team contacts the counterparty, and only completes the
full transaction once receipt has been confirmed.

So ``verify_and_authorize_large_transfer`` requires, by default, an explicit
recorded receipt attestation (:meth:`acknowledge_test_receipt`). Setting
``require_counterparty_receipt=False`` downgrades this module to a depth-only
check, which is a materially weaker control; see "When NOT to Use" in SKILL.md.

Test transfers are not foolproof
--------------------------------
Fireblocks documents malware that lets the initial test transfer succeed and
*then* swaps the deposit address before the main transfer. This engine mitigates
that specific attack by binding the test transaction to the recipient address
recorded on the request and re-checking that binding, the whitelist, and the
expiry window at the final authorisation gate — but a test transfer remains a
detection control, not a guarantee. It reduces address risk; it does not
eliminate it.

Trusted clock
-------------
Every time-sensitive method accepts an optional ``now`` argument and defaults to
``datetime.datetime.now(datetime.timezone.utc)``. The expiry window is always
measured from the moment the test transaction *first* reached the required depth,
which is latched once and never refreshed by subsequent polling. A window that a
monitoring loop can extend by continuing to poll is not a window.
"""
from __future__ import annotations

import datetime
import logging
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

__all__ = [
    "VerificationStatus",
    "RiskLevel",
    "VerificationError",
    "WhitelistError",
    "TestTransactionExpiredError",
    "TestTransactionPendingError",
    "TestTransactionMismatchError",
    "AssetConfig",
    "VerificationConfig",
    "TransferRequest",
    "TestTransaction",
    "VerificationResult",
    "TransferVerificationEngine",
    "canonicalize_address",
]

#: An EVM address: `0x` followed by 40 hex digits.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
#: Bech32/bech32m shape (BIP-173/BIP-350): a letters-only human-readable part, the
#: `1` separator, then the data part over the bech32 charset (no `1`, `b`, `i`, `o`).
_BECH32_SHAPE_RE = re.compile(r"^[a-z]{1,83}1[ac-hj-np-z02-9]{6,}$")

#: Relative tolerance used when comparing a caller-observed on-chain amount against
#: the configured dust amount. Amounts arrive as floats after passing through
#: integer base units and JSON, so exact equality would reject correct transfers.
AMOUNT_MATCH_RELATIVE_TOLERANCE = 1e-9


class VerificationStatus(Enum):
    NOT_REQUIRED = "NOT_REQUIRED"      # Transfer below large threshold
    TEST_PENDING = "TEST_PENDING"      # Test transaction awaiting depth / receipt
    TEST_CONFIRMED = "TEST_CONFIRMED"  # Test transaction reached required depth on-chain
    RECEIPT_ACKNOWLEDGED = "RECEIPT_ACKNOWLEDGED"  # Counterparty attested arrival out of band
    APPROVED = "APPROVED"              # Primary large transfer authorised for execution
    EXPIRED = "EXPIRED"                # Test verification window elapsed
    REJECTED = "REJECTED"              # Failed verification or policy violation


class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VerificationError(Exception):
    """Base exception for transfer verification errors.

    This gate raises rather than returning a non-approving report for structural
    faults (NaN notional, empty address, unregistered asset). An exception cannot
    be misread as approval; a report can, if the caller checks the wrong field.
    """


class WhitelistError(VerificationError):
    """Raised when the recipient address is not on the approved whitelist."""


class TestTransactionExpiredError(VerificationError):
    """Raised when the time between test confirmation and primary transfer exceeds the window."""


class TestTransactionPendingError(VerificationError):
    """Raised when a primary transfer is attempted before the test tx is fully verified."""


class TestTransactionMismatchError(VerificationError):
    """Raised when the observed test transaction does not match the transfer request.

    A test transaction that landed at a different address, on a different chain, or
    for a different amount than the request under authorisation verifies nothing
    about that request. Treating it as verification is the exact failure this
    module exists to prevent.
    """


def canonicalize_address(address: str) -> str:
    """Return the whitelist lookup key for ``address``.

    Case is folded **only** where the address encoding is genuinely
    case-insensitive. Folding case on a case-sensitive encoding maps distinct
    addresses onto one whitelist key, which turns the whitelist into a control
    that approves addresses nobody ever whitelisted:

    * **EVM hex** (``0x`` + 40 hex digits) — case-insensitive; ERC-55 uses
      capitalisation purely as a checksum. Folded to lowercase so a checksummed
      request matches a lowercase whitelist entry rather than being falsely
      rejected.
    * **All-uppercase bech32/bech32m** — BIP-173 forbids mixed case and treats the
      all-uppercase form (used in QR codes) as the same address as the canonical
      lowercase form. Folded to lowercase.
    * **Everything else** — Base58Check (Bitcoin legacy, XRP), Solana base58 and
      TON base64 are case-**sensitive**. Returned byte-exact, never folded.

    A mixed-case string is never treated as bech32, so Base58Check addresses (which
    are almost always mixed case) always take the byte-exact path.
    """
    if not isinstance(address, str):
        raise VerificationError(
            f"address must be a string, got {type(address).__name__}")
    stripped = address.strip()
    if not stripped:
        raise VerificationError("address must be a non-empty string")
    if _EVM_ADDRESS_RE.match(stripped):
        return stripped.lower()
    if stripped.isupper() and _BECH32_SHAPE_RE.match(stripped.lower()):
        return stripped.lower()
    return stripped


def _require_finite(value: object, name: str, *, minimum: Optional[float] = None,
                    exclusive_minimum: Optional[float] = None) -> float:
    """Coerce ``value`` to a finite float or raise.

    ``bool`` is rejected explicitly: it is a subclass of ``int``, so a stray
    ``True`` would otherwise silently become an amount of 1.0.

    NaN is the case that matters most here. ``float('nan') >= threshold`` is
    ``False`` under IEEE-754, so an unvalidated NaN notional — entirely plausible
    when a price feed returns no quote — would classify a transfer as *below* the
    large-transfer threshold and authorise it directly, bypassing this module's
    entire reason to exist.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VerificationError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise VerificationError(f"{name} must be finite, got {numeric!r}")
    if exclusive_minimum is not None and numeric <= exclusive_minimum:
        raise VerificationError(f"{name} must be > {exclusive_minimum}, got {numeric!r}")
    if minimum is not None and numeric < minimum:
        raise VerificationError(f"{name} must be >= {minimum}, got {numeric!r}")
    return numeric


def _require_int(value: object, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise VerificationError(f"{name} must be an integer, got {value!r}")
    if value < minimum:
        raise VerificationError(f"{name} must be >= {minimum}, got {value!r}")
    return value


def _require_token(value: object, name: str) -> str:
    """Normalise a symbol/chain identifier: non-empty, stripped, upper-cased."""
    if not isinstance(value, str) or not value.strip():
        raise VerificationError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip().upper()


def _normalize_tag(tag: Optional[str]) -> Optional[str]:
    """Normalise a destination tag/memo; whitespace-only is treated as absent.

    An empty tag and a missing tag are the same failure at the custodian: the
    deposit arrives uncredited. They must not be distinguished here.
    """
    if tag is None:
        return None
    if not isinstance(tag, str):
        raise VerificationError(f"destination_tag must be a string or None, got {tag!r}")
    stripped = tag.strip()
    return stripped or None


def _amounts_match(observed: float, expected: float) -> bool:
    """Compare an observed on-chain amount against the configured dust amount."""
    return math.isclose(observed, expected, rel_tol=AMOUNT_MATCH_RELATIVE_TOLERANCE,
                        abs_tol=0.0)


@dataclass
class AssetConfig:
    """Per-asset security parameters.

    ``min_confirmations`` is **policy, not a protocol constant**. Venues differ
    materially for the same asset (Kraken requires 4 confirmations for BTC and 20
    for ETH; Coinbase 2 and 14 respectively), and on chains with deterministic
    finality — XRP Ledger, TON, Solana at the ``finalized`` commitment — "depth"
    means "reached finality", not "survived N probabilistic blocks". Set this from
    your custodian's published requirement, never from a remembered default. See
    ``references/standards.md``.
    """
    symbol: str                    # e.g. 'ETH', 'BTC', 'USDT'
    chain: str                     # e.g. 'ETHEREUM', 'BITCOIN'
    decimals: int = 18
    min_confirmations: int = 12
    test_amount: float = 0.001     # Dust amount for the test transfer
    requires_destination_tag: bool = False

    def __post_init__(self) -> None:
        self.symbol = _require_token(self.symbol, "AssetConfig.symbol")
        self.chain = _require_token(self.chain, "AssetConfig.chain")
        self.decimals = _require_int(self.decimals, "AssetConfig.decimals", minimum=0)
        self.min_confirmations = _require_int(
            self.min_confirmations, "AssetConfig.min_confirmations", minimum=1)
        # A zero test amount would confirm on-chain while moving nothing, so the
        # counterparty would have nothing to acknowledge and the test would prove
        # nothing.
        self.test_amount = _require_finite(
            self.test_amount, "AssetConfig.test_amount", exclusive_minimum=0.0)
        if not isinstance(self.requires_destination_tag, bool):
            raise VerificationError("AssetConfig.requires_destination_tag must be a bool")


@dataclass
class VerificationConfig:
    """Engine-wide policy.

    ``allow_bypass_for_whitelisted`` is a **safety-critical escape hatch**: when
    enabled, a whitelisted recipient skips the test transfer entirely regardless of
    notional. It defaults to ``False`` and every use is logged at WARNING. A
    previously used address is not evidence of a safe address — clipboard malware,
    a spoofed custodian rotation notice, or a compromised address book all present
    as "an address we have used before".
    """
    large_transfer_threshold_usd: float = 50_000.0
    test_expiry_window_minutes: float = 30.0
    enforce_whitelisting: bool = True
    allow_bypass_for_whitelisted: bool = False
    #: Require a recorded out-of-band counterparty receipt attestation before
    #: approving. Disabling this reduces the gate to confirmation depth alone,
    #: which does not detect a wrong destination address. See the module docstring.
    require_counterparty_receipt: bool = True
    #: Tolerated backwards clock movement, in seconds, before a negative elapsed
    #: time is treated as a fault rather than as rounding.
    max_clock_skew_seconds: float = 60.0

    def __post_init__(self) -> None:
        self.large_transfer_threshold_usd = _require_finite(
            self.large_transfer_threshold_usd,
            "VerificationConfig.large_transfer_threshold_usd", minimum=0.0)
        self.test_expiry_window_minutes = _require_finite(
            self.test_expiry_window_minutes,
            "VerificationConfig.test_expiry_window_minutes", minimum=0.0)
        self.max_clock_skew_seconds = _require_finite(
            self.max_clock_skew_seconds,
            "VerificationConfig.max_clock_skew_seconds", minimum=0.0)


@dataclass
class TransferRequest:
    """The primary (large) transfer awaiting authorisation."""
    request_id: str
    asset_symbol: str
    sender_address: str
    recipient_address: str
    amount: float
    value_usd: float
    destination_tag: Optional[str] = None
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise VerificationError(
                f"TransferRequest.request_id must be a non-empty string, "
                f"got {self.request_id!r}")
        self.request_id = self.request_id.strip()
        self.asset_symbol = _require_token(self.asset_symbol, "TransferRequest.asset_symbol")
        # Addresses are canonicalised, not upper-cased: most encodings are
        # case-sensitive. See canonicalize_address.
        self.sender_address = canonicalize_address(self.sender_address)
        self.recipient_address = canonicalize_address(self.recipient_address)
        self.amount = _require_finite(
            self.amount, "TransferRequest.amount", exclusive_minimum=0.0)
        self.value_usd = _require_finite(
            self.value_usd, "TransferRequest.value_usd", minimum=0.0)
        self.destination_tag = _normalize_tag(self.destination_tag)


@dataclass
class TestTransaction:
    """A dust test transfer, bound to the request it is meant to verify.

    ``observed_*`` fields are what the caller actually read back from the chain or
    custody API for ``tx_hash``. They exist so the engine can prove the test landed
    at the recipient the primary transfer is going to, rather than assuming it.
    """
    request_id: str
    tx_hash: str
    amount_sent: float
    observed_recipient: str
    observed_chain: str
    observed_amount: float
    confirmations: int = 0
    status: VerificationStatus = VerificationStatus.TEST_PENDING
    initiated_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    #: Latched the first time the required depth is reached, and never refreshed
    #: by later polling. The expiry window runs from this instant.
    confirmed_at: Optional[datetime.datetime] = None
    receipt_acknowledged_by: Optional[str] = None
    receipt_acknowledged_at: Optional[datetime.datetime] = None
    receipt_channel: Optional[str] = None


@dataclass
class VerificationResult:
    request_id: str
    is_approved: bool
    status: VerificationStatus
    risk_level: RiskLevel
    message: str
    test_tx_hash: Optional[str] = None
    audit_trail: List[str] = field(default_factory=list)


class TransferVerificationEngine:
    """Pre-signing verification gate for high-value crypto transfers.

    Enforces, in order: recipient whitelisting, destination tag presence, a
    notional threshold, a dust test transfer bound to the request's recipient and
    chain, on-chain confirmation depth, an out-of-band counterparty receipt
    attestation, and a time-decay authorisation window. Authorisation is
    single-use.

    The engine performs no network I/O. Confirmation counts, observed recipients
    and receipt attestations are all supplied by the caller.
    """

    def __init__(self, config: VerificationConfig):
        if not isinstance(config, VerificationConfig):
            raise VerificationError(
                f"config must be a VerificationConfig, got {type(config).__name__}")
        self.config = config
        self.asset_configs: Dict[str, AssetConfig] = {}
        self.whitelist: Set[str] = set()
        self.pending_requests: Dict[str, TransferRequest] = {}
        self.test_transactions: Dict[str, TestTransaction] = {}
        #: Request IDs whose authorisation has already been issued and spent.
        self.consumed_requests: Set[str] = set()
        logger.info(
            "VERIFICATION ENGINE INIT: threshold=%.2f USD window=%.1f min "
            "receipt_required=%s bypass_enabled=%s",
            config.large_transfer_threshold_usd, config.test_expiry_window_minutes,
            config.require_counterparty_receipt, config.allow_bypass_for_whitelisted)
        if config.allow_bypass_for_whitelisted:
            logger.warning(
                "VERIFICATION ENGINE INIT: allow_bypass_for_whitelisted is ENABLED — "
                "whitelisted recipients skip the mandatory test transfer at any notional.")
        if not config.require_counterparty_receipt:
            logger.warning(
                "VERIFICATION ENGINE INIT: require_counterparty_receipt is DISABLED — "
                "approval will rest on confirmation depth alone, which does not detect "
                "a wrong destination address.")

    # ---------------------------------------------------------------- registry

    def register_asset(self, asset_cfg: AssetConfig) -> None:
        """Register asset security parameters and confirmation requirements."""
        if not isinstance(asset_cfg, AssetConfig):
            raise VerificationError(
                f"asset_cfg must be an AssetConfig, got {type(asset_cfg).__name__}")
        self.asset_configs[asset_cfg.symbol] = asset_cfg
        logger.info("ASSET REGISTERED [%s:%s]: min_confirmations=%d test_amount=%s tag=%s",
                    asset_cfg.symbol, asset_cfg.chain, asset_cfg.min_confirmations,
                    asset_cfg.test_amount, asset_cfg.requires_destination_tag)

    def add_to_whitelist(self, address: str) -> None:
        """Add a recipient address to the approved whitelist."""
        key = canonicalize_address(address)
        self.whitelist.add(key)
        logger.info("WHITELIST ADD [%s]", key)

    def remove_from_whitelist(self, address: str) -> bool:
        """Revoke a whitelisted address. Returns ``True`` if an entry was removed.

        Revocation takes effect immediately, including for requests already in
        flight: :meth:`verify_and_authorize_large_transfer` re-checks membership at
        the authorisation gate rather than trusting the check made at initiation.
        """
        key = canonicalize_address(address)
        removed = key in self.whitelist
        self.whitelist.discard(key)
        if removed:
            logger.warning("WHITELIST REVOKE [%s]", key)
        else:
            logger.info("WHITELIST REVOKE NO-OP [%s]: not present", key)
        return removed

    def is_whitelisted(self, address: str) -> bool:
        return canonicalize_address(address) in self.whitelist

    def is_large_transfer(self, value_usd: float) -> bool:
        """Whether ``value_usd`` breaches the large-transfer threshold.

        The comparison is ``>=``: a transfer exactly at the threshold is treated as
        large. NaN and infinite notionals are rejected rather than compared, since
        ``float('nan') >= threshold`` is ``False`` and would silently bypass.
        """
        numeric = _require_finite(value_usd, "value_usd", minimum=0.0)
        return numeric >= self.config.large_transfer_threshold_usd

    # ------------------------------------------------------------- initiation

    def initiate_transfer_request(self, request: TransferRequest) -> VerificationResult:
        """Evaluate a transfer request and decide whether a test transfer is mandatory."""
        if not isinstance(request, TransferRequest):
            raise VerificationError(
                f"request must be a TransferRequest, got {type(request).__name__}")

        audit = [f"Received transfer request {request.request_id} for {request.amount} "
                 f"{request.asset_symbol} (${request.value_usd:,.2f} USD)"]

        if request.request_id in self.consumed_requests:
            raise VerificationError(
                f"Request ID {request.request_id} has already been authorised and "
                f"consumed. Issue a new request ID rather than reusing a spent "
                f"authorisation.")

        # 1. Asset must be registered before any policy can be applied to it.
        if request.asset_symbol not in self.asset_configs:
            raise VerificationError(
                f"Asset symbol '{request.asset_symbol}' is not registered.")
        asset_cfg = self.asset_configs[request.asset_symbol]

        # 2. Whitelist validation.
        if self.config.enforce_whitelisting and not self.is_whitelisted(request.recipient_address):
            msg = f"Recipient address {request.recipient_address} is not whitelisted."
            audit.append(f"REJECTED: {msg}")
            logger.error("REJECTED [%s]: %s", request.request_id, msg)
            raise WhitelistError(msg)

        # 3. Destination tag / memo check.
        if asset_cfg.requires_destination_tag and not request.destination_tag:
            msg = f"Asset {request.asset_symbol} requires a destination memo/tag."
            logger.error("REJECTED [%s]: %s", request.request_id, msg)
            raise VerificationError(msg)

        if request.request_id in self.test_transactions:
            # The prior test still has to survive the binding re-check at the
            # authorisation gate, so a changed recipient or chain fails closed. A
            # re-submission under the same ID is still worth surfacing: the expiry
            # window continues to run from the original confirmation, not from now.
            logger.warning(
                "REQUEST RESUBMITTED [%s]: a test transaction (%s) is already on record "
                "for this ID and will be reused; its expiry window is unchanged.",
                request.request_id, self.test_transactions[request.request_id].tx_hash)

        self.pending_requests[request.request_id] = request

        # 4. Threshold.
        if not self.is_large_transfer(request.value_usd):
            audit.append("Transfer below threshold. No test transaction required.")
            return VerificationResult(
                request_id=request.request_id,
                is_approved=True,
                status=VerificationStatus.NOT_REQUIRED,
                risk_level=RiskLevel.LOW,
                message="Transfer below large value threshold. Direct execution authorized.",
                audit_trail=audit,
            )

        # 5. Configured escape hatch.
        if self.config.allow_bypass_for_whitelisted and self.is_whitelisted(request.recipient_address):
            msg = ("Test transfer BYPASSED for whitelisted recipient "
                   f"{request.recipient_address} at ${request.value_usd:,.2f} USD.")
            audit.append(msg)
            logger.warning("BYPASS [%s]: %s", request.request_id, msg)
            return VerificationResult(
                request_id=request.request_id,
                is_approved=True,
                status=VerificationStatus.APPROVED,
                # A bypassed test transfer is a high-risk approval, not a medium
                # one: nothing has verified the destination for this notional.
                risk_level=RiskLevel.HIGH,
                message="Whitelisted recipient bypass granted (test transfer skipped).",
                audit_trail=audit,
            )

        audit.append(
            f"Mandatory dust test transaction required: send {asset_cfg.test_amount} "
            f"{request.asset_symbol} on {asset_cfg.chain} to {request.recipient_address}")
        logger.info("TEST REQUIRED [%s]: %s", request.request_id, audit[-1])
        return VerificationResult(
            request_id=request.request_id,
            is_approved=False,
            status=VerificationStatus.TEST_PENDING,
            risk_level=RiskLevel.HIGH,
            message=f"Mandatory test transaction required "
                    f"({asset_cfg.test_amount} {request.asset_symbol}).",
            audit_trail=audit,
        )

    # ------------------------------------------------------- test transaction

    def _require_pending(self, request_id: str) -> TransferRequest:
        """Resolve a request that is still open, or raise."""
        if not isinstance(request_id, str) or not request_id.strip():
            raise VerificationError(
                f"request_id must be a non-empty string, got {request_id!r}")
        key = request_id.strip()
        if key in self.consumed_requests:
            raise VerificationError(
                f"Request ID {key} has already been authorised and consumed.")
        if key not in self.pending_requests:
            raise VerificationError(f"Request ID {key} not found in pending requests.")
        return self.pending_requests[key]

    def record_test_transaction(
        self,
        request_id: str,
        tx_hash: str,
        observed_recipient: str,
        observed_chain: str,
        observed_amount: float,
        now: Optional[datetime.datetime] = None,
    ) -> TestTransaction:
        """Record a broadcast dust test transfer and bind it to the request.

        ``observed_recipient``, ``observed_chain`` and ``observed_amount`` must be
        read back from the chain or custody API for ``tx_hash`` — not copied from
        the same variable that built the transfer. The point of reading them back
        is to catch the case where the address that was *signed* differs from the
        address that was *intended*, which is the whole threat model. A mismatch
        raises :class:`TestTransactionMismatchError`.

        Recording a second test transaction for a request replaces the first and
        resets it to ``TEST_PENDING``, discarding any prior receipt attestation.
        """
        req = self._require_pending(request_id)
        asset_cfg = self.asset_configs[req.asset_symbol]

        if not isinstance(tx_hash, str) or not tx_hash.strip():
            raise VerificationError(f"tx_hash must be a non-empty string, got {tx_hash!r}")
        clean_hash = tx_hash.strip()

        observed_key = canonicalize_address(observed_recipient)
        observed_chain_token = _require_token(observed_chain, "observed_chain")
        observed_amt = _require_finite(
            observed_amount, "observed_amount", exclusive_minimum=0.0)

        if observed_key != req.recipient_address:
            msg = (f"Test transaction {clean_hash} landed at {observed_key}, but request "
                   f"{req.request_id} is addressed to {req.recipient_address}. "
                   f"This test verifies nothing about the primary transfer.")
            logger.error("MISMATCH [%s]: %s", req.request_id, msg)
            raise TestTransactionMismatchError(msg)

        if observed_chain_token != asset_cfg.chain:
            msg = (f"Test transaction {clean_hash} was observed on {observed_chain_token}, "
                   f"but asset {asset_cfg.symbol} is configured for {asset_cfg.chain}. "
                   f"Sending to an approved address on the wrong network loses the funds.")
            logger.error("MISMATCH [%s]: %s", req.request_id, msg)
            raise TestTransactionMismatchError(msg)

        if not _amounts_match(observed_amt, asset_cfg.test_amount):
            msg = (f"Test transaction {clean_hash} moved {observed_amt} {asset_cfg.symbol}, "
                   f"but the configured test amount is {asset_cfg.test_amount}.")
            logger.error("MISMATCH [%s]: %s", req.request_id, msg)
            raise TestTransactionMismatchError(msg)

        if req.request_id in self.test_transactions:
            logger.warning(
                "TEST REPLACED [%s]: superseding %s with %s; prior confirmations and "
                "receipt attestation discarded.",
                req.request_id, self.test_transactions[req.request_id].tx_hash, clean_hash)

        test_tx = TestTransaction(
            request_id=req.request_id,
            tx_hash=clean_hash,
            amount_sent=asset_cfg.test_amount,
            observed_recipient=observed_key,
            observed_chain=observed_chain_token,
            observed_amount=observed_amt,
            status=VerificationStatus.TEST_PENDING,
            initiated_at=self._resolve_now(now),
        )
        self.test_transactions[req.request_id] = test_tx
        logger.info("TEST RECORDED [%s]: hash=%s to=%s on=%s amount=%s",
                    req.request_id, clean_hash, observed_key, observed_chain_token,
                    observed_amt)
        return test_tx

    def update_test_confirmations(
        self,
        request_id: str,
        confirmations: int,
        now: Optional[datetime.datetime] = None,
    ) -> TestTransaction:
        """Update the on-chain confirmation depth of a recorded test transaction.

        ``confirmed_at`` is latched the **first** time the required depth is
        reached and is not refreshed by subsequent calls. Refreshing it on every
        poll would let a monitoring loop hold the expiry window open indefinitely,
        so a test transfer confirmed hours ago would still authorise a transfer.

        A drop below the required depth — which is what a chain re-org looks like
        from the caller's side — reverts the transaction to ``TEST_PENDING`` and
        clears ``confirmed_at``, so the window restarts from the re-confirmation
        rather than running from a depth that no longer exists.
        """
        req = self._require_pending(request_id)
        key = req.request_id
        if key not in self.test_transactions:
            raise VerificationError(f"No test transaction recorded for request ID {key}")

        depth = _require_int(confirmations, "confirmations", minimum=0)
        asset_cfg = self.asset_configs[req.asset_symbol]
        test_tx = self.test_transactions[key]
        moment = self._resolve_now(now)

        previous = test_tx.confirmations
        test_tx.confirmations = depth

        if depth >= asset_cfg.min_confirmations:
            if test_tx.confirmed_at is None:
                test_tx.confirmed_at = moment
                logger.info("TEST CONFIRMED [%s]: %s reached %d/%d confirmations.",
                            key, test_tx.tx_hash, depth, asset_cfg.min_confirmations)
            test_tx.status = (
                VerificationStatus.RECEIPT_ACKNOWLEDGED
                if test_tx.receipt_acknowledged_at is not None
                else VerificationStatus.TEST_CONFIRMED
            )
        else:
            if test_tx.confirmed_at is not None:
                logger.warning(
                    "TEST DEPTH REGRESSION [%s]: %s fell from %d to %d/%d confirmations "
                    "(possible re-org); reverting to TEST_PENDING.",
                    key, test_tx.tx_hash, previous, depth, asset_cfg.min_confirmations)
                test_tx.confirmed_at = None
            test_tx.status = VerificationStatus.TEST_PENDING
        return test_tx

    def acknowledge_test_receipt(
        self,
        request_id: str,
        attested_by: str,
        channel: str,
        now: Optional[datetime.datetime] = None,
    ) -> TestTransaction:
        """Record that the counterparty confirmed, out of band, that the dust arrived.

        This is the step that makes a test transfer a control rather than a
        formality. On-chain confirmation proves the network accepted a transfer to
        whatever address was in it; only the recipient can attest that the address
        was *theirs*. CCSS v9 `1.05.8.1` requires this verification to happen "via
        Approved Communication Channels prior to the use of key material", which is
        why ``channel`` is mandatory and is recorded in the audit trail.

        ``attested_by`` and ``channel`` are recorded as supplied. This module cannot
        authenticate either — an attestation is only as trustworthy as the channel
        it arrived on, and Fireblocks documents deposit-address spoofing and
        man-in-the-middle attacks against exactly these channels.
        """
        req = self._require_pending(request_id)
        key = req.request_id
        if key not in self.test_transactions:
            raise VerificationError(
                f"No test transaction recorded for request ID {key}; nothing to acknowledge.")

        if not isinstance(attested_by, str) or not attested_by.strip():
            raise VerificationError(
                f"attested_by must be a non-empty string, got {attested_by!r}")
        if not isinstance(channel, str) or not channel.strip():
            raise VerificationError(
                f"channel must be a non-empty string, got {channel!r}")

        test_tx = self.test_transactions[key]
        test_tx.receipt_acknowledged_by = attested_by.strip()
        test_tx.receipt_channel = channel.strip()
        test_tx.receipt_acknowledged_at = self._resolve_now(now)
        if test_tx.confirmed_at is not None:
            test_tx.status = VerificationStatus.RECEIPT_ACKNOWLEDGED
        logger.info("RECEIPT ACKNOWLEDGED [%s]: %s attested arrival of %s via %s.",
                    key, test_tx.receipt_acknowledged_by, test_tx.tx_hash,
                    test_tx.receipt_channel)
        return test_tx

    # ---------------------------------------------------------- authorisation

    @staticmethod
    def _resolve_now(now: Optional[datetime.datetime]) -> datetime.datetime:
        """Return a timezone-aware UTC instant, defaulting to the system clock."""
        if now is None:
            return datetime.datetime.now(datetime.timezone.utc)
        if not isinstance(now, datetime.datetime):
            raise VerificationError(f"now must be a datetime, got {type(now).__name__}")
        if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
            raise VerificationError(
                "now must be timezone-aware; a naive datetime silently assumes local "
                "time and makes the expiry window wrong by the UTC offset.")
        return now.astimezone(datetime.timezone.utc)

    def verify_and_authorize_large_transfer(
        self,
        request_id: str,
        now: Optional[datetime.datetime] = None,
    ) -> VerificationResult:
        """Final authorisation gate for the primary transfer.

        Re-validates every control at the moment of authorisation rather than
        trusting the checks made at initiation, because whitelists get revoked and
        chains re-org in between. Checks, in order: the request is still open and
        unspent, a test transaction exists, that test is still bound to this
        request's recipient and chain, the recipient is still whitelisted, the
        required depth still holds, a counterparty receipt exists (unless
        disabled), and the expiry window has not elapsed.

        Authorisation is **single-use**. A second call for the same request raises,
        so one dust test cannot authorise a series of primary transfers within the
        window.
        """
        req = self._require_pending(request_id)
        key = req.request_id
        audit = [f"Evaluating final authorization for request {key}"]

        if key not in self.test_transactions:
            raise TestTransactionPendingError(
                f"Test transaction has not been initiated for request {key}.")

        test_tx = self.test_transactions[key]
        asset_cfg = self.asset_configs[req.asset_symbol]
        moment = self._resolve_now(now)

        # 1. The test must still be bound to this request. A request's recipient is
        #    canonicalised at construction, but re-checking costs nothing and
        #    catches a caller that mutated the dataclass in place.
        if test_tx.observed_recipient != req.recipient_address:
            msg = (f"Test transaction {test_tx.tx_hash} is bound to "
                   f"{test_tx.observed_recipient}, not to this request's recipient "
                   f"{req.recipient_address}.")
            logger.error("MISMATCH [%s]: %s", key, msg)
            raise TestTransactionMismatchError(msg)
        if test_tx.observed_chain != asset_cfg.chain:
            msg = (f"Test transaction {test_tx.tx_hash} is bound to chain "
                   f"{test_tx.observed_chain}, not to {asset_cfg.chain}.")
            logger.error("MISMATCH [%s]: %s", key, msg)
            raise TestTransactionMismatchError(msg)
        audit.append(f"Test transaction {test_tx.tx_hash} bound to "
                     f"{test_tx.observed_recipient} on {test_tx.observed_chain}.")

        # 2. Whitelist revocation between initiation and authorisation.
        if self.config.enforce_whitelisting and not self.is_whitelisted(req.recipient_address):
            msg = (f"Recipient address {req.recipient_address} was revoked from the "
                   f"whitelist after this request was initiated.")
            audit.append(f"REJECTED: {msg}")
            logger.error("REJECTED [%s]: %s", key, msg)
            raise WhitelistError(msg)

        # 3. Confirmation depth must still hold right now.
        if test_tx.confirmed_at is None or test_tx.confirmations < asset_cfg.min_confirmations:
            msg = (f"Test transaction {test_tx.tx_hash} pending confirmation "
                   f"({test_tx.confirmations}/{asset_cfg.min_confirmations} blocks).")
            audit.append(msg)
            raise TestTransactionPendingError(msg)

        # 4. Out-of-band counterparty receipt (CCSS v9 1.05.8.1).
        if self.config.require_counterparty_receipt and test_tx.receipt_acknowledged_at is None:
            msg = (f"Test transaction {test_tx.tx_hash} reached "
                   f"{test_tx.confirmations}/{asset_cfg.min_confirmations} confirmations "
                   f"but no counterparty receipt has been recorded. Confirmation depth "
                   f"alone does not verify that the destination address is the intended "
                   f"one.")
            audit.append(msg)
            logger.error("RECEIPT MISSING [%s]: %s", key, msg)
            raise TestTransactionPendingError(msg)

        # 5. Time-decay window, measured from the first confirmation.
        elapsed_seconds = (moment - test_tx.confirmed_at).total_seconds()
        if elapsed_seconds < -self.config.max_clock_skew_seconds:
            msg = (f"Test transaction {test_tx.tx_hash} reports confirmation "
                   f"{-elapsed_seconds:.1f}s in the future relative to the evaluation "
                   f"clock, beyond the {self.config.max_clock_skew_seconds:.0f}s skew "
                   f"tolerance. Refusing to evaluate an expiry window against an "
                   f"untrustworthy clock.")
            audit.append(msg)
            logger.error("CLOCK FAULT [%s]: %s", key, msg)
            raise VerificationError(msg)

        elapsed_minutes = max(elapsed_seconds, 0.0) / 60.0
        if elapsed_minutes > self.config.test_expiry_window_minutes:
            test_tx.status = VerificationStatus.EXPIRED
            msg = (f"Test transaction confirmation expired ({elapsed_minutes:.1f} mins ago, "
                   f"max allowed: {self.config.test_expiry_window_minutes} mins). "
                   f"New test transaction required.")
            audit.append(msg)
            logger.error("EXPIRED [%s]: %s", key, msg)
            raise TestTransactionExpiredError(msg)

        receipt_note = (
            f", receipt attested by {test_tx.receipt_acknowledged_by} via "
            f"{test_tx.receipt_channel}" if test_tx.receipt_acknowledged_at else "")
        audit.append(
            f"Test transaction {test_tx.tx_hash} verified ({test_tx.confirmations} "
            f"confirmations, confirmed {elapsed_minutes:.1f} mins ago{receipt_note}).")
        audit.append(f"AUTHORIZATION GRANTED: execute primary transfer of {req.amount} "
                     f"{req.asset_symbol} (${req.value_usd:,.2f} USD) to "
                     f"{req.recipient_address}")

        # Single-use: consume before returning, so a caller that retries after a
        # network error cannot obtain a second authorisation from one dust test.
        test_tx.status = VerificationStatus.APPROVED
        self.consumed_requests.add(key)
        del self.pending_requests[key]

        logger.info("APPROVED [%s]: %s", key, audit[-1])
        return VerificationResult(
            request_id=key,
            is_approved=True,
            status=VerificationStatus.APPROVED,
            risk_level=RiskLevel.LOW,
            message="Primary transfer authorized following successful test transaction "
                    "verification.",
            test_tx_hash=test_tx.tx_hash,
            audit_trail=audit,
        )
