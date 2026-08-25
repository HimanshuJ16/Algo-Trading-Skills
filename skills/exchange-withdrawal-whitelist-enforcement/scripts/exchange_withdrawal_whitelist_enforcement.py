"""exchange-withdrawal-whitelist-enforcement: a client-side pre-flight gate that
decides whether an automated withdrawal request is allowed to be submitted to an
exchange at all.

What this module is and is not
------------------------------
It is a **defence-in-depth gate that sits in front of your own withdrawal code**.
The exchange remains the authoritative enforcer: if Binance, Coinbase, Kraken or
OKX rejects a withdrawal, that rejection stands regardless of what this engine
said. The value of a local gate is that it fails *before* a signed request leaves
your infrastructure, it produces an auditable record of why, and it can enforce a
firm policy that is stricter than the venue's.

It is **not** a substitute for enabling the venue's own whitelist. A local
allowlist alone protects nothing: an attacker holding a withdrawal-capable API
key simply calls the venue directly and never runs this code.

Cool-off periods are venue policy, not a standard
-------------------------------------------------
There is no universal "mandatory 24-hour lock". As of 2026-08:

* **Binance** — the address whitelist is **opt-in**, and the separate "Whitelist
  Withdrawal Limit" suspends withdrawals to *newly added* addresses for a
  user-selected **24, 48 or 72 hours**.
* **Coinbase Exchange** — a newly added address book entry is held **48 hours**
  before it can be withdrawn to, and may be deleted during the hold.
* **OKX** — "New address withdrawal lock" is an opt-in advanced setting giving a
  **24-hour** lock on newly allowlisted addresses.
* **Kraken** — the hold is triggered differently: a password change without
  Sign-in 2FA or a Master Key holds withdrawals to new addresses (12h on Kraken
  Pro, 24h on Kraken Classic). A Global Settings Lock blocks *adding* an address
  entirely, with an operator-chosen unlock delay.

So the cool-off is per-record and configurable here, and the engine additionally
applies a firm floor (``minimum_cooloff_seconds``) that a per-record value can
lengthen but never shorten. See ``references/standards.md``.

Trusted clock
-------------
``audit_withdrawal_request`` evaluates the cool-off against an
``evaluation_timestamp_seconds`` supplied by the caller (defaulting to
``time.time()``), **never** against ``WithdrawalRequest.request_timestamp_seconds``.
The request timestamp travels with a potentially attacker-controlled request; a
lock that can be unlocked by putting a future number in the request is not a
lock. The request timestamp is recorded for the audit trail and skew-checked
only. Pass ``evaluation_timestamp_seconds`` explicitly for reproducible output.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_APPROVED",
    "STATUS_KEY_WITHDRAWAL_DISABLED",
    "STATUS_KEY_NOT_IP_RESTRICTED",
    "STATUS_UNAUTHORIZED_ADDRESS",
    "STATUS_ADDRESS_REVOKED",
    "STATUS_DESTINATION_TAG_MISMATCH",
    "STATUS_COOLOFF_ACTIVE",
    "SECONDS_PER_HOUR",
    "COOLOFF_24H_SECONDS",
    "COOLOFF_48H_SECONDS",
    "COOLOFF_72H_SECONDS",
    "WithdrawalWhitelistError",
    "NetworkWithdrawalPolicy",
    "WhitelistedAddressRecord",
    "WithdrawalRequest",
    "WithdrawalWhitelistAuditReport",
    "ExchangeWithdrawalWhitelistEngine",
    "canonicalize_address",
]

STATUS_APPROVED = "WITHDRAWAL_APPROVED"
STATUS_KEY_WITHDRAWAL_DISABLED = "API_KEY_WITHDRAWAL_DISABLED"
STATUS_KEY_NOT_IP_RESTRICTED = "API_KEY_NOT_IP_RESTRICTED_REJECTION"
STATUS_UNAUTHORIZED_ADDRESS = "UNAUTHORIZED_ADDRESS_REJECTION"
STATUS_ADDRESS_REVOKED = "ADDRESS_REVOKED_REJECTION"
STATUS_DESTINATION_TAG_MISMATCH = "DESTINATION_TAG_MISMATCH_REJECTION"
STATUS_COOLOFF_ACTIVE = "COOLOFF_PERIOD_ACTIVE_REJECTION"

SECONDS_PER_HOUR = 3_600.0
#: The three cool-off durations Binance offers for its Whitelist Withdrawal Limit.
COOLOFF_24H_SECONDS = 86_400.0
COOLOFF_48H_SECONDS = 172_800.0
COOLOFF_72H_SECONDS = 259_200.0

#: An EVM address: `0x` plus 40 hex digits. Matches the `addressRegex` Binance
#: publishes for EVM networks via GET /sapi/v1/capital/config/getall.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
#: Bech32/bech32m shape (BIP-173/BIP-350): a letters-only human-readable part, the
#: `1` separator, then the data part over the bech32 charset (no `1`, `b`, `i`, `o`).
_BECH32_SHAPE_RE = re.compile(r"^[a-z]{1,83}1[ac-hj-np-z02-9]{6,}$")


class WithdrawalWhitelistError(ValueError):
    """Raised when a record, request, or engine configuration is structurally invalid.

    A withdrawal gate must fail loudly rather than return a report. An exception
    cannot be misread as approval; a report can, if the caller checks the wrong
    field. A NaN amount, an empty destination address, or a negative cool-off is a
    programming or data-integration defect, and auditing it anyway would emit an
    authoritative-looking ``WITHDRAWAL_APPROVED`` built on garbage.
    """


def canonicalize_address(address: str) -> str:
    """Return the allowlist lookup key for ``address``.

    Case is folded **only** where the address encoding is genuinely
    case-insensitive, because folding case on a case-sensitive encoding maps
    distinct address strings onto a single allowlist key:

    * **EVM hex** (``0x`` + 40 hex digits) — case-insensitive. ERC-55 uses
      capitalisation purely as a checksum, so ``0xAbC…`` and ``0xabc…`` denote the
      same address. Folded to lowercase, which makes a checksummed request match a
      lowercase allowlist entry (and vice versa) instead of falsely rejecting.
    * **All-uppercase bech32/bech32m** — BIP-173 forbids mixed case and treats the
      all-uppercase form (used for QR codes) as the same address as the canonical
      all-lowercase form. Folded to lowercase.
    * **Everything else** — Base58Check (Bitcoin legacy, Tron, XRP) and Solana
      base58 are case-**sensitive**. Returned byte-exact, never folded.

    A mixed-case string is never treated as bech32, so Base58Check addresses (which
    are almost always mixed case) always take the byte-exact path.
    """
    if not isinstance(address, str):
        raise WithdrawalWhitelistError(
            f"destination address must be a string, got {type(address).__name__}")
    stripped = address.strip()
    if not stripped:
        raise WithdrawalWhitelistError("destination address must be a non-empty string")
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
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WithdrawalWhitelistError(f"{name} must be a real number, got {value!r}")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise WithdrawalWhitelistError(f"{name} must be finite, got {numeric!r}")
    if exclusive_minimum is not None and numeric <= exclusive_minimum:
        raise WithdrawalWhitelistError(
            f"{name} must be > {exclusive_minimum}, got {numeric!r}")
    if minimum is not None and numeric < minimum:
        raise WithdrawalWhitelistError(f"{name} must be >= {minimum}, got {numeric!r}")
    return numeric


def _require_token(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WithdrawalWhitelistError(f"{name} must be a non-empty string, got {value!r}")
    return value.strip().upper()


def _normalize_tag(tag: Optional[str]) -> Optional[str]:
    """Normalise a destination tag/memo.

    Whitespace-only is treated as absent, since an empty ``addressTag`` and a
    missing one are the same thing to a venue. Case is preserved: memos are opaque
    identifiers assigned by the receiving venue, and XLM/EOS memos are
    case-sensitive.
    """
    if tag is None:
        return None
    if not isinstance(tag, str):
        raise WithdrawalWhitelistError(
            f"destination tag must be a string or None, got {type(tag).__name__}")
    stripped = tag.strip()
    return stripped or None


@dataclass(frozen=True)
class NetworkWithdrawalPolicy:
    """Per-(asset, network) withdrawal constraints published by the venue.

    The field names mirror the per-network entries Binance returns from
    ``GET /sapi/v1/capital/config/getall``, which is the intended source: fetch the
    venue's own ``addressRegex``, ``memoRegex`` and ``withdrawTag`` rather than
    hard-coding chain rules that change when a venue adds a network.

    Registering a policy is optional. Without one the engine cannot check address
    shape or whether a memo is mandatory, and every report it produces says so in
    ``warnings`` instead of implying the checks passed.
    """

    asset_symbol: str
    network: str
    #: Mirrors `withdrawTag`: the network requires a memo/destination tag.
    requires_destination_tag: bool = False
    #: Mirrors `addressRegex`.
    address_regex: Optional[str] = None
    #: Mirrors `memoRegex`.
    memo_regex: Optional[str] = None


@dataclass
class WhitelistedAddressRecord:
    """One approved withdrawal destination.

    ``network`` is required and part of the identity of the record. The same
    address string is valid on Ethereum, BSC, Polygon and Arbitrum, and Binance
    scopes whitelist entries per coin *and* per network for exactly that reason.
    Binance's withdraw endpoint also makes ``network`` optional and falls back to
    the coin's default network, so a gate that ignores the chain can approve a
    transfer that is then routed over a chain the destination cannot receive on.

    ``destination_tag`` binds the memo to the address. On XRP, XLM, EOS, ATOM and
    similar chains the address identifies a shared venue wallet and the memo
    identifies the account inside it; a whitelist that binds only the address
    permits a request that swaps or drops the memo, which credits the funds to
    somebody else at the correct destination.
    """

    address_id: str
    asset_symbol: str                                # e.g. 'BTC', 'ETH', 'USDT'
    network: str                                     # e.g. 'BTC', 'ETH', 'BSC', 'TRX'
    destination_address: str
    label: str
    added_timestamp_seconds: float
    cooloff_duration_seconds: float = COOLOFF_24H_SECONDS
    destination_tag: Optional[str] = None


@dataclass
class WithdrawalRequest:
    """A proposed withdrawal awaiting the pre-flight gate.

    ``is_withdrawal_enabled_on_key`` defaults to ``False``. A security control must
    deny by default: a request constructed without stating the key's scope is a
    request whose scope is unknown, and the previous default of ``True`` meant any
    caller that forgot the field was silently treated as authorised.

    ``request_timestamp_seconds`` is **untrusted metadata**. It is recorded in the
    report and checked for clock skew, but never used to decide the cool-off.
    """

    request_id: str
    asset_symbol: str
    network: str
    amount: float
    destination_address: str
    request_timestamp_seconds: float
    destination_tag: Optional[str] = None
    is_withdrawal_enabled_on_key: bool = False
    #: Binance will not let a key hold withdrawal permission at all unless the key
    #: carries an IP access restriction. A withdrawal-capable key reachable from
    #: anywhere is the drain vector this skill exists to close.
    is_key_ip_restricted: bool = False


@dataclass
class WithdrawalWhitelistAuditReport:
    """The auditable outcome of one pre-flight evaluation.

    ``is_address_whitelisted``, ``is_cooloff_elapsed``, ``remaining_cooloff_seconds``
    and ``unlock_timestamp_seconds`` are ``None`` when the corresponding check did
    not run — for example, an API-key rejection short-circuits before the allowlist
    is consulted. Reporting ``False`` for a check that was never performed would put
    a claim in the audit trail that the engine never established.

    ``checks_evaluated`` names the checks that actually executed, in order.
    """

    request_id: str
    asset_symbol: str
    network: str
    amount: float
    destination_address: str
    is_withdrawal_approved: bool
    status: str
    audit_notes: str
    evaluation_timestamp_seconds: float
    request_timestamp_seconds: float
    is_address_whitelisted: Optional[bool] = None
    is_cooloff_elapsed: Optional[bool] = None
    remaining_cooloff_seconds: Optional[float] = None
    unlock_timestamp_seconds: Optional[float] = None
    matched_address_id: Optional[str] = None
    checks_evaluated: Tuple[str, ...] = ()
    warnings: List[str] = field(default_factory=list)


@dataclass
class _AllowlistEntry:
    """Internal state per (asset, network, canonical address).

    ``activation_anchor_seconds`` is the start of the cool-off and is maintained
    monotonically: re-registering an address can only ever push the unlock later,
    never earlier. Without that, an attacker (or a buggy allowlist sync) could
    re-register an address currently inside its lock with an older
    ``added_timestamp_seconds`` and clear the lock in one call.
    """

    record: WhitelistedAddressRecord
    activation_anchor_seconds: float
    effective_cooloff_seconds: float
    is_active: bool = True
    revoked_at_seconds: Optional[float] = None

    @property
    def unlock_timestamp_seconds(self) -> float:
        return self.activation_anchor_seconds + self.effective_cooloff_seconds


class ExchangeWithdrawalWhitelistEngine:
    """Pre-flight gate for automated exchange withdrawals.

    Evaluates, in order: API key withdrawal scope, API key IP restriction,
    allowlist membership scoped to (asset, network, address), revocation,
    destination tag binding, and the cool-off lock. The first failing check
    short-circuits, so the returned status names the *first* reason the request was
    stopped rather than a merged verdict.

    Thread safety
    -------------
    This class is **not** thread-safe, and an internal lock would not be enough to
    make it so. ``register_whitelisted_address`` read-modify-writes the allowlist,
    and — more importantly — an approval is a decision about a point in time: an
    operator can revoke an address in the window between ``audit_withdrawal_request``
    returning ``WITHDRAWAL_APPROVED`` and the withdrawal actually being submitted.
    The caller must serialise allowlist mutation against the full
    audit-then-submit sequence, not merely against the audit call.

    Args:
        default_cooloff_seconds: Cool-off applied to records that do not set one.
        minimum_cooloff_seconds: Firm floor. A record may lengthen its cool-off but
            never shorten it below this, which closes the bypass where a record
            carrying ``cooloff_duration_seconds=0`` silently disables the control.
        require_ip_restricted_key: Reject withdrawal-capable API keys that carry no
            IP access restriction.
        max_request_clock_skew_seconds: How far ``request_timestamp_seconds`` may
            diverge from the trusted evaluation clock before the report carries a
            warning. Skew never changes the decision.
    """

    def __init__(self, default_cooloff_seconds: float = COOLOFF_24H_SECONDS,
                 *, minimum_cooloff_seconds: float = COOLOFF_24H_SECONDS,
                 require_ip_restricted_key: bool = True,
                 max_request_clock_skew_seconds: float = 300.0) -> None:
        self.default_cooloff_seconds = _require_finite(
            default_cooloff_seconds, "default_cooloff_seconds", minimum=0.0)
        self.minimum_cooloff_seconds = _require_finite(
            minimum_cooloff_seconds, "minimum_cooloff_seconds", minimum=0.0)
        self.max_request_clock_skew_seconds = _require_finite(
            max_request_clock_skew_seconds, "max_request_clock_skew_seconds", minimum=0.0)
        self.require_ip_restricted_key = bool(require_ip_restricted_key)
        self._allowlist: Dict[Tuple[str, str, str], _AllowlistEntry] = {}
        self._policies: Dict[Tuple[str, str], NetworkWithdrawalPolicy] = {}

    # ------------------------------------------------------------------ policies

    def register_network_policy(self, policy: NetworkWithdrawalPolicy) -> None:
        """Register the venue's per-network address/memo constraints."""
        if not isinstance(policy, NetworkWithdrawalPolicy):
            raise WithdrawalWhitelistError(
                f"policy must be a NetworkWithdrawalPolicy, got {type(policy).__name__}")
        asset = _require_token(policy.asset_symbol, "policy.asset_symbol")
        network = _require_token(policy.network, "policy.network")
        for pattern, name in ((policy.address_regex, "policy.address_regex"),
                              (policy.memo_regex, "policy.memo_regex")):
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise WithdrawalWhitelistError(
                        f"{name} is not a valid regular expression: {exc}") from exc
        self._policies[(asset, network)] = policy
        logger.info("NETWORK POLICY REGISTERED [%s:%s]: requires_tag=%s",
                    asset, network, policy.requires_destination_tag)

    # ----------------------------------------------------------------- allowlist

    def register_whitelisted_address(self, record: WhitelistedAddressRecord,
                                     *, observed_at_seconds: Optional[float] = None,
                                     ) -> WhitelistedAddressRecord:
        """Add or refresh an allowlist entry and return the stored record.

        The cool-off anchor is ``max`` of the record's ``added_timestamp_seconds``,
        any existing anchor for the same key, and the time of any prior revocation.
        This makes the unlock time monotonically non-decreasing, so:

        * re-registering an in-cool-off address with an older timestamp cannot
          clear the lock (the attempt is logged as a tamper signal);
        * an address that was revoked and later re-added serves a full fresh
          cool-off, matching how venues treat a deleted-and-re-added entry.

        Args:
            record: The entry to store.
            observed_at_seconds: Trusted time at which this registration was
                observed. Defaults to ``time.time()``; pass it explicitly for
                deterministic, reproducible audits.
        """
        if not isinstance(record, WhitelistedAddressRecord):
            raise WithdrawalWhitelistError(
                f"record must be a WhitelistedAddressRecord, got {type(record).__name__}")

        asset = _require_token(record.asset_symbol, "record.asset_symbol")
        network = _require_token(record.network, "record.network")
        if not isinstance(record.address_id, str) or not record.address_id.strip():
            raise WithdrawalWhitelistError("record.address_id must be a non-empty string")
        canonical = canonicalize_address(record.destination_address)
        added_at = _require_finite(
            record.added_timestamp_seconds, "record.added_timestamp_seconds")
        record_cooloff = _require_finite(
            record.cooloff_duration_seconds, "record.cooloff_duration_seconds", minimum=0.0)
        tag = _normalize_tag(record.destination_tag)
        observed_at = self._resolve_clock(observed_at_seconds, "observed_at_seconds")

        self._enforce_policy_shape(asset, network, canonical, tag, context="record")

        key = (asset, network, canonical)
        previous = self._allowlist.get(key)

        requested_cooloff = max(record_cooloff, self.minimum_cooloff_seconds)
        effective_cooloff = requested_cooloff
        anchor = added_at
        if previous is not None:
            effective_cooloff = max(effective_cooloff, previous.effective_cooloff_seconds)
            anchor = max(anchor, previous.activation_anchor_seconds)
            if previous.revoked_at_seconds is not None:
                anchor = max(anchor, previous.revoked_at_seconds)
            if anchor > added_at or effective_cooloff > requested_cooloff:
                logger.warning(
                    "COOLOFF REGRESSION SUPPRESSED [%s:%s:%s]: submitted anchor %.0f / "
                    "cool-off %.0fs would have shortened the lock; retained anchor "
                    "%.0f / cool-off %.0fs.",
                    asset, network, canonical, added_at, record_cooloff,
                    anchor, effective_cooloff)

        if added_at > observed_at + self.max_request_clock_skew_seconds:
            logger.warning(
                "FUTURE ADD TIMESTAMP [%s:%s:%s]: added_timestamp_seconds %.0f is ahead "
                "of the trusted clock %.0f; the lock will run longer than intended.",
                asset, network, canonical, added_at, observed_at)

        stored = WhitelistedAddressRecord(
            address_id=record.address_id.strip(), asset_symbol=asset, network=network,
            destination_address=record.destination_address.strip(), label=record.label,
            added_timestamp_seconds=added_at, cooloff_duration_seconds=record_cooloff,
            destination_tag=tag,
        )
        entry = _AllowlistEntry(
            record=stored, activation_anchor_seconds=anchor,
            effective_cooloff_seconds=effective_cooloff, is_active=True,
            revoked_at_seconds=None,
        )
        self._allowlist[key] = entry
        logger.info(
            "WHITELISTED ADDRESS REGISTERED [%s:%s:%s]: id=%s unlocks at %.0f "
            "(anchor %.0f + %.0fs).",
            asset, network, canonical, stored.address_id,
            entry.unlock_timestamp_seconds, anchor, effective_cooloff)
        return stored

    def revoke_whitelisted_address(self, asset_symbol: str, network: str,
                                   destination_address: str,
                                   *, observed_at_seconds: Optional[float] = None,
                                   ) -> bool:
        """Deactivate an allowlist entry, keeping it for the audit trail.

        Returns ``True`` if an active entry was deactivated, ``False`` if there was
        nothing active to revoke. The entry is retained rather than deleted so a
        later withdrawal attempt to a revoked address reports
        ``ADDRESS_REVOKED_REJECTION`` — a much stronger operational signal than the
        generic "unknown address" a deletion would produce.
        """
        key = (_require_token(asset_symbol, "asset_symbol"),
               _require_token(network, "network"),
               canonicalize_address(destination_address))
        entry = self._allowlist.get(key)
        if entry is None or not entry.is_active:
            logger.warning("REVOKE NO-OP [%s:%s:%s]: no active allowlist entry.", *key)
            return False
        entry.is_active = False
        entry.revoked_at_seconds = self._resolve_clock(
            observed_at_seconds, "observed_at_seconds")
        logger.info("WHITELISTED ADDRESS REVOKED [%s:%s:%s]: id=%s at %.0f.",
                    *key, entry.record.address_id, entry.revoked_at_seconds)
        return True

    # --------------------------------------------------------------------- audit

    def audit_withdrawal_request(self, req: WithdrawalRequest,
                                 *, evaluation_timestamp_seconds: Optional[float] = None,
                                 ) -> WithdrawalWhitelistAuditReport:
        """Evaluate one withdrawal request against the full gate.

        Args:
            req: The proposed withdrawal.
            evaluation_timestamp_seconds: Trusted clock for the cool-off decision.
                Defaults to ``time.time()``. ``req.request_timestamp_seconds`` is
                deliberately not used here — see the module docstring.

        Raises:
            WithdrawalWhitelistError: If the request is structurally invalid
                (non-positive or non-finite amount, empty address, missing asset or
                network). Malformed input is never scored as a decision.
        """
        if not isinstance(req, WithdrawalRequest):
            raise WithdrawalWhitelistError(
                f"req must be a WithdrawalRequest, got {type(req).__name__}")
        if not isinstance(req.request_id, str) or not req.request_id.strip():
            raise WithdrawalWhitelistError("req.request_id must be a non-empty string")

        request_id = req.request_id.strip()
        asset = _require_token(req.asset_symbol, "req.asset_symbol")
        network = _require_token(req.network, "req.network")
        canonical = canonicalize_address(req.destination_address)
        amount = _require_finite(req.amount, "req.amount", exclusive_minimum=0.0)
        request_ts = _require_finite(
            req.request_timestamp_seconds, "req.request_timestamp_seconds")
        tag = _normalize_tag(req.destination_tag)
        now = self._resolve_clock(evaluation_timestamp_seconds, "evaluation_timestamp_seconds")

        warnings: List[str] = []
        skew = abs(request_ts - now)
        if skew > self.max_request_clock_skew_seconds:
            warning = (f"Request timestamp {request_ts:.0f} differs from the trusted "
                       f"evaluation clock {now:.0f} by {skew:.0f}s. The cool-off is "
                       f"evaluated against the trusted clock regardless.")
            warnings.append(warning)
            logger.warning("CLOCK SKEW [%s]: %s", request_id, warning)

        policy = self._policies.get((asset, network))
        if policy is None:
            warnings.append(
                f"No NetworkWithdrawalPolicy registered for {asset}:{network}; address "
                f"format and mandatory-memo checks were not performed.")

        checks: List[str] = []

        def build(status: str, notes: str, *, approved: bool = False,
                  whitelisted: Optional[bool] = None,
                  cooloff_elapsed: Optional[bool] = None,
                  remaining: Optional[float] = None,
                  unlock_at: Optional[float] = None,
                  address_id: Optional[str] = None,
                  ) -> WithdrawalWhitelistAuditReport:
            return WithdrawalWhitelistAuditReport(
                request_id=request_id, asset_symbol=asset, network=network, amount=amount,
                destination_address=req.destination_address.strip(),
                is_withdrawal_approved=approved, status=status, audit_notes=notes,
                evaluation_timestamp_seconds=now, request_timestamp_seconds=request_ts,
                is_address_whitelisted=whitelisted, is_cooloff_elapsed=cooloff_elapsed,
                remaining_cooloff_seconds=remaining, unlock_timestamp_seconds=unlock_at,
                matched_address_id=address_id, checks_evaluated=tuple(checks),
                warnings=warnings,
            )

        # Check 1: API key withdrawal scope. Short-circuits before the allowlist is
        # consulted, so membership is reported as None ("not evaluated"), not False.
        checks.append("api_key_withdrawal_scope")
        if not req.is_withdrawal_enabled_on_key:
            notes = (f"API KEY REJECTION [{request_id}]: the API key is not marked as "
                     f"withdrawal-enabled. Allowlist membership was not evaluated.")
            logger.critical("%s", notes)
            return build(STATUS_KEY_WITHDRAWAL_DISABLED, notes)

        # Check 2: IP access restriction on a withdrawal-capable key.
        if self.require_ip_restricted_key:
            checks.append("api_key_ip_restriction")
            if not req.is_key_ip_restricted:
                notes = (
                    f"API KEY IP RESTRICTION REJECTION [{request_id}]: the key carries "
                    f"withdrawal permission with no IP access restriction. A stolen key "
                    f"is then usable from anywhere. Binance will not enable withdrawal "
                    f"permission on an unrestricted key at all.")
                logger.critical("%s", notes)
                return build(STATUS_KEY_NOT_IP_RESTRICTED, notes)

        # Check 3: allowlist membership, scoped to (asset, network, address).
        checks.append("allowlist_membership")
        entry = self._allowlist.get((asset, network, canonical))
        if entry is None:
            notes = (
                f"UNAUTHORIZED ADDRESS REJECTION [{request_id}]: destination "
                f"'{req.destination_address.strip()}' is not on the allowlist for "
                f"{asset} on network {network}. An entry for the same address on a "
                f"different network does not authorise this request.")
            logger.critical("%s", notes)
            return build(STATUS_UNAUTHORIZED_ADDRESS, notes, whitelisted=False)

        # Check 4: revocation.
        checks.append("allowlist_revocation")
        if not entry.is_active:
            notes = (f"ADDRESS REVOKED REJECTION [{request_id}]: allowlist entry "
                     f"'{entry.record.address_id}' was revoked at "
                     f"{entry.revoked_at_seconds:.0f} and must not receive withdrawals.")
            logger.critical("%s", notes)
            return build(STATUS_ADDRESS_REVOKED, notes, whitelisted=False,
                         address_id=entry.record.address_id)

        # Check 5: destination tag / memo binding.
        checks.append("destination_tag_binding")
        tag_error = self._check_destination_tag(entry.record, tag, policy)
        if tag_error is not None:
            notes = f"DESTINATION TAG MISMATCH REJECTION [{request_id}]: {tag_error}"
            logger.critical("%s", notes)
            return build(STATUS_DESTINATION_TAG_MISMATCH, notes, whitelisted=True,
                         address_id=entry.record.address_id)

        # Check 6: cool-off lock, against the trusted clock only.
        checks.append("cooloff_lock")
        unlock_at = entry.unlock_timestamp_seconds
        remaining = max(0.0, unlock_at - now)
        if now < unlock_at:
            notes = (
                f"COOLOFF LOCK ACTIVE REJECTION [{request_id}]: allowlist entry "
                f"'{entry.record.address_id}' unlocks at {unlock_at:.0f}; "
                f"{remaining / SECONDS_PER_HOUR:.2f} hours ({remaining:.0f}s) remain.")
            logger.warning("%s", notes)
            return build(STATUS_COOLOFF_ACTIVE, notes, whitelisted=True,
                         cooloff_elapsed=False, remaining=remaining, unlock_at=unlock_at,
                         address_id=entry.record.address_id)

        notes = (f"WITHDRAWAL APPROVED [{request_id}]: {amount} {asset} to allowlisted "
                 f"address '{entry.record.destination_address}' "
                 f"({entry.record.label}) on network {network}.")
        logger.info("%s", notes)
        return build(STATUS_APPROVED, notes, approved=True, whitelisted=True,
                     cooloff_elapsed=True, remaining=0.0, unlock_at=unlock_at,
                     address_id=entry.record.address_id)

    # ------------------------------------------------------------------ internals

    def _resolve_clock(self, supplied: Optional[float], name: str) -> float:
        if supplied is None:
            return float(time.time())
        return _require_finite(supplied, name)

    def _enforce_policy_shape(self, asset: str, network: str, canonical: str,
                              tag: Optional[str], *, context: str) -> None:
        """Validate address/memo shape against the venue policy, when one is known."""
        policy = self._policies.get((asset, network))
        if policy is None:
            return
        if policy.address_regex and not re.match(policy.address_regex, canonical):
            raise WithdrawalWhitelistError(
                f"{context}: address '{canonical}' does not match the {asset}:{network} "
                f"addressRegex {policy.address_regex!r}")
        if tag is not None and policy.memo_regex and not re.match(policy.memo_regex, tag):
            raise WithdrawalWhitelistError(
                f"{context}: destination tag '{tag}' does not match the {asset}:{network} "
                f"memoRegex {policy.memo_regex!r}")
        if policy.requires_destination_tag and tag is None:
            raise WithdrawalWhitelistError(
                f"{context}: {asset}:{network} requires a destination tag/memo, but none "
                f"was supplied. Sending without it credits a shared venue wallet with no "
                f"way to attribute the deposit.")

    @staticmethod
    def _check_destination_tag(record: WhitelistedAddressRecord, tag: Optional[str],
                               policy: Optional[NetworkWithdrawalPolicy],
                               ) -> Optional[str]:
        """Return a rejection reason if the request's memo does not match the entry."""
        if policy is not None and policy.requires_destination_tag and tag is None:
            return (f"{record.asset_symbol}:{record.network} requires a destination "
                    f"tag/memo and the request supplied none.")
        if record.destination_tag != tag:
            return (f"allowlist entry '{record.address_id}' is bound to destination tag "
                    f"{record.destination_tag!r}, but the request supplied {tag!r}. On "
                    f"memo-based chains the address identifies a shared venue wallet and "
                    f"the memo identifies the account inside it, so a mismatched memo "
                    f"credits the wrong account.")
        return None
