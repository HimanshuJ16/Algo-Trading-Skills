"""withdrawal-velocity-limits-and-anomaly-detection: a pre-disbursement gate that
scores a crypto withdrawal request against rolling velocity caps, a per-account
size-anomaly baseline, and the age of the destination address record — then
returns APPROVED, TIMELOCK_HOLD, or REJECTED_FREEZE.

What this module is and is not
------------------------------
It is the **amount and rate** layer of a withdrawal gate. It answers "is this
much, this fast, unusual for this account or for the hot wallet as a whole?"

It is **not** an allowlist. It does not own address registration, network
scoping, memo binding, revocation, or address canonicalisation — that is
``exchange-withdrawal-whitelist-enforcement``. What this engine does with the
``AddressWhitelistRecord`` it is handed is narrow and defensive: it verifies the
record actually *binds* to this request's account and destination address, and
then measures the record's age. A record that does not bind is discarded as
evidence and the destination is treated as unwhitelisted.

It is also **not** the enforcer of last resort. It runs inside your own
infrastructure, in front of your own signer. An attacker who has your custodian
API credentials calls the custodian directly and never executes this code.
Configure the equivalent policy at the custodian too — see "Vendor mapping" in
``references/standards.md``.

Thresholds here are policy, not standards
-----------------------------------------
No regulator prescribes a withdrawal velocity number. The Hong Kong SFC comes
closest to naming the control class — circular SFO/IS/005/2026 (11 Feb 2026),
para 20, expects VA brokers to strengthen "abnormal withdrawal detection, such
as by setting **appropriate** withdrawal limits at the omnibus account level or
for newly whitelisted client wallets, blocking suspicious withdrawal attempts,
and ensuring timely escalation" — but "appropriate" is left to the firm. Every
default in this module is an engineering placeholder to be calibrated against
your own flow. See ``references/standards.md``.

Trusted clock
-------------
``evaluate_withdrawal_request`` measures every window and every address age
against an ``evaluation_timestamp`` supplied by the caller (defaulting to
``datetime.now(timezone.utc)``), **never** against ``WithdrawalRequest.timestamp``.
The request timestamp travels with a potentially attacker-controlled request; a
velocity window you can slide by writing a number into the request is not a
limit. The request timestamp is recorded and skew-checked only.

Concurrency and state
---------------------
The engine is **not** thread-safe and holds all state in memory. Serialise the
evaluate-then-submit sequence, and back the state with durable storage before
relying on it across a restart — an in-memory ledger that is lost on restart
resets every rolling window to zero, which is precisely the window an attacker
wants.

The velocity ledger is pruned to the longest window in use. The replay cache and
the held-request map are **not** pruned: idempotency means a decision must stay
answerable for as long as the client might retry, and holds live until an
operator releases or cancels them. Both therefore grow with request volume. In
production, back them with durable storage keyed by ``request_id`` and expire the
replay cache on your own retry horizon rather than letting the process own them
for its lifetime.
"""
from __future__ import annotations

import datetime
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

__all__ = [
    "WithdrawalStatus",
    "RiskFlag",
    "VelocityEngineError",
    "WithdrawalRequest",
    "AddressWhitelistRecord",
    "AccountHistoricalProfile",
    "WithdrawalDecision",
    "WithdrawalVelocityEngine",
    "max_attainable_in_sample_zscore",
]

UTC = datetime.timezone.utc


class WithdrawalStatus(Enum):
    APPROVED = "APPROVED"                       # Instant automated execution
    TIMELOCK_HOLD = "TIMELOCK_HOLD"             # Enforce cooling-off / manual multi-sig review
    REJECTED_FREEZE = "REJECTED_FREEZE"         # Global hot wallet circuit breaker latched


class RiskFlag(Enum):
    EXCEEDS_HOURLY_LIMIT = "EXCEEDS_HOURLY_LIMIT"
    EXCEEDS_DAILY_LIMIT = "EXCEEDS_DAILY_LIMIT"
    ANOMALY_SIZE_ZSCORE = "ANOMALY_SIZE_ZSCORE"
    NEW_ADDRESS_HOLD = "NEW_ADDRESS_HOLD"
    HOT_WALLET_LIMIT_EXCEEDED = "HOT_WALLET_LIMIT_EXCEEDED"
    #: The supplied whitelist record does not bind to this request's account and
    #: destination address. Integration fault or tampering — never auto-release.
    WHITELIST_RECORD_MISMATCH = "WHITELIST_RECORD_MISMATCH"
    #: The account has too few historical withdrawals for a Z-score to mean
    #: anything, so the size-anomaly check could not run. Held, not waved through.
    INSUFFICIENT_PROFILE_HISTORY = "INSUFFICIENT_PROFILE_HISTORY"


class VelocityEngineError(Exception):
    """Base exception for Withdrawal Velocity Engine errors."""


def max_attainable_in_sample_zscore(n: int) -> float:
    """Largest |Z| any single observation *within* a sample of size ``n`` can take,
    when mu and sigma are that sample's own mean and (n-1 denominator) standard
    deviation.

    This is the Grubbs bound, ``(n - 1) / sqrt(n)``. It is why a "3-sigma"
    withdrawal rule is incoherent on a short history: with n = 5 no historical
    withdrawal could have scored above 1.79, so a threshold of 3.0 was never
    calibrated against anything the account actually did. Z = 3.0 first becomes
    attainable in-sample at n = 11.

    The bound constrains points *inside* the sample. A new request is
    out-of-sample and may exceed it — but a sigma estimated from a handful of
    points is too unstable for the excess to mean much.
    """
    if n < 2:
        raise VelocityEngineError("Sample size must be at least 2 to define a Z-score.")
    return (n - 1) / math.sqrt(n)


def _as_utc(value: datetime.datetime, label: str) -> datetime.datetime:
    """Normalise a datetime to timezone-aware UTC.

    A naive datetime is *assumed* to be UTC, matching this module's historical
    behaviour of comparing ``datetime.utcnow()`` values. Mixing naive and aware
    datetimes otherwise raises ``TypeError`` deep inside an arithmetic
    expression, which in a risk gate surfaces as an unhandled crash rather than
    a decision.
    """
    if not isinstance(value, datetime.datetime):
        raise VelocityEngineError(f"{label} must be a datetime, got {type(value).__name__}.")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _require_finite_positive(value: float, label: str) -> float:
    """Reject NaN/Inf/non-positive amounts loudly.

    ``float('nan') >= 3.0`` is ``False``, so an unchecked NaN does not raise —
    it silently answers "not anomalous" and turns the control off.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise VelocityEngineError(f"{label} must be numeric, got {value!r}.") from exc
    if not math.isfinite(numeric):
        raise VelocityEngineError(f"{label} must be finite, got {numeric!r}.")
    if numeric <= 0:
        raise VelocityEngineError(f"{label} must be positive, got {numeric!r}.")
    return numeric


@dataclass
class WithdrawalRequest:
    request_id: str
    account_id: str
    asset: str
    amount_crypto: float
    amount_usd: float
    destination_address: str
    #: Client-asserted submission time. Recorded and skew-checked only — it is
    #: never used as the clock for velocity windows or address age.
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(UTC)
    )


@dataclass
class AddressWhitelistRecord:
    account_id: str
    address: str
    added_timestamp: datetime.datetime
    is_whitelisted: bool = True


@dataclass
class AccountHistoricalProfile:
    account_id: str
    mean_withdrawal_usd: float
    std_dev_usd: float
    historical_count: int


@dataclass
class WithdrawalDecision:
    request_id: str
    account_id: str
    status: WithdrawalStatus
    decision_timestamp: datetime.datetime
    risk_flags: List[RiskFlag] = field(default_factory=list)
    rationale: str = ""
    required_hold_hours: float = 0.0
    #: Z-score actually computed, or ``None`` when the check could not run.
    #: ``None`` means "did not run", which is not the same as "passed".
    anomaly_zscore: Optional[float] = None
    #: Non-fatal integrity observations (clock skew, stale profile, and so on).
    warnings: List[str] = field(default_factory=list)


class WithdrawalVelocityEngine:
    """Withdrawal velocity limits and size-anomaly detection for a hot wallet gate.

    Enforces rolling per-account 1h/24h USD caps and a global hot-wallet 1h cap,
    scores request size against the account's own historical baseline, and checks
    that the destination address record binds to the request and has aged past
    the cooling period.

    The global cap is a **latching** circuit breaker: once tripped it stays
    tripped, and every subsequent request returns ``REJECTED_FREEZE`` until
    :meth:`reset_hot_wallet_freeze` is called. A breaker that re-arms itself the
    moment the rolling window decays is not a breaker — the attacker simply
    waits it out.
    """

    def __init__(
        self,
        account_hourly_limit_usd: float = 100_000.0,
        account_daily_limit_usd: float = 500_000.0,
        global_hot_wallet_hourly_limit_usd: float = 2_000_000.0,
        anomaly_zscore_threshold: float = 3.0,
        address_whitelist_cooling_hours: float = 24.0,
        min_profile_observations: int = 30,
        timelock_hold_hours: float = 24.0,
        max_clock_skew_seconds: float = 300.0,
    ):
        """
        :param account_hourly_limit_usd: Max 1-hour rolling USD velocity per account.
        :param account_daily_limit_usd: Max 24-hour rolling USD velocity per account.
        :param global_hot_wallet_hourly_limit_usd: Max 1-hour rolling USD velocity
            across every account sharing the hot wallet.
        :param anomaly_zscore_threshold: Z above which a request size is flagged.
        :param address_whitelist_cooling_hours: Required age of a destination
            address record before automated release.
        :param min_profile_observations: Minimum historical withdrawals before a
            Z-score is treated as meaningful. Below it the check is recorded as
            not-run and the request is held, never silently approved.
        :param timelock_hold_hours: Hold applied to a flagged request.
        :param max_clock_skew_seconds: Tolerated gap between the request's own
            timestamp and the trusted clock before a warning is recorded.
        :raises VelocityEngineError: on a non-finite, non-positive, or internally
            incoherent configuration.
        """
        self.account_hourly_limit_usd = _require_finite_positive(
            account_hourly_limit_usd, "account_hourly_limit_usd")
        self.account_daily_limit_usd = _require_finite_positive(
            account_daily_limit_usd, "account_daily_limit_usd")
        self.global_hot_wallet_hourly_limit_usd = _require_finite_positive(
            global_hot_wallet_hourly_limit_usd, "global_hot_wallet_hourly_limit_usd")
        self.anomaly_zscore_threshold = _require_finite_positive(
            anomaly_zscore_threshold, "anomaly_zscore_threshold")
        self.timelock_hold_hours = _require_finite_positive(
            timelock_hold_hours, "timelock_hold_hours")

        cooling = float(address_whitelist_cooling_hours)
        if not math.isfinite(cooling) or cooling < 0:
            raise VelocityEngineError(
                f"address_whitelist_cooling_hours must be finite and non-negative, got {cooling!r}.")
        self.address_whitelist_cooling_hours = cooling

        skew = float(max_clock_skew_seconds)
        if not math.isfinite(skew) or skew < 0:
            raise VelocityEngineError(
                f"max_clock_skew_seconds must be finite and non-negative, got {skew!r}.")
        self.max_clock_skew_seconds = skew

        if int(min_profile_observations) < 2:
            raise VelocityEngineError("min_profile_observations must be at least 2.")
        self.min_profile_observations = int(min_profile_observations)

        # A threshold no observation in a minimally-sized profile could ever have
        # reached is a control that is dead on arrival. Refuse it at construction
        # rather than shipping a rule that never fires. See the Grubbs bound.
        attainable = max_attainable_in_sample_zscore(self.min_profile_observations)
        if attainable < self.anomaly_zscore_threshold:
            raise VelocityEngineError(
                f"anomaly_zscore_threshold={self.anomaly_zscore_threshold} is unattainable for a "
                f"profile of {self.min_profile_observations} observations: the largest in-sample "
                f"|Z| possible is {attainable:.2f}. Raise min_profile_observations to at least "
                f"{self._min_n_for_threshold(self.anomaly_zscore_threshold)} or lower the threshold."
            )

        if self.account_hourly_limit_usd > self.account_daily_limit_usd:
            raise VelocityEngineError(
                f"account_hourly_limit_usd ({self.account_hourly_limit_usd}) exceeds "
                f"account_daily_limit_usd ({self.account_daily_limit_usd}); the daily cap "
                f"could never bind.")

        #: Ledger of withdrawals that actually consumed capacity (approved, or a
        #: hold subsequently released). Pruned to the longest window in use.
        self.withdrawal_ledger: List[WithdrawalRequest] = []
        #: request_id -> decision, so a retried submission replays its decision
        #: instead of being scored (and counted) a second time.
        self._decisions: Dict[str, WithdrawalDecision] = {}
        #: Requests held pending review, awaiting release or cancellation.
        self._held_requests: Dict[str, WithdrawalRequest] = {}

        self.hot_wallet_frozen: bool = False
        self.freeze_reason: str = ""
        self.frozen_at: Optional[datetime.datetime] = None

        self._max_window_hours: float = 24.0

        logger.info(
            "Initialized Withdrawal Velocity Engine (Account 1h=$%s, 24h=$%s, "
            "HotWallet 1h=$%s, Z>=%.2f on >=%d observations)",
            f"{self.account_hourly_limit_usd:,.0f}",
            f"{self.account_daily_limit_usd:,.0f}",
            f"{self.global_hot_wallet_hourly_limit_usd:,.0f}",
            self.anomaly_zscore_threshold,
            self.min_profile_observations,
        )

    @staticmethod
    def _min_n_for_threshold(threshold: float) -> int:
        """Smallest sample size whose Grubbs bound reaches ``threshold``."""
        n = 2
        while max_attainable_in_sample_zscore(n) < threshold and n < 10_000:
            n += 1
        return n

    # ----------------------------------------------------------------- ledger

    def _prune_ledger(self, current_time: datetime.datetime) -> None:
        """Drop entries older than the longest window in use.

        Without this the ledger grows for the life of the process and every
        evaluation rescans all of history — on a gateway doing continuous
        withdrawals that is an unbounded memory leak and a growing per-request
        cost, in the hot path of a live disbursement queue.
        """
        cutoff = current_time - datetime.timedelta(hours=self._max_window_hours)
        # Unconditional: entries are not strictly ordered, because a released
        # hold is appended at its release time, which may precede the newest
        # approval. A "is the head stale?" fast path would then skip pruning
        # and let the ledger grow without bound.
        self.withdrawal_ledger = [
            req for req in self.withdrawal_ledger if req.timestamp >= cutoff
        ]

    def get_rolling_velocity_usd(
        self,
        account_id: Optional[str],
        window_hours: float,
        current_time: datetime.datetime,
    ) -> float:
        """Total USD withdrawn in the closed window ``[current_time - window_hours,
        current_time]``. ``account_id=None`` totals the whole hot wallet.

        The window is bounded at **both** ends. Without the upper bound a single
        future-dated ledger entry counts toward every window computed from now
        until real time overtakes it, quietly inflating velocity and eventually
        tripping the global breaker on legitimate flow.
        """
        try:
            window_hours = float(window_hours)
        except (TypeError, ValueError) as exc:
            raise VelocityEngineError(
                f"window_hours must be numeric, got {window_hours!r}.") from exc
        if not math.isfinite(window_hours) or window_hours <= 0:
            raise VelocityEngineError(
                f"window_hours must be finite and positive, got {window_hours!r}.")
        now = _as_utc(current_time, "current_time")
        cutoff = now - datetime.timedelta(hours=window_hours)

        total_usd = 0.0
        for req in self.withdrawal_ledger:
            if cutoff <= req.timestamp <= now:
                if account_id is None or req.account_id == account_id:
                    total_usd += req.amount_usd
        return total_usd

    # ------------------------------------------------------------- evaluation

    def evaluate_withdrawal_request(
        self,
        request: WithdrawalRequest,
        whitelist_record: Optional[AddressWhitelistRecord],
        profile: AccountHistoricalProfile,
        evaluation_timestamp: Optional[datetime.datetime] = None,
    ) -> WithdrawalDecision:
        """Score one withdrawal request and return a decision.

        :param request: the withdrawal being requested.
        :param whitelist_record: the allowlist entry the caller looked up for
            this request, or ``None`` if the destination is not allowlisted. The
            engine re-verifies that the record binds to ``request.account_id``
            and ``request.destination_address``; a record that does not bind is
            discarded as evidence.
        :param profile: the account's historical withdrawal baseline.
        :param evaluation_timestamp: the trusted clock. Defaults to now (UTC).
            Pass it explicitly for reproducible audits.
        :raises VelocityEngineError: on malformed request or profile input.
        """
        if not isinstance(request.request_id, str) or not request.request_id.strip():
            raise VelocityEngineError("request_id must be a non-empty string.")
        if not isinstance(request.account_id, str) or not request.account_id.strip():
            raise VelocityEngineError("account_id must be a non-empty string.")
        if not isinstance(request.destination_address, str) or not request.destination_address.strip():
            raise VelocityEngineError("destination_address must be a non-empty string.")

        # Idempotency: a broker/gateway timeout means *unknown*, not *failed*.
        # A retry of the same request_id must replay the original decision, not
        # score it again against a ledger the first attempt already updated.
        cached = self._decisions.get(request.request_id)
        if cached is not None:
            logger.info("REPLAYED DECISION [%s]: %s (idempotent retry).",
                        request.request_id, cached.status.value)
            return cached

        _require_finite_positive(request.amount_usd, "amount_usd")
        _require_finite_positive(request.amount_crypto, "amount_crypto")

        now = _as_utc(
            evaluation_timestamp if evaluation_timestamp is not None
            else datetime.datetime.now(UTC),
            "evaluation_timestamp",
        )
        # Normalised locally: mutating the caller's request object would be a
        # surprising side effect on an input the caller may reuse or log.
        asserted_at = _as_utc(request.timestamp, "request.timestamp")
        self._prune_ledger(now)

        warnings: List[str] = []
        skew_seconds = abs((asserted_at - now).total_seconds())
        if skew_seconds > self.max_clock_skew_seconds:
            message = (f"Request timestamp is {skew_seconds:.0f}s from the trusted clock "
                       f"(tolerance {self.max_clock_skew_seconds:.0f}s).")
            warnings.append(message)
            logger.warning("CLOCK SKEW [%s]: %s", request.request_id, message)

        # 0. Latched breaker — an already-frozen hot wallet rejects everything.
        if self.hot_wallet_frozen:
            return self._finalise(self._freeze_decision(
                request, now,
                "Hot wallet is frozen pending manual reset. " + self.freeze_reason,
                warnings,
            ))

        # 1. Global hot wallet hourly velocity — trips the breaker.
        global_1h = self.get_rolling_velocity_usd(None, 1.0, now)
        if (global_1h + request.amount_usd) > self.global_hot_wallet_hourly_limit_usd:
            self.hot_wallet_frozen = True
            self.frozen_at = now
            self.freeze_reason = (
                f"Global 1h velocity ${global_1h + request.amount_usd:,.2f} exceeded limit "
                f"${self.global_hot_wallet_hourly_limit_usd:,.2f} on request {request.request_id}.")
            logger.error("HOT WALLET CIRCUIT BREAKER LATCHED: %s", self.freeze_reason)
            return self._finalise(self._freeze_decision(
                request, now,
                "Global hot wallet hourly withdrawal limit exceeded. Automated withdrawals "
                "frozen pending SOC review and manual reset. " + self.freeze_reason,
                warnings,
            ))

        risk_flags: List[RiskFlag] = []

        # 2. Per-account rolling velocity.
        acc_1h = self.get_rolling_velocity_usd(request.account_id, 1.0, now)
        acc_24h = self.get_rolling_velocity_usd(request.account_id, 24.0, now)
        if (acc_1h + request.amount_usd) > self.account_hourly_limit_usd:
            risk_flags.append(RiskFlag.EXCEEDS_HOURLY_LIMIT)
        if (acc_24h + request.amount_usd) > self.account_daily_limit_usd:
            risk_flags.append(RiskFlag.EXCEEDS_DAILY_LIMIT)

        # 3. Size anomaly against the account's own baseline.
        z_score = self._evaluate_anomaly(request, profile, risk_flags, warnings)

        # 4. Destination address record: binding first, then age.
        self._evaluate_destination(request, whitelist_record, now, risk_flags)

        if risk_flags:
            rationale = ("Withdrawal flagged for security review. Risk Flags: "
                         f"{[f.value for f in risk_flags]}.")
            logger.warning("TIMELOCK HOLD [%s]: %s", request.request_id, rationale)
            self._held_requests[request.request_id] = request
            return self._finalise(WithdrawalDecision(
                request_id=request.request_id,
                account_id=request.account_id,
                status=WithdrawalStatus.TIMELOCK_HOLD,
                decision_timestamp=now,
                risk_flags=risk_flags,
                rationale=rationale,
                required_hold_hours=self.timelock_hold_hours,
                anomaly_zscore=z_score,
                warnings=warnings,
            ))

        # Approved requests consume capacity at the evaluation clock, not at the
        # client-asserted request time.
        self.withdrawal_ledger.append(
            WithdrawalRequest(
                request_id=request.request_id,
                account_id=request.account_id,
                asset=request.asset,
                amount_crypto=request.amount_crypto,
                amount_usd=request.amount_usd,
                destination_address=request.destination_address,
                timestamp=now,
            )
        )
        logger.info("WITHDRAWAL APPROVED [%s]: $%.2f to %s...",
                    request.request_id, request.amount_usd, request.destination_address[:8])
        return self._finalise(WithdrawalDecision(
            request_id=request.request_id,
            account_id=request.account_id,
            status=WithdrawalStatus.APPROVED,
            decision_timestamp=now,
            risk_flags=[],
            rationale="Within velocity limits, size baseline, and address cooling period.",
            required_hold_hours=0.0,
            anomaly_zscore=z_score,
            warnings=warnings,
        ))

    def _evaluate_anomaly(
        self,
        request: WithdrawalRequest,
        profile: AccountHistoricalProfile,
        risk_flags: List[RiskFlag],
        warnings: List[str],
    ) -> Optional[float]:
        """Score request size against the account baseline. Returns the Z-score,
        or ``None`` when the check could not run — in which case the request is
        flagged, not waved through."""
        if profile.account_id != request.account_id:
            raise VelocityEngineError(
                f"Profile is for account {profile.account_id!r} but the request is for "
                f"{request.account_id!r}; refusing to score against another account's baseline.")

        count = int(profile.historical_count)
        mean = float(profile.mean_withdrawal_usd)
        sigma = float(profile.std_dev_usd)

        if not math.isfinite(mean) or not math.isfinite(sigma):
            raise VelocityEngineError(
                f"Profile for {profile.account_id!r} carries non-finite mean/std "
                f"({mean!r}/{sigma!r}); a NaN silently answers 'not anomalous'.")
        if sigma < 0:
            raise VelocityEngineError(
                f"Profile std_dev_usd must be non-negative, got {sigma!r}.")
        if count < 0:
            raise VelocityEngineError(
                f"Profile historical_count must be non-negative, got {count!r}.")

        if count < self.min_profile_observations:
            message = (f"Size-anomaly check did not run: {count} historical withdrawals is "
                       f"below the {self.min_profile_observations} needed for a stable sigma.")
            warnings.append(message)
            logger.warning("INSUFFICIENT PROFILE [%s]: %s", request.account_id, message)
            risk_flags.append(RiskFlag.INSUFFICIENT_PROFILE_HISTORY)
            return None

        if sigma == 0.0:
            message = ("Size-anomaly check did not run: the account's historical withdrawals "
                       "have zero variance, so a Z-score is undefined.")
            warnings.append(message)
            logger.warning("DEGENERATE PROFILE [%s]: %s", request.account_id, message)
            risk_flags.append(RiskFlag.INSUFFICIENT_PROFILE_HISTORY)
            return None

        z_score = (request.amount_usd - mean) / sigma
        if z_score >= self.anomaly_zscore_threshold:
            logger.warning("SIZE ANOMALY [%s]: Z=%.2f >= %.2f",
                           request.account_id, z_score, self.anomaly_zscore_threshold)
            risk_flags.append(RiskFlag.ANOMALY_SIZE_ZSCORE)
        return z_score

    def _evaluate_destination(
        self,
        request: WithdrawalRequest,
        whitelist_record: Optional[AddressWhitelistRecord],
        now: datetime.datetime,
        risk_flags: List[RiskFlag],
    ) -> None:
        """Verify the allowlist record binds to this request, then check its age.

        A record is evidence about *one* (account, address) pair. Measuring the
        age of a record fetched for some other address approves a withdrawal to
        an address nobody ever allowlisted — the single worst failure this gate
        can have.
        """
        if whitelist_record is None or not whitelist_record.is_whitelisted:
            risk_flags.append(RiskFlag.NEW_ADDRESS_HOLD)
            return

        binds = (whitelist_record.account_id == request.account_id
                 and whitelist_record.address == request.destination_address)
        if not binds:
            logger.error(
                "WHITELIST RECORD MISMATCH [%s]: record is (%s, %s) but request is (%s, %s). "
                "Discarding the record as evidence and holding.",
                request.request_id, whitelist_record.account_id, whitelist_record.address,
                request.account_id, request.destination_address)
            risk_flags.append(RiskFlag.WHITELIST_RECORD_MISMATCH)
            risk_flags.append(RiskFlag.NEW_ADDRESS_HOLD)
            return

        added = _as_utc(whitelist_record.added_timestamp, "whitelist_record.added_timestamp")
        age_hours = (now - added).total_seconds() / 3600.0
        if age_hours < self.address_whitelist_cooling_hours:
            logger.info("ADDRESS IN COOLING [%s...]: age=%.1fh < %.1fh",
                        request.destination_address[:8], age_hours,
                        self.address_whitelist_cooling_hours)
            risk_flags.append(RiskFlag.NEW_ADDRESS_HOLD)

    def _freeze_decision(
        self,
        request: WithdrawalRequest,
        now: datetime.datetime,
        rationale: str,
        warnings: List[str],
    ) -> WithdrawalDecision:
        return WithdrawalDecision(
            request_id=request.request_id,
            account_id=request.account_id,
            status=WithdrawalStatus.REJECTED_FREEZE,
            decision_timestamp=now,
            risk_flags=[RiskFlag.HOT_WALLET_LIMIT_EXCEEDED],
            rationale=rationale,
            required_hold_hours=0.0,
            warnings=warnings,
        )

    def _finalise(self, decision: WithdrawalDecision) -> WithdrawalDecision:
        self._decisions[decision.request_id] = decision
        return decision

    # ------------------------------------------------------- operator actions

    def reset_hot_wallet_freeze(self, authorized_by: str) -> None:
        """Manually re-arm the hot wallet breaker after SOC review.

        Deliberately requires a named authoriser and is the *only* way out of a
        freeze: the workflow this skill documents ends in a manual multi-sig
        reset, and an operator action that leaves no attributable record cannot
        support that.
        """
        if not isinstance(authorized_by, str) or not authorized_by.strip():
            raise VelocityEngineError("reset_hot_wallet_freeze requires a named authoriser.")
        if not self.hot_wallet_frozen:
            logger.warning("FREEZE RESET NO-OP: hot wallet was not frozen.")
            return
        logger.warning("HOT WALLET FREEZE RESET by %s. Prior reason: %s",
                       authorized_by, self.freeze_reason)
        self.hot_wallet_frozen = False
        self.freeze_reason = ""
        self.frozen_at = None

    def release_held_withdrawal(
        self,
        request_id: str,
        authorized_by: str,
        release_timestamp: Optional[datetime.datetime] = None,
    ) -> WithdrawalRequest:
        """Record that a held withdrawal was manually approved and disbursed.

        Held withdrawals are not in the velocity ledger, because they did not
        move funds. When review releases one, it *does* move funds — and without
        this call it consumes no capacity, so an attacker who can get holds
        released has an unmetered channel straight through every rolling limit.
        """
        if not isinstance(authorized_by, str) or not authorized_by.strip():
            raise VelocityEngineError("release_held_withdrawal requires a named authoriser.")
        held = self._held_requests.pop(request_id, None)
        if held is None:
            raise VelocityEngineError(
                f"No held withdrawal {request_id!r} awaiting release.")

        released_at = _as_utc(
            release_timestamp if release_timestamp is not None
            else datetime.datetime.now(UTC),
            "release_timestamp",
        )
        self._prune_ledger(released_at)
        self.withdrawal_ledger.append(
            WithdrawalRequest(
                request_id=held.request_id,
                account_id=held.account_id,
                asset=held.asset,
                amount_crypto=held.amount_crypto,
                amount_usd=held.amount_usd,
                destination_address=held.destination_address,
                timestamp=released_at,
            )
        )
        logger.warning("HELD WITHDRAWAL RELEASED [%s] by %s: $%.2f now counts toward velocity.",
                       request_id, authorized_by, held.amount_usd)
        return held

    def cancel_held_withdrawal(self, request_id: str) -> None:
        """Drop a held withdrawal that review rejected, so it can never be released."""
        if self._held_requests.pop(request_id, None) is None:
            raise VelocityEngineError(
                f"No held withdrawal {request_id!r} awaiting review.")
        logger.info("HELD WITHDRAWAL CANCELLED [%s].", request_id)
