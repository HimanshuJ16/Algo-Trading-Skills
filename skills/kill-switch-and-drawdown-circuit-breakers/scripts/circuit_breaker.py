"""
kill-switch-and-drawdown-circuit-breakers: Production-grade independent risk module,
force-flatten order execution, peak equity high-water mark tracking, broker position reconciliation,
and mandatory human re-enable gates.

Design invariants (see SKILL.md "Common Pitfalls" for the rationale behind each):

* **Fail closed, never fail open.** Any input the module cannot evaluate (NaN, Inf,
  non-positive peak equity) halts trading rather than silently skipping the check.
* **A halt must never block de-risking.** Orders that strictly reduce absolute exposure
  toward zero without flipping sign are classified as reduce-only and remain permitted
  while halted, so routing every order through `check_proposed_order()` (as
  `references/workflows.md` instructs) cannot deadlock the module's own force-flatten.
* **State transitions are atomic.** All mutating paths are guarded by a re-entrant lock so
  concurrent strategy threads, a reconciliation poller, and an operator kill-switch
  endpoint cannot double-trigger or race the halt flag.
* **Every halt and every re-enable is recorded.** `audit_log` and `re_enable_log` are the
  evidence trail.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
import math
import threading
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreakerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED_POSITION_LIMIT = "HALTED_POSITION_LIMIT"
    HALTED_DAILY_LOSS = "HALTED_DAILY_LOSS"
    HALTED_DRAWDOWN = "HALTED_DRAWDOWN"
    HALTED_DESYNC = "HALTED_DESYNC"
    HALTED_MANUAL_KILL = "HALTED_MANUAL_KILL"
    HALTED_INVALID_INPUT = "HALTED_INVALID_INPUT"


class OrderDecisionCode(str, Enum):
    """Machine-readable classification prefixed onto every check_proposed_order reason."""

    OK = "OK"
    REDUCE_ONLY_ALLOWED = "REDUCE_ONLY_ALLOWED"
    HALTED = "HALTED"
    POSITION_LIMIT = "POSITION_LIMIT"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass
class BreachEventLog:
    timestamp: str
    status: CircuitBreakerStatus
    reason: str
    daily_pnl: float
    drawdown_pct: float
    current_equity: float
    positions_snapshot: Dict[str, float]
    flatten_attempted: bool = False
    flatten_succeeded: Optional[bool] = None
    flatten_error: Optional[str] = None
    alert_error: Optional[str] = None


@dataclass
class ReEnableEventLog:
    """Audit record for every operator attempt to clear a halt, granted or refused."""

    timestamp: str
    authorized_user: str
    reason: str
    cleared_status: CircuitBreakerStatus
    granted: bool
    new_peak_equity: Optional[float] = None
    rejection_reason: Optional[str] = None


def _is_finite(*values: float) -> bool:
    """True only if every value is a real, finite number (rejects NaN and +/-Inf)."""
    for v in values:
        try:
            if not math.isfinite(float(v)):
                return False
        except (TypeError, ValueError):
            return False
    return True


def is_risk_reducing(current_position_size: float, proposed_position_size: float) -> bool:
    """
    True if the proposed delta strictly moves the position toward zero without crossing it.

    A reversal (long 100 -> short 50) is NOT risk-reducing: it closes one exposure and
    opens a new one, so it must remain subject to the position limit and the halt gate.
    """
    if not _is_finite(current_position_size, proposed_position_size):
        return False
    if proposed_position_size == 0:
        return False
    projected = current_position_size + proposed_position_size
    if abs(projected) >= abs(current_position_size):
        return False
    if current_position_size > 0 and projected < 0:
        return False
    if current_position_size < 0 and projected > 0:
        return False
    return True


class KillSwitchCircuitBreaker:
    """
    Independent risk governance module with authority to veto proposed orders,
    trigger force-flattening, and require explicit human re-enable before trading resumes.

    Thread-safety: all public methods are safe to call concurrently. A halt in progress
    blocks other threads' state transitions until the flatten callback returns, which is
    the intended behaviour - no thread should be trading during a halt. The lock is
    re-entrant, so `flatten_fn` may route its liquidation orders back through
    `check_proposed_order()` on the calling thread. It must NOT, however, block waiting on a
    *different* thread that calls into this breaker: that thread cannot acquire the lock
    until the flatten returns, and the two deadlock.
    """

    def __init__(
        self,
        max_position: float,
        max_daily_loss: float,
        max_drawdown_pct: float,
        alert_fn: Optional[Callable[[str], None]] = None,
        flatten_fn: Optional[Callable[[], None]] = None,
        desync_tolerance_units: float = 0.001,
        allow_reduce_only_when_halted: bool = True,
        authorized_operators: Optional[List[str]] = None,
        max_consecutive_rejections: Optional[int] = None,
    ):
        """
        Args:
            max_position: Maximum absolute position size per instrument. Must be > 0.
            max_daily_loss: Daily loss limit as a magnitude; the sign is ignored. Non-zero.
            max_drawdown_pct: Peak-equity drawdown limit as a FRACTION in (0, 1] - 0.10
                means 10%. Passing 10 for "10%" is rejected rather than silently disabling
                the breaker for the lifetime of the process.
            alert_fn: Out-of-band alert sink. Exceptions raised here are swallowed and
                logged so a dead alert channel can never prevent a force-flatten.
            flatten_fn: Force-flatten callback. Must use aggressive/marketable orders,
                not passive resting orders.
            desync_tolerance_units: Absolute per-symbol quantity tolerance for broker
                reconciliation. Must be finite and >= 0.
            allow_reduce_only_when_halted: If True (default), orders that strictly reduce
                absolute exposure stay permitted while halted. Set False ONLY if
                liquidation is routed around this gate entirely - otherwise the halt
                blocks its own force-flatten.
            authorized_operators: Optional allowlist of operator identities permitted to
                call `human_re_enable`. None disables enforcement (identity is still
                recorded, just not checked against a list).
            max_consecutive_rejections: Optional escalation. After this many consecutive
                position-limit rejections the breaker halts with HALTED_POSITION_LIMIT, on
                the SEC Rule 15c3-5(c)(1)(ii) principle that erroneous-order controls apply
                "over a short period of time", not only order-by-order. None (default)
                disables the escalation - no threshold is assumed on your behalf.
        """
        if not _is_finite(max_position) or max_position <= 0:
            raise ValueError(f"max_position must be a finite number > 0, got {max_position!r}")
        if not _is_finite(max_daily_loss) or abs(max_daily_loss) == 0:
            raise ValueError(f"max_daily_loss must be a finite non-zero number, got {max_daily_loss!r}")
        if not _is_finite(max_drawdown_pct) or not 0 < max_drawdown_pct <= 1:
            raise ValueError(
                f"max_drawdown_pct must be a fraction in (0, 1] - 0.10 means 10%. Got {max_drawdown_pct!r}."
            )
        if not _is_finite(desync_tolerance_units) or desync_tolerance_units < 0:
            raise ValueError(
                f"desync_tolerance_units must be a finite number >= 0, got {desync_tolerance_units!r}"
            )
        if max_consecutive_rejections is not None and (
            not isinstance(max_consecutive_rejections, int)
            or isinstance(max_consecutive_rejections, bool)
            or max_consecutive_rejections < 1
        ):
            raise ValueError(
                f"max_consecutive_rejections must be None or an int >= 1, got {max_consecutive_rejections!r}"
            )

        self.max_position = float(max_position)
        self.max_daily_loss = abs(float(max_daily_loss))
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.flatten_fn = flatten_fn or (lambda: logger.info("Force-flattening all open positions..."))
        self.desync_tolerance_units = float(desync_tolerance_units)
        self.allow_reduce_only_when_halted = allow_reduce_only_when_halted
        self.authorized_operators = list(authorized_operators) if authorized_operators is not None else None
        self.max_consecutive_rejections = max_consecutive_rejections

        self._lock = threading.RLock()
        self.status = CircuitBreakerStatus.ACTIVE
        self.halted = False
        self.peak_equity: Optional[float] = None
        self.consecutive_rejections = 0
        self.audit_log: List[BreachEventLog] = []
        self.re_enable_log: List[ReEnableEventLog] = []

    # ------------------------------------------------------------------ orders

    def check_proposed_order(
        self, proposed_position_delta: float, current_position_size: float, symbol: str = "DEFAULT"
    ) -> Tuple[bool, str]:
        """
        Vetoes an order if the breaker is halted or the projected position exceeds limits.

        `proposed_position_delta` is the *change* this order would make to the position,
        signed by side (+100 to buy 100, -100 to sell 100) - not the position the order
        would leave behind. It is checked against the projected position
        `current_position_size + proposed_position_delta`, so passing an absolute target
        here would silently overstate the exposure being evaluated.

        Returns (approved, reason). The reason is always prefixed with an OrderDecisionCode
        value followed by ": ", so callers branch on the code rather than parsing free text.

        Risk-reducing orders (see `is_risk_reducing`) are approved even while halted and
        even when the current position already exceeds `max_position`, unless
        `allow_reduce_only_when_halted` is False.
        """
        if not _is_finite(proposed_position_delta, current_position_size):
            msg = (
                f"{OrderDecisionCode.INVALID_INPUT.value}: Order rejected for '{symbol}': non-finite "
                f"size (proposed={proposed_position_delta!r}, current={current_position_size!r})."
            )
            logger.error(msg)
            return False, msg

        with self._lock:
            reducing = is_risk_reducing(current_position_size, proposed_position_delta)

            if self.halted:
                if reducing and self.allow_reduce_only_when_halted:
                    return True, (
                        f"{OrderDecisionCode.REDUCE_ONLY_ALLOWED.value}: Risk-reducing order permitted "
                        f"for '{symbol}' while halted ({self.status.value})."
                    )
                return False, (
                    f"{OrderDecisionCode.HALTED.value}: Order rejected: System is in HALTED state "
                    f"({self.status.value})."
                )

            projected = abs(current_position_size + proposed_position_delta)

            if projected > self.max_position and not reducing:
                msg = (
                    f"{OrderDecisionCode.POSITION_LIMIT.value}: Order rejected: Projected position "
                    f"{projected} for '{symbol}' exceeds max limit {self.max_position}."
                )
                logger.warning(msg)
                self.consecutive_rejections += 1
                if (
                    self.max_consecutive_rejections is not None
                    and self.consecutive_rejections >= self.max_consecutive_rejections
                ):
                    self._trigger_halt(
                        CircuitBreakerStatus.HALTED_POSITION_LIMIT,
                        (
                            f"Runaway order flow: {self.consecutive_rejections} consecutive position-limit "
                            f"rejections (escalation threshold {self.max_consecutive_rejections}); "
                            f"last symbol '{symbol}'"
                        ),
                        daily_pnl=0.0,
                        drawdown_pct=0.0,
                        current_equity=self.peak_equity or 0.0,
                        positions_snapshot={symbol: current_position_size},
                    )
                return False, msg

            self.consecutive_rejections = 0
            if projected > self.max_position:
                return True, (
                    f"{OrderDecisionCode.REDUCE_ONLY_ALLOWED.value}: Risk-reducing order permitted for "
                    f"'{symbol}' despite position {projected} exceeding max limit {self.max_position}."
                )
            return True, f"{OrderDecisionCode.OK.value}: ok"

    # -------------------------------------------------------------- pnl / risk

    def check_pnl_and_drawdown(
        self, daily_pnl: float, current_equity: float, active_positions: Optional[Dict[str, float]] = None
    ) -> Tuple[bool, str]:
        """
        Evaluates daily loss and peak-equity drawdown limits. Returns (is_halted, status).

        Fails CLOSED: a non-finite `daily_pnl` or `current_equity`, or a non-positive peak
        equity, halts with HALTED_INVALID_INPUT rather than silently passing every
        comparison - NaN compares False against every threshold, which would leave both
        the loss limit and the drawdown limit inert with no outward signal.
        """
        with self._lock:
            if self.halted:
                return True, f"Already halted ({self.status.value})."

            positions = active_positions or {}

            if not _is_finite(daily_pnl, current_equity):
                self._trigger_halt(
                    CircuitBreakerStatus.HALTED_INVALID_INPUT,
                    (
                        f"Risk inputs not evaluable: daily_pnl={daily_pnl!r}, "
                        f"current_equity={current_equity!r}. Halting fail-closed."
                    ),
                    daily_pnl=0.0,
                    drawdown_pct=0.0,
                    current_equity=self.peak_equity or 0.0,
                    positions_snapshot=positions,
                )
                return True, self.status.value

            if self.peak_equity is None or current_equity > self.peak_equity:
                self.peak_equity = current_equity

            if self.peak_equity <= 0:
                self._trigger_halt(
                    CircuitBreakerStatus.HALTED_INVALID_INPUT,
                    (
                        f"Peak equity {self.peak_equity} is not positive; drawdown is undefined. "
                        f"Halting fail-closed."
                    ),
                    daily_pnl=daily_pnl,
                    drawdown_pct=0.0,
                    current_equity=current_equity,
                    positions_snapshot=positions,
                )
                return True, self.status.value

            drawdown_pct = (self.peak_equity - current_equity) / self.peak_equity

            # 1. Daily Loss Check
            if daily_pnl <= -self.max_daily_loss:
                self._trigger_halt(
                    CircuitBreakerStatus.HALTED_DAILY_LOSS,
                    f"Daily loss breach: PnL {daily_pnl:.2f} <= -{self.max_daily_loss:.2f}",
                    daily_pnl,
                    drawdown_pct,
                    current_equity,
                    positions,
                )
                return True, self.status.value

            # 2. Max Drawdown Check
            if drawdown_pct >= self.max_drawdown_pct:
                self._trigger_halt(
                    CircuitBreakerStatus.HALTED_DRAWDOWN,
                    f"Max drawdown breach: Drawdown {drawdown_pct:.2%} >= {self.max_drawdown_pct:.2%}",
                    daily_pnl,
                    drawdown_pct,
                    current_equity,
                    positions,
                )
                return True, self.status.value

            return False, "ok"

    def record_capital_flow(self, amount: float) -> Optional[float]:
        """
        Shift the peak-equity high-water mark by an external deposit (+) or withdrawal (-).

        Without this, a scheduled withdrawal is booked as drawdown and can trip the kill
        switch and flatten a healthy book, while a deposit inflates the peak and
        understates subsequent real drawdown. Call it when the cash movement settles, and
        before the next `check_pnl_and_drawdown` that uses post-flow equity.

        Returns the adjusted peak equity, or None if no high-water mark exists yet.
        """
        if not _is_finite(amount):
            raise ValueError(f"Capital flow amount must be a finite number, got {amount!r}")
        with self._lock:
            if self.peak_equity is None:
                return None
            self.peak_equity += float(amount)
            logger.info(
                "Capital flow of %.2f applied; peak equity high-water mark adjusted to %.2f",
                amount,
                self.peak_equity,
            )
            return self.peak_equity

    # ------------------------------------------------------------ reconciliation

    def reconcile_broker_positions(
        self, internal_positions: Dict[str, float], broker_positions: Dict[str, float]
    ) -> bool:
        """
        Reconciles internal position state against broker account state.

        Returns True only when every symbol agrees within `desync_tolerance_units`. The
        comparison runs even while halted, so an operator can confirm the book is clean
        before re-enabling; a desync found while already halted is logged without
        re-triggering the halt response.

        Symbols are compared in sorted order so the symbol named in the audit log is
        deterministic across processes.
        """
        for sym in sorted(set(internal_positions) | set(broker_positions)):
            internal_qty = internal_positions.get(sym, 0.0)
            broker_qty = broker_positions.get(sym, 0.0)

            if not _is_finite(internal_qty, broker_qty):
                msg = (
                    f"POSITION DESYNC UNRESOLVABLE for '{sym}': non-finite quantity "
                    f"(Internal={internal_qty!r}, Broker={broker_qty!r})"
                )
            elif abs(internal_qty - broker_qty) > self.desync_tolerance_units:
                msg = f"POSITION DESYNC DETECTED for '{sym}': Internal={internal_qty}, Broker={broker_qty}"
            else:
                continue

            with self._lock:
                if not self.halted:
                    self._trigger_halt(
                        CircuitBreakerStatus.HALTED_DESYNC,
                        msg,
                        daily_pnl=0.0,
                        drawdown_pct=0.0,
                        current_equity=self.peak_equity or 0.0,
                        positions_snapshot=dict(internal_positions),
                    )
                else:
                    logger.warning("%s (breaker already halted: %s)", msg, self.status.value)
            return False
        return True

    # -------------------------------------------------------------- halt / gate

    def trigger_emergency_kill_switch(self, reason: str = "Manual emergency halt requested") -> None:
        """
        Manual emergency kill switch trigger.

        Always re-runs the alert and force-flatten response, even if already halted, since
        an operator pressing the kill switch is explicitly asking for liquidation. The
        `status` field retains the ORIGINAL halt cause; `audit_log` records every event.
        """
        with self._lock:
            self._trigger_halt(
                CircuitBreakerStatus.HALTED_MANUAL_KILL,
                f"MANUAL KILL SWITCH TRIGGERED: {reason}",
                daily_pnl=0.0,
                drawdown_pct=0.0,
                current_equity=self.peak_equity or 0.0,
                positions_snapshot={},
                force_response=True,
            )

    def _trigger_halt(
        self,
        status: CircuitBreakerStatus,
        reason: str,
        daily_pnl: float,
        drawdown_pct: float,
        current_equity: float,
        positions_snapshot: Dict[str, float],
        force_response: bool = False,
    ) -> None:
        with self._lock:
            already_halted = self.halted
            self.halted = True
            if not already_halted:
                # First cause wins: an operator reading `status` sees the root cause, not
                # whichever breaker happened to fire last. Full history lives in audit_log.
                self.status = status

            run_response = force_response or not already_halted
            log_entry = BreachEventLog(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                status=status,
                reason=reason,
                daily_pnl=daily_pnl,
                drawdown_pct=drawdown_pct,
                current_equity=current_equity,
                positions_snapshot=positions_snapshot,
                flatten_attempted=run_response,
            )
            self.audit_log.append(log_entry)

            if not run_response:
                return

            alert_msg = f"CRITICAL RISK ALERT [{status.value}]: {reason}"

            # A dead alert channel must NEVER prevent the force-flatten, so the alert call
            # is isolated and its failure recorded rather than propagated to the caller.
            try:
                self.alert_fn(alert_msg)
            except Exception as exc:  # noqa: BLE001 - alert sinks are arbitrary user code
                log_entry.alert_error = repr(exc)
                logger.critical("Alert channel failed during halt [%s]: %r", status.value, exc)

            try:
                self.flatten_fn()
                log_entry.flatten_succeeded = True
            except Exception as exc:  # noqa: BLE001 - flatten sinks are arbitrary user code
                log_entry.flatten_succeeded = False
                log_entry.flatten_error = repr(exc)
                logger.critical("Error during force-flatten execution: %r", exc)
                # A failed flatten means positions are still live during a breach. That is
                # the single most urgent event this module can produce, so it is escalated
                # out-of-band instead of being left to a log reader to discover.
                try:
                    self.alert_fn(
                        f"CRITICAL RISK ALERT [{status.value}] FORCE-FLATTEN FAILED: {exc!r}. "
                        f"Positions may still be open - MANUAL INTERVENTION REQUIRED."
                    )
                except Exception as alert_exc:  # noqa: BLE001
                    logger.critical("Alert channel also failed reporting flatten failure: %r", alert_exc)

    def human_re_enable(
        self,
        authorized_user: str,
        reason: str,
        new_peak_equity: Optional[float] = None,
    ) -> bool:
        """
        Human re-enable gate. Returns True only if the halt was actually cleared.

        Enforces - rather than merely documents - the operator gate:
          * refuses a blank operator identity or a blank reason;
          * refuses an operator absent from `authorized_operators` when that list is set;
          * refuses when the breaker is not halted (nothing to clear);
          * records every attempt, granted or refused, in `re_enable_log`.

        `new_peak_equity` explicitly re-baselines the drawdown high-water mark. Without it,
        re-enabling after a drawdown halt leaves the breached high-water mark in place and
        the very next `check_pnl_and_drawdown` re-halts immediately. Re-baselining is never
        automatic: silently resetting the peak would erase the drawdown limit.
        """
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        def _refuse(rejection: str) -> bool:
            self.re_enable_log.append(
                ReEnableEventLog(
                    timestamp=timestamp,
                    authorized_user=str(authorized_user),
                    reason=str(reason),
                    cleared_status=self.status,
                    granted=False,
                    rejection_reason=rejection,
                )
            )
            logger.error("Circuit breaker re-enable REFUSED for %r: %s", authorized_user, rejection)
            return False

        with self._lock:
            if not isinstance(authorized_user, str) or not authorized_user.strip():
                return _refuse("Operator identity is required for an auditable re-enable.")
            if not isinstance(reason, str) or not reason.strip():
                return _refuse("A re-enable reason is required for an auditable re-enable.")
            if self.authorized_operators is not None and authorized_user not in self.authorized_operators:
                return _refuse(f"Operator '{authorized_user}' is not on the authorized-operator allowlist.")
            if not self.halted:
                return _refuse("Circuit breaker is not halted; nothing to re-enable.")
            if new_peak_equity is not None and (not _is_finite(new_peak_equity) or new_peak_equity <= 0):
                return _refuse(f"new_peak_equity must be a finite number > 0, got {new_peak_equity!r}")

            cleared_status = self.status
            if new_peak_equity is not None:
                self.peak_equity = float(new_peak_equity)

            self.halted = False
            self.status = CircuitBreakerStatus.ACTIVE
            self.consecutive_rejections = 0

            self.re_enable_log.append(
                ReEnableEventLog(
                    timestamp=timestamp,
                    authorized_user=authorized_user,
                    reason=reason,
                    cleared_status=cleared_status,
                    granted=True,
                    new_peak_equity=new_peak_equity,
                )
            )
            logger.info(
                "Circuit breaker re-enabled by '%s' (cleared %s). Reason: %s",
                authorized_user,
                cleared_status.value,
                reason,
            )
            return True


# Backward compatibility wrapper class
class CircuitBreaker:

    def __init__(self, max_position, max_daily_loss, max_drawdown_pct, alert_fn):
        self.engine = KillSwitchCircuitBreaker(
            max_position=max_position,
            max_daily_loss=max_daily_loss,
            max_drawdown_pct=max_drawdown_pct,
            alert_fn=alert_fn,
        )
        self.max_position = max_position
        self.max_daily_loss = max_daily_loss
        self.max_drawdown_pct = max_drawdown_pct
        self.alert_fn = alert_fn

    @property
    def peak_equity(self):
        return self.engine.peak_equity

    @peak_equity.setter
    def peak_equity(self, val):
        self.engine.peak_equity = val

    @property
    def halted(self):
        """Read-only. A halt is cleared only through the audited `human_re_enable` path.

        There is deliberately no setter: assigning `halted = False` would clear a halt
        with no operator, no reason, and no `re_enable_log` row -- exactly the unaudited
        bypass the module exists to prevent.
        """
        return self.engine.halted

    def human_re_enable(self, authorized_user, reason, new_peak_equity=None):
        """The only way to clear a halt through this wrapper. Returns True if cleared."""
        return self.engine.human_re_enable(authorized_user, reason, new_peak_equity)

    def check_order(self, proposed_position_size, current_position_size):
        ok, reason = self.engine.check_proposed_order(proposed_position_size, current_position_size)
        if ok:
            return True, "ok"
        # Branch on the machine-readable prefix rather than substring-sniffing the human
        # text: a HALTED_POSITION_LIMIT halt message also contains the word "position".
        code = reason.split(":", 1)[0]
        if code == OrderDecisionCode.POSITION_LIMIT.value:
            return False, "position_limit"
        if code == OrderDecisionCode.INVALID_INPUT.value:
            return False, "invalid_input"
        return False, "halted"

    def check_pnl(self, daily_pnl, current_equity):
        return self.engine.check_pnl_and_drawdown(daily_pnl, current_equity)[0]
