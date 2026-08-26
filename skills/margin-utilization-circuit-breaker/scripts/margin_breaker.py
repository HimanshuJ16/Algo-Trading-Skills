"""
margin-utilization-circuit-breaker:
A latching, house-policy circuit breaker on **margin utilization** that halts new
exposure-increasing orders long before the broker's own liquidation point, and
stays halted until a human re-arms it.

Scope, and how this differs from its neighbours
-----------------------------------------------
* ``broker-account-margin-call-handling`` grades a broker account snapshot,
  cross-checks the broker's authoritative ``excess_liquidity`` cushion, and plans
  liquidity-aware de-leveraging once a call is close. It is stateless and
  broker-facing.
* ``leverage-limit-enforcement-across-instruments`` caps *exposure* against
  equity. It models no margin requirement at all.
* **This module** is the risk-management analogue of
  ``kill-switch-and-drawdown-circuit-breakers``: a strategy-independent breaker
  whose trip metric is a margin *budget* you choose, which **latches** on trip,
  refuses to re-arm while still stressed, and records who re-armed it and why.

A stateless "is the ratio above 0.8 right now" check adds nothing over the
broker-facing skill above. The latch, the risk-reducing carve-out and the audited
re-arm are the reason this module exists.

Two definitions decide whether the number means anything
--------------------------------------------------------
**Which requirement is in the numerator.** ``used_margin`` may be an *initial*
margin requirement or a *maintenance* requirement, and they are roughly a factor
of two apart for US margin equities: Reg T (12 CFR 220.12(a)) sets initial margin
at 50% of current market value, while FINRA Rule 4210(c) sets maintenance at 25%
for long positions. A book at 0.80 on the initial basis can sit near 0.40 on the
maintenance basis. Declare the basis at construction via ``basis=``; it is
carried into every message and it changes what a threshold means. Liquidation is
driven by the *maintenance* basis: Interactive Brokers liquidates when Excess
Liquidity (Equity with Loan Value - Maintenance Margin, for the securities
segment) goes negative, i.e. when maintenance utilization reaches 1.0.

**Which direction the ratio runs.** This module's utilization is
``used_margin / account_equity`` - higher is worse. MetaTrader's *margin level*
is ``equity / margin * 100`` - **the reciprocal**, where higher is better and
brokers stop out at low values such as 50% or 20%. Feeding an MT4/MT5
``ACCOUNT_MARGIN_LEVEL`` into ``used_margin`` inverts the control: a genuinely
distressed account reports a tiny utilization and every order is approved.
Normalise at the adapter boundary, not here.

Thresholds here are **house policy**, not regulation. No rule surveyed in
``references/standards.md`` prescribes a margin utilization number for a trading
firm. MiFID II RTS 6 Art. 15(4) requires an investment firm to *set* market and
credit risk limits based on its capital base and risk tolerance, and Art. 15(5)
requires orders that would compromise those thresholds to be blocked or cancelled
automatically - it fixes no value. SEC Rule 15c3-5(c)(1)(i) binds broker-dealers
with market access, not their customers.

Out of scope: this module does not compute margin requirements (no SPAN, no
portfolio margin, no cross-margin offsets), does not place or cancel orders, and
does not plan liquidations. See ``references/standards.md`` for sources.
"""
import logging
import math
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MarginBasis",
    "MarginDataError",
    "MarginOrderCheck",
    "MarginState",
    "MarginStatus",
    "MarginUtilizationBreaker",
    "ReArmAttempt",
]


class MarginDataError(ValueError):
    """
    Raised when margin inputs or configuration are unusable.

    Kept distinct from an ordinary rejection so a caller can tell "the breaker
    vetoed this order" from "the data feeding the breaker is broken". Both must
    stop the order; only the second means escalate to whoever owns the feed.

    Named to match ``broker-account-margin-call-handling``, which raises the same
    concept, so the two cross-linked modules read the same way.
    """


class MarginBasis(str, Enum):
    """Which margin requirement ``used_margin`` carries."""

    #: The requirement the broker liquidates against. Utilization reaching 1.0
    #: means equity no longer covers the requirement.
    MAINTENANCE = "MAINTENANCE"
    #: The requirement to *open* a position (Reg T: 50% of market value).
    #: Roughly twice maintenance for US margin equities, so the same numeric
    #: threshold is a materially looser budget on this basis.
    INITIAL = "INITIAL"


class MarginStatus(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HARD_STOP = "HARD_STOP"


@dataclass(frozen=True)
class MarginState:
    """
    A graded margin snapshot.

    ``available_margin`` is clamped at zero; read ``margin_deficit`` for the size
    of any shortfall, which is the number an operator actually needs.
    """

    used_margin: float
    available_margin: float
    account_equity: float
    utilization_pct: float
    status: MarginStatus
    message: str
    basis: MarginBasis = MarginBasis.MAINTENANCE
    #: ``max(used_margin - account_equity, 0)``. Non-zero means equity no longer
    #: covers the requirement on the declared basis.
    margin_deficit: float = 0.0
    #: True while the breaker's latch is set. A latched breaker reports
    #: ``HARD_STOP`` even once utilization has fallen back below the threshold.
    latched: bool = False
    #: Age of the snapshot in seconds, when a timestamp was supplied.
    data_age_seconds: Optional[float] = None


@dataclass(frozen=True)
class MarginOrderCheck:
    approved: bool
    margin_state: Optional[MarginState]
    rejection_reason: Optional[str] = None
    #: True when the order was approved because it strictly *releases* margin
    #: while the breaker was halted. Such orders are the de-risking the breaker
    #: is demanding; vetoing them would make the breaker veto its own remedy.
    risk_reducing: bool = False
    #: True when the veto came from unusable or stale input rather than from a
    #: breached threshold. Escalate to whoever owns the margin feed.
    is_data_error: bool = False


@dataclass(frozen=True)
class ReArmAttempt:
    """One recorded attempt to clear the latch, granted or refused."""

    timestamp: datetime
    operator: str
    reason: str
    granted: bool
    utilization_pct: Optional[float]
    detail: str


def _require_finite(name: str, value: float, *, allow_negative: bool = True) -> float:
    """
    Validate a numeric margin input.

    NaN is the specific hazard. Every comparison against NaN is False, so a NaN
    utilization falls past ``>= hard_stop`` and ``>= warning`` straight into the
    healthy branch: the breaker reports NORMAL and approves every order while
    checking nothing. Unusable input must raise, not default to healthy.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MarginDataError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise MarginDataError(
            f"{name} must be finite, got {value!r}. Refusing to evaluate margin "
            f"utilization on an unusable feed value; treat this as a data outage, "
            f"not a healthy account."
        )
    if not allow_negative and value < 0:
        raise MarginDataError(f"{name} must be >= 0, got {value!r}")
    return float(value)


def _validate_threshold(name: str, value: float) -> float:
    """
    Reject a threshold that cannot do its job.

    The dominant real-world configuration error is scale: passing ``80`` for
    "80%" into a parameter that expects a fraction. Nothing raises, the
    comparison ``utilization >= 80`` is never true, and the breaker is disabled
    for the life of the process with no outward signal - the first evidence is
    the forced liquidation it was installed to prevent.
    """
    value = _require_finite(name, value)
    if not 0.0 < value <= 1.0:
        raise MarginDataError(
            f"{name} must be a fraction in (0, 1], got {value!r}. "
            f"Pass 0.80 for 80%, not 80."
        )
    return value


def _coerce_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise MarginDataError(f"{name} must be a datetime, got {value!r}")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise MarginDataError(
            f"{name} must be timezone-aware. A naive timestamp is silently "
            f"interpreted as local time, which makes the staleness check wrong "
            f"by the UTC offset - and wrong in the tolerant direction."
        )
    return value.astimezone(timezone.utc)


class MarginUtilizationBreaker:
    """
    Latching circuit breaker on margin utilization.

    ``utilization = used_margin / account_equity`` on the declared
    :class:`MarginBasis`. Crossing ``hard_stop_threshold`` sets a latch that
    blocks every exposure-increasing order until an operator calls
    :meth:`re_arm`, which is itself refused while utilization is still above
    ``re_arm_threshold``.

    Thread-safe: the latch is guarded by a re-entrant lock, so a strategy loop, a
    margin poller and an operator endpoint may call concurrently without two
    callers both reading "not latched" and both placing an order.
    """

    def __init__(
        self,
        warning_threshold: float = 0.60,
        hard_stop_threshold: float = 0.80,
        *,
        basis: MarginBasis = MarginBasis.MAINTENANCE,
        re_arm_threshold: Optional[float] = None,
        max_data_age_seconds: Optional[float] = None,
        latching: bool = True,
    ) -> None:
        """
        Args:
            warning_threshold: Fraction at which to alert and stop adding size.
            hard_stop_threshold: Fraction at which to latch and block new
                exposure. Must exceed ``warning_threshold``.
            basis: Which requirement ``used_margin`` carries. On ``MAINTENANCE``
                the thresholds must sit strictly below 1.0, because utilization
                of 1.0 on that basis is the point at which the broker's own
                cushion is exhausted - a breaker that trips there has not
                prevented anything.
            re_arm_threshold: Utilization at or below which :meth:`re_arm` will
                clear the latch. Defaults to ``warning_threshold``. Re-arming at
                the trip level just re-trips on the next evaluation.
            max_data_age_seconds: Reject snapshots older than this. ``None``
                disables the check, which is a fail-open default - set it.
            latching: Set ``False`` for stateless snapshot grading. The latch is
                the point of this module; disabling it leaves a plain threshold
                check that ``broker-account-margin-call-handling`` already does
                more thoroughly.
        """
        if not isinstance(basis, MarginBasis):
            raise MarginDataError(f"basis must be a MarginBasis, got {basis!r}")

        self.warning_threshold = _validate_threshold("warning_threshold", warning_threshold)
        self.hard_stop_threshold = _validate_threshold(
            "hard_stop_threshold", hard_stop_threshold
        )
        if self.warning_threshold >= self.hard_stop_threshold:
            raise MarginDataError(
                f"warning_threshold ({self.warning_threshold}) must be below "
                f"hard_stop_threshold ({self.hard_stop_threshold}); otherwise the "
                f"warning tier is unreachable and the account jumps straight to halt."
            )
        if basis is MarginBasis.MAINTENANCE and self.hard_stop_threshold >= 1.0:
            raise MarginDataError(
                "hard_stop_threshold must be < 1.0 on the MAINTENANCE basis: at 1.0 "
                "equity no longer covers the maintenance requirement and the broker "
                "may already be liquidating. Leave a cushion."
            )

        if re_arm_threshold is None:
            self.re_arm_threshold = self.warning_threshold
        else:
            self.re_arm_threshold = _validate_threshold("re_arm_threshold", re_arm_threshold)
            if self.re_arm_threshold >= self.hard_stop_threshold:
                raise MarginDataError(
                    f"re_arm_threshold ({self.re_arm_threshold}) must be strictly below "
                    f"hard_stop_threshold ({self.hard_stop_threshold}). At or above it, an "
                    f"operator can re-arm at a utilization that trips again on the very "
                    f"next evaluation, and the one-evaluation reprieve reads as a fix."
                )

        if max_data_age_seconds is not None:
            max_data_age_seconds = _require_finite(
                "max_data_age_seconds", max_data_age_seconds, allow_negative=False
            )
            if max_data_age_seconds <= 0:
                raise MarginDataError("max_data_age_seconds must be > 0 when set.")
        else:
            logger.warning(
                "MarginUtilizationBreaker constructed with max_data_age_seconds=None: "
                "stale margin snapshots will be graded as if current. Set it."
            )
        self.max_data_age_seconds = max_data_age_seconds

        self.basis = basis
        self.latching = latching

        self._lock = threading.RLock()
        self._latched = False
        self._last_status: Optional[MarginStatus] = None
        self._re_arm_log: List[ReArmAttempt] = []

    # ---------------------------------------------------------------- state --

    @property
    def is_latched(self) -> bool:
        """True while the hard stop is held open awaiting an operator re-arm."""
        with self._lock:
            return self._latched

    @property
    def re_arm_log(self) -> Tuple[ReArmAttempt, ...]:
        """Every re-arm attempt, granted and refused, oldest first."""
        with self._lock:
            return tuple(self._re_arm_log)

    # ------------------------------------------------------------ assessment --

    def _assess(
        self,
        used_margin: float,
        account_equity: float,
        *,
        latched: bool,
        data_age_seconds: Optional[float],
        label: str,
    ) -> MarginState:
        """
        Grade a margin snapshot. Pure: touches no instance state and logs nothing,
        so a *projected* order state cannot write "all new entries BLOCKED" into
        the audit log for an account that is in fact healthy.
        """
        used_margin = _require_finite(f"{label}used_margin", used_margin, allow_negative=False)
        account_equity = _require_finite(f"{label}account_equity", account_equity)

        if account_equity <= 0:
            # Utilization is undefined against non-positive equity. Reporting a
            # finite 1.0 here would understate a debit balance as "exactly fully
            # used", and would let a caller comparing against 1.0 conclude the
            # account had merely touched the line.
            return MarginState(
                used_margin=used_margin,
                available_margin=0.0,
                account_equity=account_equity,
                utilization_pct=math.inf,
                status=MarginStatus.HARD_STOP,
                message=(
                    f"CRITICAL: account equity {account_equity:,.2f} <= 0. Margin "
                    f"utilization is undefined; all new entries BLOCKED and the "
                    f"account requires human escalation."
                ),
                basis=self.basis,
                # Cover-the-requirement shortfall, not the requirement itself:
                # against a debit balance the capital needed exceeds used_margin
                # by the size of the debit.
                margin_deficit=used_margin - account_equity,
                latched=latched,
                data_age_seconds=data_age_seconds,
            )

        utilization = used_margin / account_equity
        available = max(account_equity - used_margin, 0.0)
        deficit = max(used_margin - account_equity, 0.0)

        if latched:
            status = MarginStatus.HARD_STOP
            msg = (
                f"MARGIN HARD STOP (LATCHED): {self.basis.value} utilization "
                f"{utilization:.1%}. Breaker is held open pending operator re-arm; "
                f"only margin-releasing orders are permitted."
            )
        elif utilization >= self.hard_stop_threshold:
            status = MarginStatus.HARD_STOP
            msg = (
                f"MARGIN HARD STOP: {self.basis.value} utilization {utilization:.1%} >= "
                f"{self.hard_stop_threshold:.1%}. All exposure-increasing entries BLOCKED."
            )
        elif utilization >= self.warning_threshold:
            status = MarginStatus.WARNING
            msg = (
                f"MARGIN WARNING: {self.basis.value} utilization {utilization:.1%} >= "
                f"{self.warning_threshold:.1%}. Consider reducing positions."
            )
        else:
            status = MarginStatus.NORMAL
            msg = (
                f"{self.basis.value} margin utilization {utilization:.1%} "
                f"- within normal range."
            )

        return MarginState(
            used_margin=used_margin,
            available_margin=available,
            account_equity=account_equity,
            utilization_pct=utilization,
            status=status,
            message=msg,
            basis=self.basis,
            margin_deficit=deficit,
            latched=latched,
            data_age_seconds=data_age_seconds,
        )

    def _snapshot_age(
        self, as_of: Optional[datetime], now: Optional[datetime]
    ) -> Optional[float]:
        """
        Return snapshot age in seconds, raising if it is unusable or too old.

        A margin feed that has silently stopped updating is indistinguishable
        from a calm market, and it is exactly during a fast move that a feed
        stalls. Fail closed.
        """
        if as_of is None:
            if self.max_data_age_seconds is not None:
                raise MarginDataError(
                    "max_data_age_seconds is configured but no 'as_of' timestamp was "
                    "supplied; freshness cannot be verified, so the snapshot is refused."
                )
            return None

        as_of_utc = _coerce_utc("as_of", as_of)
        now_utc = _coerce_utc("now", now) if now is not None else datetime.now(timezone.utc)
        age = (now_utc - as_of_utc).total_seconds()
        if age < 0:
            raise MarginDataError(
                f"'as_of' is {-age:.3f}s in the future relative to 'now'. Clock skew in "
                f"this direction makes every snapshot look fresh forever; refusing it."
            )
        if self.max_data_age_seconds is not None and age > self.max_data_age_seconds:
            raise MarginDataError(
                f"margin snapshot is {age:.3f}s old, exceeding max_data_age_seconds="
                f"{self.max_data_age_seconds}. Treat as a data outage, not a healthy "
                f"account."
            )
        return age

    def evaluate_margin(
        self,
        used_margin: float,
        account_equity: float,
        *,
        as_of: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> MarginState:
        """
        Grade the *account's current* margin state and update the latch.

        Raises:
            MarginDataError: on non-finite, negative, stale or untimestamped
                input. Callers on the monitoring path must treat the exception as
                a halt condition, not as a passed check.
        """
        age = self._snapshot_age(as_of, now)

        with self._lock:
            state = self._assess(
                used_margin,
                account_equity,
                latched=self._latched,
                data_age_seconds=age,
                label="",
            )
            if self.latching and state.status is MarginStatus.HARD_STOP and not self._latched:
                # Keep the *trip* message rather than re-grading into the
                # "(LATCHED)" wording: the audit log should show the crossing
                # that caused the halt, not the steady state that followed it.
                self._latched = True
                state = replace(state, latched=True)
            transitioned = state.status is not self._last_status
            self._last_status = state.status

        # Log transitions only. Re-emitting CRITICAL on every poll of a halted
        # account buries the transition that mattered under thousands of lines.
        if transitioned:
            if state.status is MarginStatus.HARD_STOP:
                logger.critical(state.message)
            elif state.status is MarginStatus.WARNING:
                logger.warning(state.message)
            else:
                logger.info(state.message)
        return state

    # --------------------------------------------------------------- gating --

    def check_order(
        self,
        used_margin: float,
        account_equity: float,
        additional_margin_required: float = 0.0,
        *,
        as_of: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> MarginOrderCheck:
        """
        Pre-trade gate.

        ``additional_margin_required`` is the *change* in the margin requirement
        this order causes: positive for an order that consumes margin, negative
        for one that releases it (a closing or reducing order).

        A closing order is approved even while the breaker is halted, provided it
        strictly reduces the requirement. Without that carve-out the breaker
        vetoes the de-risking it is demanding, and the positions it exists to
        shrink stay open. The carve-out is verified arithmetically from the
        projected requirement - it is not a caller-supplied flag to be trusted.

        Never raises: the pre-trade path fails closed by returning a veto with
        ``is_data_error=True``, so a broken feed cannot become an approval.
        """
        try:
            age = self._snapshot_age(as_of, now)
            used_margin = _require_finite("used_margin", used_margin, allow_negative=False)
            account_equity = _require_finite("account_equity", account_equity)
            delta = _require_finite("additional_margin_required", additional_margin_required)

            projected_used = used_margin + delta
            if projected_used < 0:
                raise MarginDataError(
                    f"additional_margin_required ({delta}) releases more margin than the "
                    f"{used_margin} currently used, giving a negative projected "
                    f"requirement. Reject rather than approve on an impossible projection."
                )
        except MarginDataError as exc:
            logger.error("Order vetoed on unusable margin input: %s", exc)
            return MarginOrderCheck(
                approved=False,
                margin_state=None,
                rejection_reason=f"MARGIN DATA ERROR: {exc}",
                is_data_error=True,
            )

        current = self.evaluate_margin(used_margin, account_equity, as_of=as_of, now=now)
        projected = self._assess(
            projected_used,
            account_equity,
            latched=current.latched,
            data_age_seconds=age,
            label="projected ",
        )

        blocked = (
            current.status is MarginStatus.HARD_STOP
            or projected.status is MarginStatus.HARD_STOP
        )
        if not blocked:
            return MarginOrderCheck(approved=True, margin_state=projected)

        # Strictly-reducing carve-out. Equality is not reduction: a reversal or a
        # margin-neutral swap leaves the requirement where it was and stays blocked.
        if delta < 0 and projected_used < used_margin:
            logger.info(
                "Margin-releasing order approved while halted: requirement %.2f -> %.2f",
                used_margin,
                projected_used,
            )
            return MarginOrderCheck(
                approved=True, margin_state=projected, risk_reducing=True
            )

        reason = (
            projected.message
            if projected.status is MarginStatus.HARD_STOP
            else current.message
        )
        return MarginOrderCheck(
            approved=False, margin_state=projected, rejection_reason=reason
        )

    # ------------------------------------------------------------- re-arming --

    def re_arm(
        self,
        operator: str,
        reason: str,
        *,
        used_margin: float,
        account_equity: float,
        as_of: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Clear the latch after human review. Returns ``True`` if cleared.

        Refused - and recorded as refused - when the operator identity or reason
        is blank, when the margin input is unusable, or when utilization is still
        above ``re_arm_threshold``. That last refusal is the important one:
        re-arming at the trip level simply re-trips on the next poll, and an
        operator who sees trading resume for a single evaluation reasonably
        concludes the problem is solved.

        MiFID II RTS 6 Art. 15(6) requires that overrides of a firm's own
        pre-trade blocks be temporary, exceptional, verified by the risk
        management function and authorised by a designated individual - which is
        why this returns a checked boolean and appends to :attr:`re_arm_log`
        rather than logging a string and continuing.
        """
        stamp = _coerce_utc("now", now) if now is not None else datetime.now(timezone.utc)
        operator_clean = (operator or "").strip()
        reason_clean = (reason or "").strip()

        def _record(granted: bool, utilization: Optional[float], detail: str) -> bool:
            attempt = ReArmAttempt(
                timestamp=stamp,
                operator=operator_clean,
                reason=reason_clean,
                granted=granted,
                utilization_pct=utilization,
                detail=detail,
            )
            with self._lock:
                self._re_arm_log.append(attempt)
            if granted:
                logger.critical(
                    "MARGIN BREAKER RE-ARMED by %r: %s (utilization %s)",
                    operator_clean,
                    reason_clean,
                    "n/a" if utilization is None else f"{utilization:.1%}",
                )
            else:
                logger.error("Margin breaker re-arm REFUSED: %s", detail)
            return granted

        if not operator_clean:
            return _record(False, None, "blank operator identity")
        if not reason_clean:
            return _record(False, None, "blank reason")

        try:
            age = self._snapshot_age(as_of, now)
            state = self._assess(
                used_margin,
                account_equity,
                latched=False,
                data_age_seconds=age,
                label="re-arm ",
            )
        except MarginDataError as exc:
            return _record(False, None, f"unusable margin input: {exc}")

        if state.utilization_pct > self.re_arm_threshold:
            return _record(
                False,
                state.utilization_pct,
                f"utilization {state.utilization_pct:.1%} still above re_arm_threshold "
                f"{self.re_arm_threshold:.1%}; reduce exposure before re-arming",
            )

        with self._lock:
            self._latched = False
            self._last_status = None
        return _record(True, state.utilization_pct, "latch cleared")
