"""
key-rotation-schedule-for-hot-wallet-keys: a policy evaluator for the lifecycle of an
online signing key -- a blockchain hot wallet key, or an exchange/broker API key -- used
by an algorithmic trading bot.

What this module is and is not
------------------------------
It is a **stateless, point-in-time policy evaluator over key metadata**. Given the age,
usage, compromise status and residual balance of a key, it decides whether that key must
be rotated, what lifecycle state it should now be in, and whether it is yet safe to
destroy the key material.

It does **not** generate keys, hold key material, sign anything, call a KMS, sweep funds,
revoke an API credential, or zeroize memory. ``replacement_key_id`` is a *proposed label*
for the key an operator or KMS must create; nothing in this module creates it. Treat every
report as an instruction to a rotation runbook, never as evidence that rotation happened.

The thresholds here are engineering defaults, not standards
-----------------------------------------------------------
No standard mandates a 90-day hot wallet key rotation, a 100,000-signature ceiling, or a
$10M signed-volume ceiling. Earlier versions of this skill asserted all three as
requirements; they are not. What the sources actually say:

* **NIST SP 800-57 Part 1 Rev. 5**, Table 1 and Sec. 5.3.6(1)(b): for a *private signature
  key* "a maximum cryptoperiod of about one to three years is recommended. A private
  signature key shall be destroyed at the end of its cryptoperiod." Sec. 5.3.6 also warns
  that the suggested cryptoperiods "are only rough order-of-magnitude guidelines".
* **CCSS v9.0** specifies no rotation interval and no signature-count limit.
* **PCI DSS v4.0** Req. 3.7.4 leaves the cryptoperiod "as defined by the associated
  application vendor or key owner".

So 90 days is roughly 4-12x *shorter* than NIST's baseline for a signature key. That is a
defensible posture for a key that is online and continuously exposed -- SP 800-57 Sec.
5.3.1 lists the embodiment (factor 2), the operating environment (factor 3) and "the
volume of data flow or the number of transactions" (factor 5) among the things that
shorten a cryptoperiod -- but it is a *choice*, and every threshold on this engine is
configurable. The 90-day figure most likely entered circulation from AWS Config's
``ACCESS_KEYS_ROTATED`` rule, whose ``maxAccessKeyAge`` parameter defaults to 90; that is
a configurable default for IAM access keys, not a rule about wallet signing keys.

The signature ceiling does not protect against nonce bias
----------------------------------------------------------
A 100,000-signature cap is an **operational blast-radius cap** -- it bounds how many
transactions a single compromised key could have authorised -- and nothing more. It is not
a cryptographic bound:

* Where ECDSA nonces are biased or reused, private key recovery needs on the order of
  2 to a few hundred signatures, not 100,000 (Breitner & Heninger, "Biased Nonce Sense",
  eprint 2019/023). A key that leaks at signature 3 has already leaked long before any
  count-based trigger fires.
* Where nonces are deterministic -- RFC 6979 ECDSA, or Ed25519 per RFC 8032 -- there is no
  signature-count bound to respect at all.

Count-based rotation is therefore an exposure-limiting policy, not a defence against the
signature scheme. Rotating on a count while running a biased RNG fixes nothing.

A blockchain key cannot be revoked -- rotation means sweeping funds
--------------------------------------------------------------------
This is the distinction the engine exists to enforce, and the one most easily lost when
"key rotation" is reasoned about by analogy with API credentials.

* An **exchange/broker API key** (``KEY_CLASS_EXCHANGE_API``) can be revoked server-side.
  Once the venue revokes it, it is inert, and destroying the local copy is safe.
* An **on-chain signing key** (``KEY_CLASS_ONCHAIN_SIGNING``) cannot be revoked by anyone.
  A secp256k1 or Ed25519 private key controls its address permanently. "Rotating" it means
  generating a new address and **moving the balance to it**. Until that sweep confirms:

  - destroying the old key material strands every asset still at the old address, and
  - if the old key is compromised, the attacker keeps full authority over whatever remains.

  CCSS v9.0's key-compromise requirements are framed in exactly these terms -- regenerate
  the wallet and *send funds to the newly-generated wallet*.

So ``REVOKED_SHREDDED`` is unreachable for an on-chain key while ``residual_balance_usd``
is above zero. A compromised on-chain key with a residual balance yields
``EMERGENCY_SWEEP_REQUIRED``, not a shred instruction.

Sizing the grace period
-----------------------
The 24-hour dual-key overlap is a default, not a standard. Its floor is set by how long
work authorised under the old key can still be in flight, which is a property of the venue
and the chain, not of the calendar:

* Ethereum finalises across two consecutive epochs of 32 twelve-second slots -- roughly
  13 minutes -- and a transaction can sit unmined in the mempool for far longer.
* Bitcoin confirmation is probabilistic; conventional practice waits for several blocks.
* An exchange may settle, reconcile or reverse against an API key well after the last call.

Set ``grace_period_hours`` above the slowest of these for the venues actually in use. Note
that a grace period is a *drain* window for work already authorised -- the old key should
not be authorising anything new during it. If ``last_used_timestamp_epoch`` advances past
the grace start, the cutover did not happen and the report says so.

Shorter is not automatically safer
-----------------------------------
SP 800-57 Sec. 5.3.2 cautions that "short cryptoperiods may be counter-productive,
particularly where denial-of-service is the paramount concern and there is a significant
potential for error in the re-keying" -- and for a trading bot, a botched rotation means an
unhedged book, not merely an outage. Every on-chain rotation is also a fee-bearing,
publicly visible transaction. Rotate on a cadence the operator can execute reliably.

Known limitations
-----------------
* Nothing here verifies that a sweep, a revocation, or a memory zeroization actually
  occurred. ``residual_balance_usd`` and ``current_state`` are asserted by the caller.
* ``residual_balance_usd`` is a single USD figure. It will miss anything the caller's
  balance source misses -- tokens, NFTs, staked or locked positions, pending rewards, and
  contract allowances this address has granted. An address can be "empty" by balance and
  still carry authority worth stealing.
* On-chain dust would otherwise stall a rotation forever, so ``dust_threshold_usd`` exists.
  It defaults to ``0.0`` (strictly fail-closed); raising it is a deliberate, recordable
  decision, and is preferable to writing a false zero balance into the audit trail.
* ``replacement_key_id`` is ``f"{key_id}_V2"``. It is a label, and it does not compose --
  rotating ``K_V2`` proposes ``K_V2_V2``. Supply a real naming scheme upstream.
* Multi-signature and MPC key shares are not modelled; rotating one share of a quorum is a
  different procedure. See ``multi-party-computation-mpc-custody-solutions``.
* One key is audited per call. Cross-key invariants -- "never leave the wallet with zero
  active keys" -- belong to the caller.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# --- Lifecycle states -------------------------------------------------------------
#: Key is in service and within every rotation trigger.
STATE_ACTIVE = "ACTIVE"
#: Rotation has been initiated. A replacement has been proposed and the old key is
#: draining work that was already authorised under it.
STATE_DEPRECATED_GRACE_PERIOD = "DEPRECATED_GRACE_PERIOD"
#: The grace period has elapsed but value remains reachable by this key. The key material
#: MUST NOT be destroyed in this state -- destroying it strands the residual balance.
STATE_PENDING_FUND_SWEEP = "PENDING_FUND_SWEEP"
#: Terminal. The credential is revoked (API key) or its address is drained (on-chain key)
#: and the key material may be destroyed.
STATE_REVOKED_SHREDDED = "REVOKED_SHREDDED"

VALID_KEY_STATES = frozenset({
    STATE_ACTIVE,
    STATE_DEPRECATED_GRACE_PERIOD,
    STATE_PENDING_FUND_SWEEP,
    STATE_REVOKED_SHREDDED,
})

# --- Key classes ------------------------------------------------------------------
#: A blockchain private key. Cannot be revoked; rotation requires a fund sweep.
KEY_CLASS_ONCHAIN_SIGNING = "ONCHAIN_SIGNING"
#: An exchange or broker API credential. Revocable server-side; no address to sweep.
KEY_CLASS_EXCHANGE_API = "EXCHANGE_API"

VALID_KEY_CLASSES = frozenset({KEY_CLASS_ONCHAIN_SIGNING, KEY_CLASS_EXCHANGE_API})

# --- Report statuses --------------------------------------------------------------
STATUS_KEY_HEALTHY_ACTIVE = "KEY_HEALTHY_ACTIVE"
STATUS_ROTATION_INITIATED_AGE_EXPIRED = "ROTATION_INITIATED_AGE_EXPIRED"
STATUS_ROTATION_INITIATED_USAGE_EXPIRED = "ROTATION_INITIATED_USAGE_EXPIRED"
STATUS_ROTATION_INITIATED_VOLUME_EXPIRED = "ROTATION_INITIATED_VOLUME_EXPIRED"
STATUS_GRACE_PERIOD_ACTIVE = "GRACE_PERIOD_ACTIVE"
STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP = "GRACE_PERIOD_ELAPSED_PENDING_SWEEP"
STATUS_ROTATION_COMPLETE_KEY_SHREDDED = "ROTATION_COMPLETE_KEY_SHREDDED"
STATUS_EMERGENCY_REVOKED_COMPROMISED = "EMERGENCY_REVOKED_COMPROMISED"
STATUS_EMERGENCY_SWEEP_REQUIRED = "EMERGENCY_SWEEP_REQUIRED"
STATUS_KEY_ALREADY_REVOKED = "KEY_ALREADY_REVOKED"

#: Engineering defaults, not standards. See the module docstring: NIST SP 800-57 Pt 1 Rev 5
#: recommends one to three years for a private signature key and calls its own figures
#: "rough order-of-magnitude guidelines"; CCSS v9.0 sets no interval; PCI DSS v4.0 Req 3.7.4
#: leaves the cryptoperiod to the key owner. 90 days encodes a deliberately conservative
#: posture for a key that is online and continuously exposed.
DEFAULT_MAX_KEY_AGE_DAYS = 90.0
DEFAULT_MAX_SIGNATURES_LIMIT = 100_000
DEFAULT_MAX_VOLUME_USD_LIMIT = 10_000_000.0
DEFAULT_GRACE_PERIOD_HOURS = 24.0

#: Balance at or below which an on-chain address counts as swept. Defaults to zero, i.e.
#: strictly fail-closed: any residual value at all blocks destruction of the key. Raise it
#: only as a deliberate, recorded decision -- on-chain dust (leftover wei, dust UTXOs) is
#: common enough to stall a rotation indefinitely, and the alternative operators reach for
#: is to write a false zero into the balance, which corrupts the audit trail instead.
DEFAULT_DUST_THRESHOLD_USD = 0.0

#: Tolerance for ordinary NTP jitter between the clock that stamped the key and the clock
#: auditing it. A creation timestamp further in the future than this is treated as a data
#: error rather than silently clamped to "age zero" -- clamping is fail-open, and it makes
#: an arbitrarily old key report healthy forever.
DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS = 300.0

#: Epoch *seconds* for any realistic audit sit far below this. A value above it is almost
#: certainly milliseconds (``time.time() * 1000``), which would otherwise be read as a
#: far-future creation date and, before validation, reported as a brand-new key.
_MAX_PLAUSIBLE_EPOCH_SECONDS = 1e11

_SECONDS_PER_DAY = 86400.0
_SECONDS_PER_HOUR = 3600.0


class KeyRotationError(ValueError):
    """Raised when key metadata or engine configuration is invalid.

    A key rotation audit must fail loudly. A NaN signature count, a negative age limit, a
    millisecond timestamp passed where seconds were expected, or an unrecognised lifecycle
    state are data errors, and evaluating them anyway emits an authoritative-looking
    ``KEY_HEALTHY_ACTIVE`` verdict over a key that may be years past its cryptoperiod.
    """


def _validate_amount(value: float, label: str) -> float:
    """Return ``value`` as a finite, non-negative float, or raise ``KeyRotationError``."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise KeyRotationError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(amount):
        raise KeyRotationError(f"{label} must be finite, got {amount!r}.")
    if amount < 0.0:
        raise KeyRotationError(f"{label} must be non-negative, got {amount!r}.")
    return amount


def _validate_count(value: int, label: str) -> int:
    """Return ``value`` as a non-negative int, or raise ``KeyRotationError``.

    Rejects floats outright: a fractional signature count means the caller is aggregating
    something other than signatures.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise KeyRotationError(f"{label} must be an int, got {value!r}.")
    if value < 0:
        raise KeyRotationError(f"{label} must be non-negative, got {value!r}.")
    return value


def _validate_epoch(value: float, label: str) -> float:
    """Return ``value`` as a plausible POSIX epoch in **seconds**, or raise.

    Catches the millisecond-epoch mix-up explicitly, because that error is silent and
    fail-open: a millisecond timestamp reads as a far-future creation date, which an
    age check that clamps negatives to zero reports as a healthy new key indefinitely.
    """
    try:
        epoch = float(value)
    except (TypeError, ValueError) as exc:
        raise KeyRotationError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(epoch):
        raise KeyRotationError(f"{label} must be finite, got {epoch!r}.")
    if epoch <= 0.0:
        raise KeyRotationError(
            f"{label} must be a positive POSIX epoch in seconds, got {epoch!r}."
        )
    if epoch > _MAX_PLAUSIBLE_EPOCH_SECONDS:
        raise KeyRotationError(
            f"{label}={epoch!r} is too large to be epoch seconds and is almost certainly "
            f"milliseconds. Divide by 1000. Left unchecked this reads as a future creation "
            f"date and reports an expired key as healthy."
        )
    return epoch


@dataclass
class HotWalletKeyMetadata:
    """Point-in-time metadata for one online signing key.

    Every field is asserted by the caller; this module verifies none of them against a
    chain, a KMS or a venue.

    Attributes:
        key_id: Stable identifier, e.g. ``'HOT_KEY_ETH_001'``.
        created_timestamp_epoch: Key creation time, POSIX epoch **seconds**.
        last_used_timestamp_epoch: Last signing time, POSIX epoch **seconds**. Used to
            detect a cutover that never happened -- a key still signing after its grace
            period began.
        total_signatures_count: Signatures produced by this key over its life.
        total_volume_usd_signed: Cumulative USD notional authorised by this key.
        is_compromised: Operator's compromise assertion. Overrides every other trigger and
            skips the grace period entirely.
        current_state: One of ``VALID_KEY_STATES``. The engine advances this in place.
        key_class: ``KEY_CLASS_ONCHAIN_SIGNING`` (default, and the stricter path -- cannot
            be revoked, so requires a fund sweep before shredding) or
            ``KEY_CLASS_EXCHANGE_API`` (revocable server-side).
        residual_balance_usd: Value still reachable by this key. For an on-chain key this
            is the balance at its address, and it gates destruction of the key material.
            Ignored for an exchange API key, which controls no address of its own.
        grace_period_started_epoch: Set by the engine when it initiates rotation. Required
            once ``current_state`` is ``DEPRECATED_GRACE_PERIOD``; without it the grace
            expiry cannot be computed.
    """

    key_id: str
    created_timestamp_epoch: float
    last_used_timestamp_epoch: float
    total_signatures_count: int
    total_volume_usd_signed: float
    is_compromised: bool
    current_state: str = STATE_ACTIVE
    key_class: str = KEY_CLASS_ONCHAIN_SIGNING
    residual_balance_usd: float = 0.0
    grace_period_started_epoch: Optional[float] = None


@dataclass
class KeyRotationReport:
    """Verdict for a single key audit.

    ``new_key_state`` is the state the engine has moved the key into; it is also written
    back onto the supplied ``HotWalletKeyMetadata`` so that repeated audits advance the
    lifecycle rather than re-triggering it.
    """

    key_id: str
    key_age_days: float
    total_signatures_count: int
    total_volume_usd_signed: float
    is_rotation_required: bool
    new_key_state: str
    replacement_key_id: Optional[str]
    status: str
    audit_notes: str
    residual_balance_usd: float = 0.0
    requires_fund_sweep: bool = False
    grace_period_ends_epoch: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


class HotWalletKeyRotationEngine:
    """Evaluates rotation policy over hot wallet signing keys and broker API keys.

    The engine is a policy evaluator: it decides *what should happen* to a key and
    advances its lifecycle state. It never generates, holds, signs with, sweeps or
    destroys key material. See the module docstring for the reasoning behind each
    threshold and for why an on-chain key cannot reach ``REVOKED_SHREDDED`` while it
    still controls value.

    All thresholds are engineering defaults and are configurable. No standard mandates
    them.
    """

    def __init__(
        self,
        max_key_age_days: float = DEFAULT_MAX_KEY_AGE_DAYS,
        max_signatures_limit: int = DEFAULT_MAX_SIGNATURES_LIMIT,
        max_volume_usd_limit: float = DEFAULT_MAX_VOLUME_USD_LIMIT,
        grace_period_hours: float = DEFAULT_GRACE_PERIOD_HOURS,
        clock_skew_tolerance_seconds: float = DEFAULT_CLOCK_SKEW_TOLERANCE_SECONDS,
        dust_threshold_usd: float = DEFAULT_DUST_THRESHOLD_USD,
    ) -> None:
        self.max_key_age_days = _validate_amount(max_key_age_days, "max_key_age_days")
        if self.max_key_age_days <= 0.0:
            raise KeyRotationError("max_key_age_days must be positive.")

        self.max_signatures_limit = _validate_count(
            max_signatures_limit, "max_signatures_limit"
        )
        if self.max_signatures_limit == 0:
            raise KeyRotationError("max_signatures_limit must be positive.")

        self.max_volume_usd_limit = _validate_amount(
            max_volume_usd_limit, "max_volume_usd_limit"
        )
        if self.max_volume_usd_limit <= 0.0:
            raise KeyRotationError("max_volume_usd_limit must be positive.")

        self.grace_period_hours = _validate_amount(
            grace_period_hours, "grace_period_hours"
        )
        self.clock_skew_tolerance_seconds = _validate_amount(
            clock_skew_tolerance_seconds, "clock_skew_tolerance_seconds"
        )
        self.dust_threshold_usd = _validate_amount(
            dust_threshold_usd, "dust_threshold_usd"
        )

    # -- internals ------------------------------------------------------------------

    def _validate_metadata(self, key_meta: HotWalletKeyMetadata) -> None:
        if not isinstance(key_meta.key_id, str) or not key_meta.key_id.strip():
            raise KeyRotationError("key_id must be a non-empty string.")
        if key_meta.current_state not in VALID_KEY_STATES:
            raise KeyRotationError(
                f"[{key_meta.key_id}] unknown current_state "
                f"{key_meta.current_state!r}; expected one of {sorted(VALID_KEY_STATES)}."
            )
        if key_meta.key_class not in VALID_KEY_CLASSES:
            raise KeyRotationError(
                f"[{key_meta.key_id}] unknown key_class {key_meta.key_class!r}; "
                f"expected one of {sorted(VALID_KEY_CLASSES)}."
            )
        if not isinstance(key_meta.is_compromised, bool):
            raise KeyRotationError(
                f"[{key_meta.key_id}] is_compromised must be a bool, got "
                f"{key_meta.is_compromised!r}."
            )

        _validate_epoch(
            key_meta.created_timestamp_epoch,
            f"[{key_meta.key_id}] created_timestamp_epoch",
        )
        _validate_epoch(
            key_meta.last_used_timestamp_epoch,
            f"[{key_meta.key_id}] last_used_timestamp_epoch",
        )
        _validate_count(
            key_meta.total_signatures_count,
            f"[{key_meta.key_id}] total_signatures_count",
        )
        _validate_amount(
            key_meta.total_volume_usd_signed,
            f"[{key_meta.key_id}] total_volume_usd_signed",
        )
        _validate_amount(
            key_meta.residual_balance_usd,
            f"[{key_meta.key_id}] residual_balance_usd",
        )

        if (
            key_meta.last_used_timestamp_epoch
            < key_meta.created_timestamp_epoch - self.clock_skew_tolerance_seconds
        ):
            raise KeyRotationError(
                f"[{key_meta.key_id}] last_used_timestamp_epoch "
                f"({key_meta.last_used_timestamp_epoch}) precedes created_timestamp_epoch "
                f"({key_meta.created_timestamp_epoch}); a key cannot sign before it exists."
            )

        if key_meta.grace_period_started_epoch is not None:
            _validate_epoch(
                key_meta.grace_period_started_epoch,
                f"[{key_meta.key_id}] grace_period_started_epoch",
            )
        elif key_meta.current_state == STATE_DEPRECATED_GRACE_PERIOD:
            raise KeyRotationError(
                f"[{key_meta.key_id}] is in {STATE_DEPRECATED_GRACE_PERIOD} but has no "
                f"grace_period_started_epoch; the grace expiry cannot be computed. Set it "
                f"to the moment rotation was initiated."
            )

    def _age_seconds(self, key_meta: HotWalletKeyMetadata, now: float) -> float:
        """Age in seconds, rejecting a creation timestamp implausibly far in the future.

        Small negative ages are ordinary clock skew between hosts and are clamped. A large
        one means the timestamp is wrong, and clamping it would be fail-open: the key would
        report ``KEY_HEALTHY_ACTIVE`` no matter how old it really is.
        """
        delta = now - key_meta.created_timestamp_epoch
        if delta < -self.clock_skew_tolerance_seconds:
            raise KeyRotationError(
                f"[{key_meta.key_id}] created_timestamp_epoch "
                f"({key_meta.created_timestamp_epoch}) is "
                f"{-delta:,.0f}s in the future relative to the audit time ({now}), beyond "
                f"the {self.clock_skew_tolerance_seconds:,.0f}s skew tolerance. Refusing to "
                f"report age 0 for a key of unknown age."
            )
        return max(0.0, delta)

    def _requires_sweep(self, key_meta: HotWalletKeyMetadata) -> bool:
        """True when key material must be preserved until value is moved off this key.

        Only on-chain keys gate on this. An exchange API key controls no address of its
        own; revoking it at the venue is sufficient and the account balance is unaffected.
        """
        return (
            key_meta.key_class == KEY_CLASS_ONCHAIN_SIGNING
            and key_meta.residual_balance_usd > self.dust_threshold_usd
        )

    def _build(
        self,
        key_meta: HotWalletKeyMetadata,
        age_days: float,
        *,
        state: str,
        status: str,
        rotation_required: bool,
        notes: str,
        replacement_key_id: Optional[str] = None,
        grace_ends: Optional[float] = None,
        warnings: Optional[List[str]] = None,
    ) -> KeyRotationReport:
        key_meta.current_state = state
        return KeyRotationReport(
            key_id=key_meta.key_id,
            key_age_days=round(age_days, 2),
            total_signatures_count=key_meta.total_signatures_count,
            total_volume_usd_signed=key_meta.total_volume_usd_signed,
            is_rotation_required=rotation_required,
            new_key_state=state,
            replacement_key_id=replacement_key_id,
            status=status,
            audit_notes=notes,
            residual_balance_usd=key_meta.residual_balance_usd,
            requires_fund_sweep=self._requires_sweep(key_meta),
            grace_period_ends_epoch=grace_ends,
            warnings=list(warnings or []),
        )

    # -- public API -----------------------------------------------------------------

    def audit_and_rotate_key(
        self,
        key_meta: HotWalletKeyMetadata,
        current_time_epoch: Optional[float] = None,
    ) -> KeyRotationReport:
        """Audit one key and advance its lifecycle state.

        Evaluation order, which is also the safety order:

        1. A key already in ``REVOKED_SHREDDED`` stays there. It is never re-audited back
           into service.
        2. Compromise overrides every other trigger and skips the grace period. For an
           on-chain key still holding value the verdict is ``EMERGENCY_SWEEP_REQUIRED``,
           not a shred instruction -- the attacker controls that address until it is
           drained, and destroying the key strands whatever is left.
        3. A key awaiting a sweep is shredded only once its residual balance reaches zero.
        4. A key inside its grace period is reported idempotently: the same replacement
           label, no fresh rotation event. Once the grace window elapses it moves to a
           sweep or straight to shredded.
        5. An active key is tested against the age, signature-count and volume triggers,
           on **unrounded** age -- rounding is for display only, and classifying on a
           rounded value lets presentation decide policy.

        Args:
            key_meta: Metadata for the key. Mutated in place: ``current_state`` and, on
                rotation initiation, ``grace_period_started_epoch`` are updated so that a
                subsequent call advances the lifecycle instead of re-triggering it.
            current_time_epoch: Audit time, POSIX epoch seconds. Defaults to
                ``time.time()``; pass it explicitly for deterministic tests and replays.

        Returns:
            A ``KeyRotationReport``. ``new_key_state`` is an instruction to a rotation
            runbook, never evidence that rotation, revocation or shredding happened.

        Raises:
            KeyRotationError: on invalid metadata, invalid engine configuration, or a
                creation timestamp implausibly far in the future.
        """
        self._validate_metadata(key_meta)

        if current_time_epoch is None:
            now = time.time()
        else:
            now = _validate_epoch(current_time_epoch, "current_time_epoch")

        # A grace start dated in the future never elapses, so the key would sit in
        # GRACE_PERIOD_ACTIVE indefinitely -- the same fail-open shape as a future-dated
        # creation timestamp, and just as silent.
        grace_started = key_meta.grace_period_started_epoch
        if (
            grace_started is not None
            and grace_started > now + self.clock_skew_tolerance_seconds
        ):
            raise KeyRotationError(
                f"[{key_meta.key_id}] grace_period_started_epoch ({grace_started}) is in "
                f"the future relative to the audit time ({now}); the grace period would "
                f"never elapse and the key would never leave the grace state."
            )

        age_days = self._age_seconds(key_meta, now) / _SECONDS_PER_DAY
        warnings: List[str] = []

        # 1. Terminal state. A revoked key is never resurrected as healthy.
        if key_meta.current_state == STATE_REVOKED_SHREDDED:
            notes = (
                f"KEY ALREADY REVOKED [{key_meta.key_id}]: terminal state; no further action."
            )
            if (
                key_meta.residual_balance_usd > 0.0
                and key_meta.key_class == KEY_CLASS_ONCHAIN_SIGNING
            ):
                stranded = (
                    f"${key_meta.residual_balance_usd:,.2f} remains at the address of a key "
                    f"already marked shredded. If the material really was destroyed those "
                    f"funds are unrecoverable; if it survives, an obsolete key still "
                    f"controls value."
                )
                warnings.append(stranded)
                logger.error("STRANDED FUNDS [%s]: %s", key_meta.key_id, stranded)
            if key_meta.is_compromised:
                # Rotation has nothing left to do, but the flag must not vanish silently:
                # what matters now is what this key signed while it was live.
                post_mortem = (
                    "Key is flagged compromised but was already revoked. Rotation has no "
                    "further action; the open question is what this key authorised before "
                    "revocation. Escalate to incident forensics."
                )
                warnings.append(post_mortem)
                logger.critical("COMPROMISED AFTER REVOCATION [%s]: %s", key_meta.key_id, post_mortem)
            logger.info(notes)
            return self._build(
                key_meta, age_days,
                state=STATE_REVOKED_SHREDDED,
                status=STATUS_KEY_ALREADY_REVOKED,
                rotation_required=False,
                notes=notes,
                warnings=warnings,
            )

        # 2. Compromise overrides everything and never gets a grace period.
        if key_meta.is_compromised:
            if self._requires_sweep(key_meta):
                notes = (
                    f"EMERGENCY SWEEP REQUIRED [{key_meta.key_id}]: key COMPROMISED with "
                    f"${key_meta.residual_balance_usd:,.2f} still reachable. An on-chain key "
                    f"cannot be revoked -- the attacker retains authority over this address "
                    f"until the balance is moved. Sweep to a freshly generated address "
                    f"first; do NOT destroy the key material yet."
                )
                logger.critical(notes)
                return self._build(
                    key_meta, age_days,
                    state=STATE_PENDING_FUND_SWEEP,
                    status=STATUS_EMERGENCY_SWEEP_REQUIRED,
                    rotation_required=True,
                    notes=notes,
                    replacement_key_id=f"{key_meta.key_id}_EMERGENCY_REPLACEMENT",
                )
            notes = (
                f"EMERGENCY REVOCATION [{key_meta.key_id}]: key COMPROMISED with no residual "
                f"balance reachable. Revoke at the venue / destroy material immediately; no "
                f"grace period applies to a compromised key."
            )
            logger.critical(notes)
            return self._build(
                key_meta, age_days,
                state=STATE_REVOKED_SHREDDED,
                status=STATUS_EMERGENCY_REVOKED_COMPROMISED,
                rotation_required=True,
                notes=notes,
                replacement_key_id=f"{key_meta.key_id}_EMERGENCY_REPLACEMENT",
            )

        # 3. Awaiting a sweep: shred only once nothing of value is reachable.
        if key_meta.current_state == STATE_PENDING_FUND_SWEEP:
            if self._requires_sweep(key_meta):
                notes = (
                    f"PENDING FUND SWEEP [{key_meta.key_id}]: "
                    f"${key_meta.residual_balance_usd:,.2f} still at this address. Key "
                    f"material MUST be retained until the sweep confirms."
                )
                logger.warning(notes)
                return self._build(
                    key_meta, age_days,
                    state=STATE_PENDING_FUND_SWEEP,
                    status=STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP,
                    rotation_required=True,
                    notes=notes,
                    replacement_key_id=f"{key_meta.key_id}_V2",
                )
            notes = (
                f"ROTATION COMPLETE [{key_meta.key_id}]: sweep confirmed, no value reachable. "
                f"Key material may now be destroyed."
            )
            logger.info(notes)
            return self._build(
                key_meta, age_days,
                state=STATE_REVOKED_SHREDDED,
                status=STATUS_ROTATION_COMPLETE_KEY_SHREDDED,
                rotation_required=False,
                notes=notes,
            )

        # 4. Inside or past the grace period. Idempotent while the window is open.
        if key_meta.current_state == STATE_DEPRECATED_GRACE_PERIOD:
            # _validate_metadata guarantees this is set for this state, and the future-date
            # guard above guarantees it has already begun.
            grace_started = float(key_meta.grace_period_started_epoch)
            grace_ends = grace_started + self.grace_period_hours * _SECONDS_PER_HOUR
            repl_id = f"{key_meta.key_id}_V2"

            if (
                key_meta.last_used_timestamp_epoch
                > grace_started + self.clock_skew_tolerance_seconds
            ):
                cutover = (
                    f"Key signed at {key_meta.last_used_timestamp_epoch}, after its grace "
                    f"period began at {grace_started}. A grace period drains work already "
                    f"authorised; it is not a licence to keep signing. The cutover to the "
                    f"replacement did not happen."
                )
                warnings.append(cutover)
                logger.warning("CUTOVER INCOMPLETE [%s]: %s", key_meta.key_id, cutover)

            if now < grace_ends:
                remaining_h = (grace_ends - now) / _SECONDS_PER_HOUR
                notes = (
                    f"GRACE PERIOD ACTIVE [{key_meta.key_id}]: {remaining_h:.2f}h remaining "
                    f"of {self.grace_period_hours:.2f}h. Replacement '{repl_id}' should be "
                    f"carrying new work; this key is draining only."
                )
                logger.info(notes)
                return self._build(
                    key_meta, age_days,
                    state=STATE_DEPRECATED_GRACE_PERIOD,
                    status=STATUS_GRACE_PERIOD_ACTIVE,
                    rotation_required=True,
                    notes=notes,
                    replacement_key_id=repl_id,
                    grace_ends=grace_ends,
                    warnings=warnings,
                )

            if self._requires_sweep(key_meta):
                notes = (
                    f"GRACE PERIOD ELAPSED [{key_meta.key_id}]: window closed but "
                    f"${key_meta.residual_balance_usd:,.2f} remains reachable. Sweep to the "
                    f"replacement address before destroying key material."
                )
                logger.warning(notes)
                return self._build(
                    key_meta, age_days,
                    state=STATE_PENDING_FUND_SWEEP,
                    status=STATUS_GRACE_PERIOD_ELAPSED_PENDING_SWEEP,
                    rotation_required=True,
                    notes=notes,
                    replacement_key_id=repl_id,
                    grace_ends=grace_ends,
                    warnings=warnings,
                )

            notes = (
                f"ROTATION COMPLETE [{key_meta.key_id}]: grace period elapsed and no value "
                f"reachable. Revoke / destroy key material."
            )
            logger.info(notes)
            return self._build(
                key_meta, age_days,
                state=STATE_REVOKED_SHREDDED,
                status=STATUS_ROTATION_COMPLETE_KEY_SHREDDED,
                rotation_required=False,
                notes=notes,
                grace_ends=grace_ends,
                warnings=warnings,
            )

        # 5. Active key: evaluate the rotation triggers on unrounded age.
        is_age_expired = age_days >= self.max_key_age_days
        is_usage_expired = key_meta.total_signatures_count >= self.max_signatures_limit
        is_volume_expired = key_meta.total_volume_usd_signed >= self.max_volume_usd_limit

        if is_age_expired or is_usage_expired or is_volume_expired:
            repl_id = f"{key_meta.key_id}_V2"
            if is_age_expired:
                reason = (
                    f"Age ({age_days:.2f} days >= {self.max_key_age_days:.2f} day limit)"
                )
                status = STATUS_ROTATION_INITIATED_AGE_EXPIRED
            elif is_usage_expired:
                reason = (
                    f"Signatures ({key_meta.total_signatures_count:,} >= "
                    f"{self.max_signatures_limit:,} limit)"
                )
                status = STATUS_ROTATION_INITIATED_USAGE_EXPIRED
            else:
                reason = (
                    f"Volume (${key_meta.total_volume_usd_signed:,.0f} >= "
                    f"${self.max_volume_usd_limit:,.0f} limit)"
                )
                status = STATUS_ROTATION_INITIATED_VOLUME_EXPIRED

            key_meta.grace_period_started_epoch = now
            grace_ends = now + self.grace_period_hours * _SECONDS_PER_HOUR
            notes = (
                f"KEY ROTATION INITIATED [{key_meta.key_id}]: {reason}. Transitioned to "
                f"'{STATE_DEPRECATED_GRACE_PERIOD}' ({self.grace_period_hours:.2f}h). "
                f"Proposed replacement label '{repl_id}' -- generate the key out of band; "
                f"this engine does not create it."
            )
            logger.warning(notes)
            return self._build(
                key_meta, age_days,
                state=STATE_DEPRECATED_GRACE_PERIOD,
                status=status,
                rotation_required=True,
                notes=notes,
                replacement_key_id=repl_id,
                grace_ends=grace_ends,
                warnings=warnings,
            )

        notes = (
            f"KEY HEALTHY [{key_meta.key_id}]: Age = {age_days:.2f} days, "
            f"Txs = {key_meta.total_signatures_count:,}, "
            f"Volume = ${key_meta.total_volume_usd_signed:,.0f} USD. State = {STATE_ACTIVE}."
        )
        logger.info(notes)
        return self._build(
            key_meta, age_days,
            state=STATE_ACTIVE,
            status=STATUS_KEY_HEALTHY_ACTIVE,
            rotation_required=False,
            notes=notes,
            warnings=warnings,
        )
