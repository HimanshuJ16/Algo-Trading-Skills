"""Independent capital-preservation kill switch for algorithmic trading systems.

The engine sits between strategy logic and the execution gateway and enforces
hard, strategy-independent limits:

* peak-to-trough drawdown of session P&L (and, optionally, an absolute session
  loss limit measured from flat),
* order submission rate over a rolling window (runaway-algorithm protection),
* consecutive broker/venue errors (connectivity degradation),
* staleness of the mark-to-market feed the drawdown control depends on.

Two distinct non-trading states are modelled and they are not interchangeable:

``EngineState.DEGRADED_WARNING``
    Recoverable. New orders are blocked because a risk *input* cannot currently
    be trusted (for example the P&L feed is stale or produced a non-finite
    value). The engine clears this state by itself as soon as the input
    recovers.

``EngineState.HALTED``
    Terminal. A risk *limit* was breached. The engine never auto-recovers;
    :meth:`CapitalPreservationEngine.manual_reset` with a valid authorisation
    token is the only way out. This mirrors the "hard stop" requirement in
    ``references/standards.md``.

Scope limits - read before relying on this module:

* It blocks *new* order submissions. It does not cancel resting orders and does
  not flatten open positions, so on its own it does **not** satisfy MiFID II
  RTS 6 Article 12 kill functionality. Wire the ``on_halt`` callback to
  whatever performs the cancel-all / flatten.
* It is thread-safe within one process. It is not process-safe: run one engine
  per order-routing process, or put it behind a single gateway process.
"""

from __future__ import annotations

import hmac
import logging
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Width of the rolling order-rate window. ``max_orders_per_minute`` is defined
#: against this window, so it is a module constant rather than a limit field.
ORDER_RATE_WINDOW_SECONDS: float = 60.0

#: Environment variable holding the manual-reset authorisation token. There is
#: deliberately no default: an unset variable makes :meth:`manual_reset` fail
#: closed rather than fall back to a token published in this repository.
RESET_TOKEN_ENV_VAR: str = "CAPITAL_PRESERVATION_RESET_TOKEN"


class EngineState(Enum):
    """Operating state of the engine. Values are stable and persisted."""

    ACTIVE = 1
    DEGRADED_WARNING = 2
    HALTED = 3


@dataclass(frozen=True)
class PreservationLimits:
    """Hard limits enforced by the engine.

    Every value here is a placeholder. None of these numbers is set by a
    regulator, exchange or broker; calibrate them against your own desk's risk
    tolerance and measured operating rates (see ``references/standards.md``).

    Attributes:
        max_daily_drawdown_usd: Peak-to-trough give-back of session P&L, in
            account currency, that trips a halt. Measured against a high-water
            mark seeded at 0.0 (flat at session start), so it also catches a
            straight-line loss from flat.
        max_orders_per_minute: Order submissions permitted in any
            :data:`ORDER_RATE_WINDOW_SECONDS` window before a halt trips.
        max_consecutive_errors: Consecutive broker/venue errors, without an
            intervening success, that trip a halt.
        max_daily_loss_usd: Optional absolute session loss limit measured from
            flat rather than from the high-water mark. ``None`` disables it. A
            drawdown limit alone lets an unbounded loss be dressed up as a
            small give-back from a large intraday peak.
        max_pnl_staleness_seconds: Optional age above which the last
            :meth:`CapitalPreservationEngine.update_pnl` is considered stale
            and new orders are blocked (``DEGRADED_WARNING``, recoverable).
            ``None`` disables the check - only do that if some other layer
            guarantees P&L freshness.
    """

    max_daily_drawdown_usd: float = 50000.0
    max_orders_per_minute: int = 100
    max_consecutive_errors: int = 5
    max_daily_loss_usd: Optional[float] = None
    max_pnl_staleness_seconds: Optional[float] = None

    def __post_init__(self) -> None:
        self._require_positive_finite("max_daily_drawdown_usd", self.max_daily_drawdown_usd)
        self._require_positive_finite("max_daily_loss_usd", self.max_daily_loss_usd, optional=True)
        self._require_positive_finite(
            "max_pnl_staleness_seconds", self.max_pnl_staleness_seconds, optional=True
        )

        for name in ("max_orders_per_minute", "max_consecutive_errors"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 1:
                raise ValueError(f"{name} must be >= 1, got {value}")

    @staticmethod
    def _require_positive_finite(name: str, value: Any, optional: bool = False) -> None:
        if value is None:
            if optional:
                return
            raise ValueError(f"{name} must not be None")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number, got {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise ValueError(f"{name} must be finite, got {value!r}")
        if float(value) <= 0.0:
            raise ValueError(f"{name} must be > 0, got {value!r}")


@dataclass(frozen=True)
class HaltRecord:
    """One immutable audit entry: a halt, or the reset that cleared it."""

    event: str
    reason: str
    occurred_at_utc: str
    operator: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "reason": self.reason,
            "occurred_at_utc": self.occurred_at_utc,
            "operator": self.operator,
        }


class ResetAuthorizer:
    """Validates manual-reset tokens against a secret supplied at runtime.

    The secret is read from :data:`RESET_TOKEN_ENV_VAR` (or injected directly in
    tests). If no secret is configured the authorizer denies every reset - a
    kill switch that can be cleared with a value hard-coded in a public
    repository is not a kill switch.
    """

    def __init__(
        self,
        expected_token: Optional[str] = None,
        env_var: str = RESET_TOKEN_ENV_VAR,
    ) -> None:
        self._expected_token = (
            expected_token if expected_token is not None else os.environ.get(env_var)
        )
        self._env_var = env_var

    @property
    def is_configured(self) -> bool:
        return bool(self._expected_token)

    def authorize(self, token: str) -> None:
        """Raise :class:`PermissionError` unless ``token`` matches the secret."""
        if not self._expected_token:
            raise PermissionError(
                f"Manual reset is not configured: set the {self._env_var} environment "
                "variable (or inject a ResetAuthorizer) before deploying."
            )
        if not isinstance(token, str):
            raise PermissionError("Invalid authorization")
        # Constant-time comparison: the reset token is a secret, and a timing
        # oracle on a kill-switch override is cheap to close.
        if not hmac.compare_digest(token, self._expected_token):
            raise PermissionError("Invalid authorization")


@dataclass
class _RateWindow:
    """Rolling order-submission window keyed on a monotonic clock."""

    window_seconds: float
    timestamps: Deque[float] = field(default_factory=deque)

    def prune(self, now: float) -> None:
        """Drop timestamps outside the half-open window ``(now - window, now]``.

        The window is half-open on purpose. With a closed window an order
        exactly ``window_seconds`` old still counts, so evenly spaced traffic at
        exactly the configured rate trips the halt and the effective limit is
        one below the configured one.
        """
        cutoff = now - self.window_seconds
        while self.timestamps and self.timestamps[0] <= cutoff:
            self.timestamps.popleft()

    def __len__(self) -> int:
        return len(self.timestamps)


class CapitalPreservationEngine:
    """Independent kill-switch middleware.

    Args:
        limits: Hard limits to enforce.
        authorizer: Validates :meth:`manual_reset` tokens. Defaults to reading
            :data:`RESET_TOKEN_ENV_VAR` from the environment.
        clock: Monotonic time source, in seconds. Injectable so rate-limit and
            staleness behaviour can be tested deterministically. Must be
            monotonic - a wall clock lets an NTP step forward silently empty
            the order-rate window and a step backward freeze it.
        on_halt: Optional callback invoked once per halt with the
            :class:`HaltRecord`. Use it to trigger cancel-all / flatten /
            paging. It is invoked *after* the engine's lock is released, so it
            may safely call back into the engine, and exceptions it raises are
            logged and swallowed rather than allowed to break the kill switch.

    The engine is safe to call from multiple threads; all public methods take an
    internal reentrant lock.
    """

    def __init__(
        self,
        limits: PreservationLimits,
        *,
        authorizer: Optional[ResetAuthorizer] = None,
        clock: Callable[[], float] = time.monotonic,
        on_halt: Optional[Callable[[HaltRecord], None]] = None,
    ) -> None:
        self.limits = limits
        self._authorizer = authorizer if authorizer is not None else ResetAuthorizer()
        self._clock = clock
        self._on_halt = on_halt
        self._lock = threading.RLock()

        self.state = EngineState.ACTIVE
        self.halt_reason = ""
        self.degraded_reason = ""

        # Session P&L tracking. The high-water mark is seeded at 0.0 so a
        # session that only ever loses money still reports a drawdown equal to
        # its loss.
        self.current_session_pnl = 0.0
        self.peak_session_pnl = 0.0
        self.current_drawdown = 0.0

        self._rate_window = _RateWindow(ORDER_RATE_WINDOW_SECONDS)
        self.consecutive_errors = 0
        self._last_pnl_update_at: Optional[float] = None
        self.audit_log: List[HaltRecord] = []

    # ------------------------------------------------------------------
    # Risk inputs
    # ------------------------------------------------------------------
    def update_pnl(self, realized_pnl: float, unrealized_pnl: float) -> None:
        """Feed the latest session P&L from the mark-to-market engine.

        Both arguments are *cumulative session* figures, not increments.

        A non-finite input is treated as a data-integrity failure and puts the
        engine into ``DEGRADED_WARNING`` (blocking new orders) rather than being
        compared against the limits - ``nan >= limit`` is ``False``, so a silent
        NaN would disable the drawdown control entirely.
        """
        pending: Optional[HaltRecord] = None
        with self._lock:
            try:
                total_pnl = float(realized_pnl) + float(unrealized_pnl)
            except (TypeError, ValueError):
                self._enter_degraded("P&L update was not numeric")
                return
            if not math.isfinite(total_pnl):
                self._enter_degraded(
                    "Non-finite P&L update "
                    f"(realized={realized_pnl!r}, unrealized={unrealized_pnl!r})"
                )
                return

            self._last_pnl_update_at = self._clock()
            self.current_session_pnl = total_pnl
            self.peak_session_pnl = max(self.peak_session_pnl, total_pnl)
            self.current_drawdown = self.peak_session_pnl - total_pnl
            self._clear_degraded()

            if self.current_drawdown >= self.limits.max_daily_drawdown_usd:
                pending = self._trigger_halt(
                    f"Peak-to-trough drawdown ${self.current_drawdown:,.2f} exceeded limit "
                    f"${self.limits.max_daily_drawdown_usd:,.2f} "
                    f"(peak session P&L ${self.peak_session_pnl:,.2f})"
                )
            elif (
                self.limits.max_daily_loss_usd is not None
                and -total_pnl >= self.limits.max_daily_loss_usd
            ):
                pending = self._trigger_halt(
                    f"Session loss ${-total_pnl:,.2f} exceeded limit "
                    f"${self.limits.max_daily_loss_usd:,.2f}"
                )
        self._fire_halt_callback(pending)

    def register_error(self) -> None:
        """Record a broker/venue error (FIX reject, HTTP 5xx, socket timeout).

        The counter is consecutive and has no time decay: it is cleared only by
        :meth:`register_success`. On a low-message-rate desk this means errors
        hours apart can accumulate into a halt, which is deliberate - but it is
        why ``max_consecutive_errors`` must be calibrated against your own
        message rate rather than copied.
        """
        pending: Optional[HaltRecord] = None
        with self._lock:
            self.consecutive_errors += 1
            if self.consecutive_errors >= self.limits.max_consecutive_errors:
                pending = self._trigger_halt(
                    f"{self.consecutive_errors} consecutive API errors. "
                    "Potential venue degradation."
                )
        self._fire_halt_callback(pending)

    def register_success(self) -> None:
        """Clear the consecutive-error counter after a successful operation."""
        with self._lock:
            self.consecutive_errors = 0

    # ------------------------------------------------------------------
    # Pre-trade gate
    # ------------------------------------------------------------------
    def check_order_allowed(self) -> bool:
        """Pre-trade gate. Must be called immediately before routing an order.

        Returns ``True`` only when the engine is ``ACTIVE``, the P&L feed is
        fresh (if a staleness limit is configured), and the order fits inside
        the rolling rate budget.

        This call has a side effect: on success it consumes one slot of the
        order-rate budget. Call it **exactly once per outbound order
        submission**. Calling it speculatively, or twice for one order, inflates
        the measured rate and can trip the runaway-algorithm halt spuriously.
        Conversely, an order the caller ends up not sending still counts - which
        is the correct bias for runaway detection, since a strategy looping on
        submission attempts is exactly the failure this limit exists to catch.
        """
        pending: Optional[HaltRecord] = None
        allowed = False
        with self._lock:
            if self.state == EngineState.HALTED:
                logger.critical("ORDER BLOCKED: Engine is HALTED. Reason: %s", self.halt_reason)
                return False

            now = self._clock()

            if self._pnl_is_stale(now):
                if self._last_pnl_update_at is None:
                    reason = "P&L feed has never reported; drawdown control is not yet armed"
                else:
                    age = now - self._last_pnl_update_at
                    reason = (
                        f"P&L feed stale: no update for {age:,.1f}s "
                        f"(limit {self.limits.max_pnl_staleness_seconds:,.1f}s)"
                    )
                self._enter_degraded(reason)
                logger.error("ORDER BLOCKED: %s", reason)
                return False

            # A degraded state is cleared only by a valid P&L update, never by
            # this gate - otherwise a NaN-induced degrade would be erased by the
            # very next order check without the feed having recovered.
            self._rate_window.prune(now)
            if len(self._rate_window) >= self.limits.max_orders_per_minute:
                pending = self._trigger_halt(
                    "Runaway Algo Protection: Exceeded "
                    f"{self.limits.max_orders_per_minute} orders per "
                    f"{ORDER_RATE_WINDOW_SECONDS:.0f}s."
                )
            else:
                self._rate_window.timestamps.append(now)
                allowed = self.state == EngineState.ACTIVE

        self._fire_halt_callback(pending)
        return allowed

    # ------------------------------------------------------------------
    # Recovery and persistence
    # ------------------------------------------------------------------
    def manual_reset(
        self,
        auth_token: str,
        operator: Optional[str] = None,
        rebaseline_session_pnl: bool = False,
    ) -> None:
        """Clear a halt after human review. Raises ``PermissionError`` if unauthorised.

        A reset re-arms the gate; it never raises a limit. By default the
        session's P&L history is kept, so a reset issued while the drawdown
        limit is still breached re-halts *immediately, inside this call* rather
        than leaving a window in which orders flow until the next
        :meth:`update_pnl` arrives.

        Set ``rebaseline_session_pnl=True`` only as a deliberate, recorded
        decision to start a fresh risk session: the high-water mark and drawdown
        are re-anchored to the current session P&L, which grants the strategy a
        full new drawdown budget. The choice is written to :attr:`audit_log`.

        Args:
            auth_token: Secret validated by the configured
                :class:`ResetAuthorizer`.
            operator: Identity recorded in the audit trail. Supply it - an
                anonymous kill-switch override is not an auditable one.
            rebaseline_session_pnl: Re-anchor the drawdown high-water mark to
                the current session P&L.
        """
        self._authorizer.authorize(auth_token)
        pending: Optional[HaltRecord] = None
        with self._lock:
            cleared = self.halt_reason
            self.state = EngineState.ACTIVE
            self.halt_reason = ""
            self.degraded_reason = ""
            self._rate_window.timestamps.clear()
            self.consecutive_errors = 0

            if rebaseline_session_pnl:
                self.peak_session_pnl = self.current_session_pnl
                self.current_drawdown = 0.0

            self._record(
                HaltRecord(
                    event="reset",
                    reason=(
                        (f"Manual reset clearing: {cleared}" if cleared else "Manual reset (no active halt)")
                        + (
                            "; session P&L re-baselined (new drawdown budget granted)"
                            if rebaseline_session_pnl
                            else ""
                        )
                    ),
                    occurred_at_utc=_utc_now_iso(),
                    operator=operator,
                )
            )
            logger.warning(
                "Engine manually reset by %s (rebaseline=%s). Cleared halt: %s",
                operator or "<unidentified operator>",
                rebaseline_session_pnl,
                cleared or "<none>",
            )

            # Re-evaluate the P&L limits against retained state so the reset
            # cannot open a fail-open window on a still-breached limit.
            if self.current_drawdown >= self.limits.max_daily_drawdown_usd:
                pending = self._trigger_halt(
                    f"Drawdown ${self.current_drawdown:,.2f} still exceeds limit "
                    f"${self.limits.max_daily_drawdown_usd:,.2f} at reset; "
                    "re-baseline the session or lower exposure before resuming."
                )
            elif (
                self.limits.max_daily_loss_usd is not None
                and -self.current_session_pnl >= self.limits.max_daily_loss_usd
            ):
                pending = self._trigger_halt(
                    f"Session loss ${-self.current_session_pnl:,.2f} still exceeds limit "
                    f"${self.limits.max_daily_loss_usd:,.2f} at reset."
                )
        self._fire_halt_callback(pending)

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serialisable state snapshot for durable storage.

        Persist this after every state change and pass it to :meth:`restore` on
        start-up, so a process restart during a halt wakes up HALTED instead of
        silently re-enabling trading.

        The rolling order-rate window is deliberately **not** included: its
        timestamps come from a monotonic clock whose epoch does not survive a
        process restart. A restored engine therefore starts with an empty rate
        budget, which is safe because a persisted halt still blocks every order.
        """
        with self._lock:
            return {
                "state": self.state.name,
                "halt_reason": self.halt_reason,
                "degraded_reason": self.degraded_reason,
                "current_session_pnl": self.current_session_pnl,
                "peak_session_pnl": self.peak_session_pnl,
                "current_drawdown": self.current_drawdown,
                "consecutive_errors": self.consecutive_errors,
                "audit_log": [record.to_dict() for record in self.audit_log],
            }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        """Reload state produced by :meth:`snapshot`.

        Any snapshot that cannot be parsed in full - unrecognised state name,
        non-numeric P&L, malformed audit log - restores as ``HALTED`` rather
        than ``ACTIVE``. The snapshot is parsed completely *before* any field is
        assigned, so a malformed value part-way through cannot leave the engine
        ACTIVE holding half-restored risk state.
        """
        with self._lock:
            # The monotonic epoch changed across the restart; force a fresh P&L
            # update before the staleness gate will pass again, and start with
            # an empty rate budget.
            self._last_pnl_update_at = None
            self._rate_window.timestamps.clear()

            try:
                state = EngineState[snapshot["state"]]
                session_pnl = float(snapshot.get("current_session_pnl", 0.0))
                peak_pnl = float(snapshot.get("peak_session_pnl", 0.0))
                drawdown = float(snapshot.get("current_drawdown", 0.0))
                errors = int(snapshot.get("consecutive_errors", 0))
                audit_log = [
                    HaltRecord(
                        event=str(entry.get("event", "unknown")),
                        reason=str(entry.get("reason", "")),
                        occurred_at_utc=str(entry.get("occurred_at_utc", "")),
                        operator=entry.get("operator"),
                    )
                    for entry in snapshot.get("audit_log", [])
                ]
                if not all(math.isfinite(v) for v in (session_pnl, peak_pnl, drawdown)):
                    raise ValueError("non-finite P&L in snapshot")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                self.state = EngineState.HALTED
                self.halt_reason = f"Unreadable persisted state ({exc}); failing closed."
                self.degraded_reason = ""
                logger.critical("Restored an unreadable snapshot; engine is HALTED. (%s)", exc)
                return

            self.state = state
            self.halt_reason = str(snapshot.get("halt_reason", ""))
            self.degraded_reason = str(snapshot.get("degraded_reason", ""))
            self.current_session_pnl = session_pnl
            self.peak_session_pnl = peak_pnl
            self.current_drawdown = drawdown
            self.consecutive_errors = errors
            self.audit_log = audit_log

    # ------------------------------------------------------------------
    # Internals - all called with the lock held
    # ------------------------------------------------------------------
    def _pnl_is_stale(self, now: float) -> bool:
        limit = self.limits.max_pnl_staleness_seconds
        if limit is None:
            return False
        if self._last_pnl_update_at is None:
            return True
        return (now - self._last_pnl_update_at) > limit

    def _enter_degraded(self, reason: str) -> None:
        if self.state == EngineState.HALTED:
            return
        if self.state != EngineState.DEGRADED_WARNING or self.degraded_reason != reason:
            logger.error("Engine DEGRADED (new orders blocked): %s", reason)
        self.state = EngineState.DEGRADED_WARNING
        self.degraded_reason = reason

    def _clear_degraded(self) -> None:
        """Auto-recover from a degraded state once a valid P&L update lands."""
        if self.state == EngineState.DEGRADED_WARNING:
            logger.info("Engine recovered from DEGRADED: %s", self.degraded_reason)
            self.state = EngineState.ACTIVE
            self.degraded_reason = ""

    def _trigger_halt(self, reason: str) -> Optional[HaltRecord]:
        """Latch the terminal halt. Returns the record to notify on, if new."""
        if self.state == EngineState.HALTED:
            return None
        self.state = EngineState.HALTED
        self.halt_reason = reason
        record = HaltRecord(event="halt", reason=reason, occurred_at_utc=_utc_now_iso())
        self._record(record)
        logger.critical("*** CAPITAL PRESERVATION KILL-SWITCH ENGAGED *** Reason: %s", reason)
        return record

    def _record(self, record: HaltRecord) -> None:
        self.audit_log.append(record)

    def _fire_halt_callback(self, record: Optional[HaltRecord]) -> None:
        """Invoke ``on_halt`` outside the lock, never letting it break the halt."""
        if record is None or self._on_halt is None:
            return
        try:
            self._on_halt(record)
        except Exception:  # noqa: BLE001 - a broken hook must not unlatch the halt
            logger.exception("on_halt callback raised; the halt remains latched.")


def _utc_now_iso() -> str:
    """Wall-clock timestamp for audit records only - never for interval maths."""
    return datetime.now(timezone.utc).isoformat()
