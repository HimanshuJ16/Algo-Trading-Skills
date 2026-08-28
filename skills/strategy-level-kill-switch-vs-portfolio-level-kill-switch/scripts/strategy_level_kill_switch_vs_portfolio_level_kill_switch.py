"""
strategy-level-kill-switch-vs-portfolio-level-kill-switch: a two-tier circuit breaker that
decides *which scope* must stop. A strategy-level trip isolates one strategy; a
portfolio-level trip halts the whole fund. The hard part is not the drawdown arithmetic - it
is keeping the two scopes from contaminating each other.

Design invariants (see SKILL.md "Common Pitfalls" for the rationale behind each):

* **The trip latches.** `is_triggered` reports whether the kill switch is *engaged*, not
  whether the current equity happens to breach right now. Recomputing it from live equity on
  every poll silently re-enables a killed strategy the moment price bounces - which is the
  auto-resume this control exists to prevent.
* **Liquidation dispatches exactly once.** `is_newly_tripped` is True only on the transition
  into the trip. A 1-second risk loop reading `is_triggered` instead would fire a fresh
  liquidation cascade every second while the first one is still working.
* **Fail closed, and never fail open.** A `NaN` equity makes `dd >= limit` False, so an
  unchecked non-finite input turns the breaker off while reporting healthy. Non-finite inputs
  and a non-positive peak halt the scope (`HALTED_INVALID_INPUT`) instead.
* **A fail-closed halt does not liquidate, and does not feed the cascade counter.** The engine
  has no evidence the book is down, so market-flattening on one bad tick is itself the loss
  event. Critically, a shared feed outage halts *every* strategy at once: counting halts as
  cascade failures would liquidate a perfectly healthy fund on a data problem. Only strategies
  tripped by their own measured drawdown count toward `max_tripped_strategies_limit`.
* **The cascade counter excludes the portfolio switch's own fan-out.** A portfolio trip marks
  every strategy tripped; if those fed the cascade count, the portfolio switch would
  permanently re-justify itself and re-trip the instant an operator cleared it.
* **The hierarchy propagates downward only.** While the portfolio latch is engaged, a healthy
  strategy still reports `is_trading_halted=True` (`PORTFOLIO_HALT_INHERITED`). A strategy
  trip never halts its siblings.
* **Re-enabling is human, audited, and scope-aware.** Recovery runs strategies-first,
  fund-second: clearing a strategy latch is allowed while the fund is halted (it cannot
  resume trading), and clearing the master latch is refused while the cascade condition still
  holds, so the fund cannot be re-enabled straight back into a re-trip. Clearing the master
  latch releases the strategies the master switch itself halted, never one that tripped on
  its own drawdown.
* **State transitions are atomic.** A re-entrant lock guards every latch so a strategy loop, a
  risk poller and an operator endpoint cannot race them.

Scope limits (deliberately *not* handled here):

* No order gating and no order cancellation. This engine decides *which scope* must stop;
  latching order entry and dispatching FIX cancels is
  `execution-algorithm-kill-switch-integration`, and enforcing reduce-only flow while halted
  (so the halt does not veto its own liquidation) is
  `kill-switch-and-drawdown-circuit-breakers`.
* No FX conversion and no reconciliation. Every equity figure must already be in one
  reporting currency and should be sourced from the broker/custodian, not the bot's internal
  bookkeeping. The engine never checks that the strategy equities sum to the portfolio equity.
* No regulatory thresholds. The 10% / 15% / 3-strategy defaults are firm risk policy - see
  `references/standards.md`.
"""
import datetime
import logging
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """Legacy Config container for backward compatibility.

    This carries no risk policy. The real limits live on `StrategyState.drawdown_limit_pct`
    and `PortfolioState.portfolio_drawdown_limit_pct` / `max_tripped_strategies_limit`.
    """

    name: str = "strategy-level-kill-switch-vs-portfolio-level-kill-switch"


class Engine:
    """Legacy Engine class for backward compatibility.

    `run()` is a construction stub, **not** a risk check. Nothing here evaluates a drawdown,
    trips a switch or halts anything; a caller that reads a `True` from it as "the kill switch
    is healthy" has verified nothing at all. Use `HierarchicalKillSwitchEngine`.
    """

    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        """Always returns True. Not a kill-switch evaluation - see the class docstring."""
        return True


class KillSwitchScope(str, Enum):
    STRATEGY_LEVEL = "STRATEGY_LEVEL"
    PORTFOLIO_LEVEL = "PORTFOLIO_LEVEL"


class KillSwitchAction(str, Enum):
    """What the caller must *do now* as a result of one evaluation.

    `NO_ACTION` exists because reporting `SOFT_HALT` for a healthy strategy - as versions
    before 2.0.0 did - means a caller that reads `report.action` without also checking
    `is_triggered` halts a strategy that never breached anything.
    """

    NO_ACTION = "NO_ACTION"                 # Nothing to dispatch; read is_trading_halted
    SOFT_HALT = "SOFT_HALT"                 # Block new entries; allow passive exits
    HARD_LIQUIDATE = "HARD_LIQUIDATE"       # Cancel all orders; market liquidate positions


class KillSwitchReason(str, Enum):
    """Machine-readable cause behind one report, for the audit trail and for escalation."""

    NO_BREACH = "NO_BREACH"
    STRATEGY_DRAWDOWN_BREACH = "STRATEGY_DRAWDOWN_BREACH"
    PORTFOLIO_DRAWDOWN_BREACH = "PORTFOLIO_DRAWDOWN_BREACH"
    CASCADE_BREACH = "CASCADE_BREACH"
    LATCHED_PRIOR_TRIP = "LATCHED_PRIOR_TRIP"
    PORTFOLIO_HALT_INHERITED = "PORTFOLIO_HALT_INHERITED"
    HALTED_INVALID_INPUT = "HALTED_INVALID_INPUT"


#: Trips that count toward `PortfolioState.max_tripped_strategies_limit`. A fail-closed halt
#: is excluded on purpose: one bad feed halts every strategy simultaneously, and counting
#: those would cascade-liquidate a fund that never lost a cent.
_CASCADE_ELIGIBLE_REASONS = frozenset({KillSwitchReason.STRATEGY_DRAWDOWN_BREACH.value})


def _is_finite(*values: object) -> bool:
    """True only if every value is a real, finite number (rejects NaN, +/-Inf, non-numerics).

    Strings are rejected rather than coerced. `"88000"` would parse and trade while
    `"88,000"` from the same mis-wired field would raise and halt; a risk input whose
    behaviour depends on how a number was formatted is a bug waiting for a bad day.
    """
    for value in values:
        if isinstance(value, (str, bytes, bytearray)):
            return False
        try:
            if not math.isfinite(float(value)):  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    return True


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _validate_limit_pct(field_name: str, value: object) -> float:
    """Validates a drawdown limit expressed in **percentage points** (10.0 means 10%).

    Raises on a value that cannot bound anything. Warns - rather than raising - on a limit
    below 1.0, because `0.10` meaning "10%" is a plausible units slip that here reads as 0.1%
    and liquidates on noise, while a genuine 0.5% limit is legitimate for some books.
    """
    if not _is_finite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    limit = float(value)  # type: ignore[arg-type]
    if not 0.0 < limit <= 100.0:
        raise ValueError(
            f"{field_name} must be in (0, 100] percentage points - 10.0 means 10%, "
            f"not 0.10. Got {value!r}."
        )
    if limit < 1.0:
        logger.warning(
            "%s = %r is below 1 percentage point. If this was meant as a fraction "
            "(0.10 for 10%%), it reads here as %.2f%% and will trip on noise.",
            field_name, value, limit,
        )
    return limit


@dataclass
class StrategyState:
    """One sub-strategy's equity baseline and latched kill-switch state.

    `peak_equity_usd` is the high-water mark the engine ratchets, measured **net of settled
    capital flows**: pass the cumulative net flow into this strategy since the baseline was
    recorded as `capital_flow_usd` on each evaluation, or an allocation top-up ratchets the
    peak and a withdrawal reads as a drawdown.

    `drawdown_limit_pct` is in **percentage points** (10.0 = 10%), and is firm risk policy -
    no rule surveyed in `references/standards.md` mandates a number.
    """

    strategy_id: str
    peak_equity_usd: float
    current_equity_usd: float
    drawdown_limit_pct: float = 10.0       # 10% max strategy drawdown
    is_tripped: bool = False
    action_taken: Optional[KillSwitchAction] = None
    tripped_time_epoch: float = 0.0
    # Which scope latched this strategy: STRATEGY_LEVEL for its own breach or halt,
    # PORTFOLIO_LEVEL when the master switch fanned out to it. Drives both the cascade
    # counter and what a scoped `human_re_enable()` is allowed to clear.
    tripped_by_scope: Optional[KillSwitchScope] = None
    tripped_reason: Optional[str] = None   # a KillSwitchReason value


@dataclass
class PortfolioState:
    """Master portfolio equity baseline and latched master kill-switch state.

    `max_tripped_strategies_limit` counts only strategies tripped by their **own measured
    drawdown**. Set it relative to the number of strategies actually registered: a limit
    larger than the roster can never fire, and the engine warns when that is the case.
    """

    total_peak_equity_usd: float
    total_current_equity_usd: float
    portfolio_drawdown_limit_pct: float = 15.0  # 15% max portfolio drawdown
    max_tripped_strategies_limit: int = 3       # Trip portfolio if 3 strategies fail
    is_portfolio_tripped: bool = False
    tripped_time_epoch: float = 0.0
    tripped_reason: Optional[str] = None        # a KillSwitchReason value


@dataclass
class KillSwitchExecutionReport:
    """Structured, auditable outcome of one evaluation at one scope.

    Read the three booleans for different questions:

    * `is_newly_tripped` - **dispatch the liquidation now, exactly once.** Never gate a
      liquidation on `is_triggered`; that re-fires on every poll.
    * `is_trading_halted` - **may this scope trade?** For a strategy this is True when the
      strategy itself is latched *or* the master portfolio switch is engaged.
    * `is_triggered` - is this scope's own kill switch engaged (breach now, or latched from an
      earlier breach)? Latch-inclusive by design.

    `drawdown_pct` is rounded for readability; the breach decision uses the unrounded value,
    so a reported `10.00` with `is_triggered` False means the true drawdown sat just below.
    """

    scope: KillSwitchScope
    strategy_id: Optional[str]              # None if portfolio-level
    is_triggered: bool
    drawdown_pct: float
    limit_pct: float
    action: KillSwitchAction
    affected_strategies: List[str]          # strategies newly halted by THIS report
    audit_notes: str
    reason_code: str = KillSwitchReason.NO_BREACH.value
    is_newly_tripped: bool = False
    is_latched: bool = False                # engaged by a prior evaluation, not this one
    is_trading_halted: bool = False
    tripped_strategy_count: int = 0         # strategies latched by their own drawdown
    evaluated_at: Optional[str] = None


@dataclass
class ReEnableEventLog:
    """Audit record for every operator attempt to clear a latch, granted or refused."""

    timestamp: str
    scope: str
    strategy_id: Optional[str]
    operator_id: str
    reason: str
    granted: bool
    released_strategies: Tuple[str, ...] = ()
    rejection_reason: Optional[str] = None


class HierarchicalKillSwitchEngine:
    """
    Hierarchical kill switch governing Strategy-Level vs Portfolio-Level circuit breakers:
    per-strategy drawdown isolation, master portfolio drawdown, cascade detection, and the
    audited human re-enable that is the only way out of either latch.

    The engine owns the latches and nothing else. It places no orders, cancels nothing and
    computes no P&L - it consumes equity figures the caller sources from the broker and
    returns the scope decision plus a one-shot dispatch flag.
    """

    def __init__(
        self,
        portfolio_state: PortfolioState,
        strategies: List[StrategyState],
        cooldown_seconds: float = 86400.0,   # 24-hour dwell before a re-enable is accepted
        authorized_operators: Tuple[str, ...] = (),
        clock: Callable[[], float] = time.time,
    ):
        """
        `cooldown_seconds` is a **minimum dwell time that gates** the human re-enable, not a
        timer that resumes trading on its own. Nothing surveyed in `references/standards.md`
        mandates a duration; the default is firm policy. Pass an empty
        `authorized_operators` to accept any non-blank operator identity (still audited).

        `clock` is injectable so cooldown behaviour is testable without sleeping.
        """
        if not callable(clock):
            raise TypeError("clock must be a zero-argument callable returning epoch seconds")
        if not _is_finite(cooldown_seconds) or float(cooldown_seconds) < 0.0:
            raise ValueError(
                f"cooldown_seconds must be a non-negative finite number, got "
                f"{cooldown_seconds!r}"
            )

        _validate_limit_pct(
            "portfolio_drawdown_limit_pct", portfolio_state.portfolio_drawdown_limit_pct
        )
        cascade_limit = portfolio_state.max_tripped_strategies_limit
        if not isinstance(cascade_limit, int) or isinstance(cascade_limit, bool) or cascade_limit < 1:
            raise ValueError(
                f"max_tripped_strategies_limit must be an integer >= 1, got {cascade_limit!r}"
            )

        self.strategies: Dict[str, StrategyState] = {}
        for strategy in strategies:
            if strategy.strategy_id in self.strategies:
                raise ValueError(
                    f"Duplicate strategy_id {strategy.strategy_id!r}; the later state would "
                    f"shadow the earlier one and silently drop a monitored strategy."
                )
            _validate_limit_pct(
                f"drawdown_limit_pct[{strategy.strategy_id}]", strategy.drawdown_limit_pct
            )
            self.strategies[strategy.strategy_id] = strategy

        if cascade_limit > len(self.strategies):
            logger.warning(
                "max_tripped_strategies_limit=%d exceeds the %d registered strategies; the "
                "cascade trigger can never fire.", cascade_limit, len(self.strategies),
            )

        self.portfolio_state = portfolio_state
        self.cooldown_seconds = float(cooldown_seconds)
        self.authorized_operators: Tuple[str, ...] = tuple(authorized_operators)
        self._clock = clock
        self._lock = threading.RLock()
        self.re_enable_log: List[ReEnableEventLog] = []

    # ---------------------------------------------------------------- latch state

    @property
    def is_portfolio_halted(self) -> bool:
        """True while the master portfolio latch is engaged and un-cleared."""
        with self._lock:
            return self.portfolio_state.is_portfolio_tripped

    @property
    def tripped_strategy_ids(self) -> List[str]:
        """Every latched strategy, whatever scope or reason latched it."""
        with self._lock:
            return [s.strategy_id for s in self.strategies.values() if s.is_tripped]

    @property
    def cascade_trip_count(self) -> int:
        """Strategies latched by their own measured drawdown - the cascade counter's input."""
        with self._lock:
            return len(self._cascade_tripped_ids())

    def is_strategy_trading_halted(self, strategy_id: str) -> bool:
        """The order-gate answer for one strategy, including an inherited portfolio halt."""
        with self._lock:
            strategy = self._require_strategy(strategy_id)
            return strategy.is_tripped or self.portfolio_state.is_portfolio_tripped

    # ---------------------------------------------------------------- evaluation

    def evaluate_strategy_kill_switch(
        self,
        strategy_id: str,
        current_equity_usd: float,
        action: KillSwitchAction = KillSwitchAction.HARD_LIQUIDATE,
        capital_flow_usd: float = 0.0,
    ) -> KillSwitchExecutionReport:
        """Evaluates one strategy's drawdown against its own limit and latches on breach.

        `capital_flow_usd` is the cumulative **settled** net capital flow into this strategy
        since `peak_equity_usd` was recorded (positive for allocations in, negative for
        withdrawals); it is removed before the drawdown is measured so a funding movement is
        not read as P&L.

        Tripping one strategy never halts its siblings - that isolation is the whole point of
        the strategy tier. It does, however, advance the cascade counter the portfolio tier
        reads.
        """
        action = self._require_dispatchable_action(action)

        with self._lock:
            strategy = self._require_strategy(strategy_id)

            if not _is_finite(current_equity_usd, capital_flow_usd):
                return self._halt_strategy(
                    strategy,
                    f"current_equity_usd/capital_flow_usd is not finite "
                    f"({current_equity_usd!r}/{capital_flow_usd!r}); every drawdown "
                    f"comparison against it would silently return False.",
                )

            adjusted_equity = float(current_equity_usd) - float(capital_flow_usd)
            if not _is_finite(adjusted_equity):
                # Reachable by overflow even when both inputs were individually finite.
                # Checked *before* the ratchet: writing an infinite peak would poison the
                # state permanently and halt the strategy on every later evaluation.
                return self._halt_strategy(
                    strategy,
                    f"Flow-adjusted equity overflowed to {adjusted_equity!r} from "
                    f"{current_equity_usd!r} - {capital_flow_usd!r}.",
                )

            if adjusted_equity > strategy.peak_equity_usd:
                strategy.peak_equity_usd = adjusted_equity
            strategy.current_equity_usd = float(current_equity_usd)

            peak = float(strategy.peak_equity_usd)
            if not _is_finite(peak) or peak <= 0.0:
                return self._halt_strategy(
                    strategy,
                    f"peak_equity_usd must be positive and finite (got {peak!r}); the "
                    f"drawdown denominator is undefined.",
                )

            drawdown = max(0.0, (peak - adjusted_equity) / peak * 100.0)
            if not _is_finite(drawdown):
                return self._halt_strategy(
                    strategy,
                    f"Computed drawdown is not finite (peak={peak!r}, adjusted equity="
                    f"{adjusted_equity!r}); the audit record would carry an infinity.",
                )

            is_breach = drawdown >= strategy.drawdown_limit_pct
            portfolio_halted = self.portfolio_state.is_portfolio_tripped
            was_latched = strategy.is_tripped

            newly_tripped = is_breach and not was_latched
            if newly_tripped:
                strategy.is_tripped = True
                strategy.action_taken = action
                strategy.tripped_time_epoch = self._clock()
                strategy.tripped_by_scope = KillSwitchScope.STRATEGY_LEVEL
                strategy.tripped_reason = KillSwitchReason.STRATEGY_DRAWDOWN_BREACH.value

            if newly_tripped:
                reason = KillSwitchReason.STRATEGY_DRAWDOWN_BREACH
            elif strategy.is_tripped:
                reason = KillSwitchReason.LATCHED_PRIOR_TRIP
            elif portfolio_halted:
                reason = KillSwitchReason.PORTFOLIO_HALT_INHERITED
            else:
                reason = KillSwitchReason.NO_BREACH

            cascade_count = len(self._cascade_tripped_ids())
            is_trading_halted = strategy.is_tripped or portfolio_halted

            if newly_tripped:
                notes = (
                    f"STRATEGY KILL SWITCH TRIPPED [{strategy_id}]: Drawdown {drawdown:.2f}% "
                    f">= Limit {strategy.drawdown_limit_pct}%. Action = {action.value}. "
                    f"Only this strategy is halted; siblings continue. Cascade counter now "
                    f"{cascade_count}/{self.portfolio_state.max_tripped_strategies_limit}."
                )
                logger.critical(notes)
            elif strategy.is_tripped:
                notes = (
                    f"STRATEGY STATUS [{strategy_id}]: Drawdown = {drawdown:.2f}% (Limit "
                    f"{strategy.drawdown_limit_pct}%). LATCHED from a prior trip "
                    f"[{strategy.tripped_reason}] by {strategy.tripped_by_scope.value if strategy.tripped_by_scope else 'UNKNOWN'}; "
                    f"human_re_enable() is required even though the drawdown has recovered."
                )
                logger.warning(notes)
            elif portfolio_halted:
                notes = (
                    f"STRATEGY STATUS [{strategy_id}]: Drawdown = {drawdown:.2f}% (Limit "
                    f"{strategy.drawdown_limit_pct}%). Strategy is within its own limit but "
                    f"the MASTER PORTFOLIO kill switch is engaged; trading stays halted."
                )
                logger.warning(notes)
            else:
                notes = (
                    f"STRATEGY STATUS [{strategy_id}]: Drawdown = {drawdown:.2f}% "
                    f"(Limit {strategy.drawdown_limit_pct}%)."
                )
                logger.info(notes)

            return KillSwitchExecutionReport(
                scope=KillSwitchScope.STRATEGY_LEVEL,
                strategy_id=strategy_id,
                is_triggered=strategy.is_tripped,
                drawdown_pct=round(drawdown, 2),
                limit_pct=strategy.drawdown_limit_pct,
                action=action if newly_tripped else KillSwitchAction.NO_ACTION,
                affected_strategies=[strategy_id] if newly_tripped else [],
                audit_notes=notes,
                reason_code=reason.value,
                is_newly_tripped=newly_tripped,
                is_latched=was_latched,
                is_trading_halted=is_trading_halted,
                tripped_strategy_count=cascade_count,
                evaluated_at=_utc_now_iso(),
            )

    def evaluate_portfolio_kill_switch(
        self,
        total_current_equity_usd: float,
        action: KillSwitchAction = KillSwitchAction.HARD_LIQUIDATE,
        capital_flow_usd: float = 0.0,
    ) -> KillSwitchExecutionReport:
        """Evaluates master portfolio drawdown and cascaded strategy failures.

        Two independent triggers: total fund drawdown against
        `portfolio_drawdown_limit_pct`, and a cascade of strategies that each tripped on their
        *own* measured drawdown reaching `max_tripped_strategies_limit`. Fail-closed halts and
        strategies this switch itself fanned out to are excluded from the cascade count.

        On the transition into a trip the switch fans out to every strategy that is not
        already latched. `affected_strategies` lists exactly those - a strategy already
        hard-liquidated by its own trip is not queued for a second liquidation.
        """
        action = self._require_dispatchable_action(action)
        portfolio = self.portfolio_state

        with self._lock:
            if not _is_finite(total_current_equity_usd, capital_flow_usd):
                return self._halt_portfolio(
                    f"total_current_equity_usd/capital_flow_usd is not finite "
                    f"({total_current_equity_usd!r}/{capital_flow_usd!r}); both portfolio "
                    f"triggers would silently evaluate to False."
                )

            adjusted_equity = float(total_current_equity_usd) - float(capital_flow_usd)
            if not _is_finite(adjusted_equity):
                return self._halt_portfolio(
                    f"Flow-adjusted fund equity overflowed to {adjusted_equity!r} from "
                    f"{total_current_equity_usd!r} - {capital_flow_usd!r}."
                )

            if adjusted_equity > portfolio.total_peak_equity_usd:
                portfolio.total_peak_equity_usd = adjusted_equity
            portfolio.total_current_equity_usd = float(total_current_equity_usd)

            peak = float(portfolio.total_peak_equity_usd)
            if not _is_finite(peak) or peak <= 0.0:
                return self._halt_portfolio(
                    f"total_peak_equity_usd must be positive and finite (got {peak!r}); the "
                    f"drawdown denominator is undefined."
                )

            portfolio_dd = max(0.0, (peak - adjusted_equity) / peak * 100.0)
            if not _is_finite(portfolio_dd):
                return self._halt_portfolio(
                    f"Computed fund drawdown is not finite (peak={peak!r}, adjusted equity="
                    f"{adjusted_equity!r}); the audit record would carry an infinity."
                )

            cascade_ids = self._cascade_tripped_ids()
            cascade_count = len(cascade_ids)

            is_dd_breach = portfolio_dd >= portfolio.portfolio_drawdown_limit_pct
            is_cascade_breach = cascade_count >= portfolio.max_tripped_strategies_limit
            is_breach = is_dd_breach or is_cascade_breach
            was_latched = portfolio.is_portfolio_tripped

            newly_tripped = is_breach and not was_latched
            fanned_out: List[str] = []
            if newly_tripped:
                reason = (
                    KillSwitchReason.PORTFOLIO_DRAWDOWN_BREACH if is_dd_breach
                    else KillSwitchReason.CASCADE_BREACH
                )
                portfolio.is_portfolio_tripped = True
                portfolio.tripped_time_epoch = self._clock()
                portfolio.tripped_reason = reason.value
                for strategy in self.strategies.values():
                    # Leave an already-latched strategy alone: overwriting its action and
                    # originating scope destroys the audit record of why it first stopped,
                    # and re-queueing it would dispatch a second liquidation.
                    if strategy.is_tripped:
                        continue
                    strategy.is_tripped = True
                    strategy.action_taken = action
                    strategy.tripped_time_epoch = portfolio.tripped_time_epoch
                    strategy.tripped_by_scope = KillSwitchScope.PORTFOLIO_LEVEL
                    strategy.tripped_reason = reason.value
                    fanned_out.append(strategy.strategy_id)
            elif portfolio.is_portfolio_tripped:
                reason = KillSwitchReason.LATCHED_PRIOR_TRIP
            else:
                reason = KillSwitchReason.NO_BREACH

            if newly_tripped:
                cause = (
                    "Drawdown Limit Breach" if is_dd_breach
                    else f"Cascade Failure ({cascade_count} strategies tripped on their own "
                         f"drawdown: {', '.join(cascade_ids)})"
                )
                notes = (
                    f"MASTER PORTFOLIO KILL SWITCH TRIPPED: Reason = {cause}, Portfolio "
                    f"Drawdown = {portfolio_dd:.2f}% (Limit "
                    f"{portfolio.portfolio_drawdown_limit_pct}%). Action = {action.value}. "
                    f"Halting {len(fanned_out)} of {len(self.strategies)} strategies "
                    f"({len(self.strategies) - len(fanned_out)} already latched)."
                )
                logger.critical(notes)
            elif portfolio.is_portfolio_tripped:
                notes = (
                    f"PORTFOLIO STATUS: Drawdown = {portfolio_dd:.2f}% (Limit "
                    f"{portfolio.portfolio_drawdown_limit_pct}%), Cascade Count = "
                    f"{cascade_count}/{portfolio.max_tripped_strategies_limit}. LATCHED "
                    f"LOCKOUT REMAINS ACTIVE from a prior trip [{portfolio.tripped_reason}]; "
                    f"human_re_enable() is required."
                )
                logger.warning(notes)
            else:
                notes = (
                    f"PORTFOLIO STATUS: Drawdown = {portfolio_dd:.2f}% (Limit "
                    f"{portfolio.portfolio_drawdown_limit_pct}%), Cascade Count = "
                    f"{cascade_count}/{portfolio.max_tripped_strategies_limit}."
                )
                logger.info(notes)

            return KillSwitchExecutionReport(
                scope=KillSwitchScope.PORTFOLIO_LEVEL,
                strategy_id=None,
                is_triggered=portfolio.is_portfolio_tripped,
                drawdown_pct=round(portfolio_dd, 2),
                limit_pct=portfolio.portfolio_drawdown_limit_pct,
                action=action if newly_tripped else KillSwitchAction.NO_ACTION,
                affected_strategies=fanned_out,
                audit_notes=notes,
                reason_code=reason.value,
                is_newly_tripped=newly_tripped,
                is_latched=was_latched,
                is_trading_halted=portfolio.is_portfolio_tripped,
                tripped_strategy_count=cascade_count,
                evaluated_at=_utc_now_iso(),
            )

    # ---------------------------------------------------------------- human re-enable

    def human_re_enable(
        self,
        scope: KillSwitchScope,
        operator_id: str,
        reason: str,
        strategy_id: Optional[str] = None,
    ) -> bool:
        """Clear one latch. Returns True only if the request was granted.

        This clears the *latch*, not the breach. The peak equity that produced the drawdown
        survives, so unless the operator deliberately re-baselines `peak_equity_usd` (or
        `total_peak_equity_usd`) in their own state store, the next evaluation re-trips
        immediately. Never re-baseline automatically - that erases the limit while appearing
        to satisfy it.

        Scope rules, which fix the recovery order as strategies-first, fund-second:

        * `STRATEGY_LEVEL` requires `strategy_id` and clears that one latch. It is permitted
          while the master latch is engaged, and safely so: `is_strategy_trading_halted()`
          still returns True and every report still carries `PORTFOLIO_HALT_INHERITED`, so
          nothing resumes trading until the fund does.
        * `PORTFOLIO_LEVEL` clears the master latch and releases every strategy the master
          switch itself halted. A strategy that tripped on its own drawdown stays halted and
          needs its own re-enable. It is **refused while the cascade condition still holds**,
          because lifting the master latch then would re-trip the fund on the next evaluation.

        Every attempt, granted or refused, is appended to `re_enable_log`.
        """
        with self._lock:
            try:
                scope = KillSwitchScope(scope)
            except (ValueError, TypeError, KeyError):
                # An unhashable value (a list, say) raises TypeError from the enum lookup,
                # not ValueError; letting that escape turns a refused re-enable into a crash
                # in the operator's hands.
                return self._record_re_enable(
                    scope=str(scope), strategy_id=strategy_id, operator_id=operator_id,
                    reason=reason, granted=False,
                    rejection=f"Unknown scope {scope!r}.",
                )

            if not isinstance(operator_id, str) or not operator_id.strip():
                rejection: Optional[str] = "Blank operator identity."
            elif not isinstance(reason, str) or not reason.strip():
                rejection = "Blank re-enable reason."
            elif (self.authorized_operators
                    and operator_id.strip() not in self.authorized_operators):
                rejection = f"Operator {operator_id!r} is not in authorized_operators."
            elif scope is KillSwitchScope.PORTFOLIO_LEVEL:
                rejection = self._reject_portfolio_re_enable()
            else:
                rejection = self._reject_strategy_re_enable(strategy_id)

            if rejection is not None:
                return self._record_re_enable(
                    scope=scope.value, strategy_id=strategy_id, operator_id=operator_id,
                    reason=reason, granted=False, rejection=rejection,
                )

            if scope is KillSwitchScope.PORTFOLIO_LEVEL:
                released = self._clear_portfolio_latch()
            else:
                released = self._clear_strategy_latch(str(strategy_id))

            self._record_re_enable(
                scope=scope.value, strategy_id=strategy_id, operator_id=operator_id,
                reason=reason, granted=True, released=tuple(released),
            )
            logger.critical(
                "KILL SWITCH LATCH CLEARED [%s] by operator=%r, reason=%r. Released: %s. "
                "The breach condition is NOT cleared - re-baseline the peak equity "
                "deliberately or the next evaluation re-trips.",
                scope.value, operator_id, reason, released or "none",
            )
            return True

    # ---------------------------------------------------------------- internals

    def _require_strategy(self, strategy_id: str) -> StrategyState:
        strategy = self.strategies.get(strategy_id)
        if strategy is None:
            raise ValueError(f"Strategy '{strategy_id}' not found.")
        return strategy

    @staticmethod
    def _require_dispatchable_action(action: object) -> KillSwitchAction:
        """Rejects an action that cannot stop anything, so a mistyped call cannot look like
        a successful kill."""
        try:
            resolved = KillSwitchAction(action)
        except (ValueError, TypeError, KeyError) as exc:
            # TypeError covers an unhashable value; both must surface as one ValueError so a
            # caller cannot distinguish "mistyped" from "crashed" and retry blindly.
            raise ValueError(f"Unknown KillSwitchAction {action!r}.") from exc
        if resolved is KillSwitchAction.NO_ACTION:
            raise ValueError(
                "action=NO_ACTION would trip a kill switch that halts nothing. Pass "
                "SOFT_HALT or HARD_LIQUIDATE."
            )
        return resolved

    def _cascade_tripped_ids(self) -> List[str]:
        """Strategies latched by their own measured drawdown, in registration order."""
        return [
            s.strategy_id for s in self.strategies.values()
            if s.is_tripped
            and s.tripped_by_scope is KillSwitchScope.STRATEGY_LEVEL
            and s.tripped_reason in _CASCADE_ELIGIBLE_REASONS
        ]

    def _halt_strategy(
        self, strategy: StrategyState, detail: str
    ) -> KillSwitchExecutionReport:
        """Latch a fail-closed strategy halt. Blocks new risk; deliberately does not liquidate
        and deliberately does not advance the cascade counter."""
        was_latched = strategy.is_tripped
        if not was_latched:
            strategy.is_tripped = True
            strategy.action_taken = KillSwitchAction.SOFT_HALT
            strategy.tripped_time_epoch = self._clock()
            strategy.tripped_by_scope = KillSwitchScope.STRATEGY_LEVEL
            strategy.tripped_reason = KillSwitchReason.HALTED_INVALID_INPUT.value

        notes = (
            f"FAIL-CLOSED STRATEGY HALT [{strategy.strategy_id}]: {detail} Trading halted, "
            f"nothing liquidated (the engine will not market-flatten on data it could not "
            f"evaluate) and the cascade counter is not advanced. Escalate to a human."
        )
        logger.critical(notes)
        return KillSwitchExecutionReport(
            scope=KillSwitchScope.STRATEGY_LEVEL,
            strategy_id=strategy.strategy_id,
            is_triggered=True,
            drawdown_pct=0.0,
            limit_pct=strategy.drawdown_limit_pct,
            action=KillSwitchAction.NO_ACTION,
            affected_strategies=[],
            audit_notes=notes,
            reason_code=KillSwitchReason.HALTED_INVALID_INPUT.value,
            is_newly_tripped=False,
            is_latched=was_latched,
            is_trading_halted=True,
            tripped_strategy_count=len(self._cascade_tripped_ids()),
            evaluated_at=_utc_now_iso(),
        )

    def _halt_portfolio(self, detail: str) -> KillSwitchExecutionReport:
        """Latch a fail-closed portfolio halt. Blocks new risk fund-wide; liquidates nothing
        and fans out to no strategy."""
        portfolio = self.portfolio_state
        was_latched = portfolio.is_portfolio_tripped
        if not was_latched:
            portfolio.is_portfolio_tripped = True
            portfolio.tripped_time_epoch = self._clock()
            portfolio.tripped_reason = KillSwitchReason.HALTED_INVALID_INPUT.value

        notes = (
            f"FAIL-CLOSED PORTFOLIO HALT: {detail} Fund-wide trading halted, nothing "
            f"liquidated (the engine will not market-flatten on data it could not evaluate). "
            f"Escalate to a human."
        )
        logger.critical(notes)
        return KillSwitchExecutionReport(
            scope=KillSwitchScope.PORTFOLIO_LEVEL,
            strategy_id=None,
            is_triggered=True,
            drawdown_pct=0.0,
            limit_pct=portfolio.portfolio_drawdown_limit_pct,
            action=KillSwitchAction.NO_ACTION,
            affected_strategies=[],
            audit_notes=notes,
            reason_code=KillSwitchReason.HALTED_INVALID_INPUT.value,
            is_newly_tripped=False,
            is_latched=was_latched,
            is_trading_halted=True,
            tripped_strategy_count=len(self._cascade_tripped_ids()),
            evaluated_at=_utc_now_iso(),
        )

    def _cooldown_remaining_s(self, tripped_time_epoch: float) -> float:
        if self.cooldown_seconds <= 0.0 or not _is_finite(tripped_time_epoch):
            return 0.0
        elapsed = float(self._clock()) - float(tripped_time_epoch)
        return max(0.0, self.cooldown_seconds - elapsed)

    def _reject_portfolio_re_enable(self) -> Optional[str]:
        portfolio = self.portfolio_state
        if not portfolio.is_portfolio_tripped:
            return "Master portfolio kill switch is not tripped; nothing to re-enable."
        # Lifting the master latch while the cascade condition still holds re-trips the fund
        # on the very next evaluation. Force the strategy latches to be dealt with first -
        # that ordering is safe because clearing a strategy latch cannot resume trading while
        # the master latch is still engaged.
        cascade_ids = self._cascade_tripped_ids()
        if len(cascade_ids) >= portfolio.max_tripped_strategies_limit:
            return (
                f"The cascade condition still holds ({len(cascade_ids)} >= "
                f"{portfolio.max_tripped_strategies_limit}: {', '.join(cascade_ids)}). "
                f"Re-enable those strategies first, or the master switch re-trips on the "
                f"next evaluation."
            )
        remaining = self._cooldown_remaining_s(portfolio.tripped_time_epoch)
        if remaining > 0.0:
            return (
                f"Cooldown not elapsed: {remaining:.0f}s of {self.cooldown_seconds:.0f}s "
                f"remain before the master latch may be cleared."
            )
        return None

    def _reject_strategy_re_enable(self, strategy_id: Optional[str]) -> Optional[str]:
        if not strategy_id:
            return "STRATEGY_LEVEL re-enable requires a strategy_id."
        strategy = self.strategies.get(strategy_id)
        if strategy is None:
            return f"Strategy {strategy_id!r} not found."
        if not strategy.is_tripped:
            return f"Strategy {strategy_id!r} is not tripped; nothing to re-enable."
        remaining = self._cooldown_remaining_s(strategy.tripped_time_epoch)
        if remaining > 0.0:
            return (
                f"Cooldown not elapsed: {remaining:.0f}s of {self.cooldown_seconds:.0f}s "
                f"remain before {strategy_id!r} may be cleared."
            )
        return None

    def _clear_portfolio_latch(self) -> List[str]:
        """Clears the master latch plus every strategy the master switch itself halted."""
        portfolio = self.portfolio_state
        portfolio.is_portfolio_tripped = False
        portfolio.tripped_reason = None
        portfolio.tripped_time_epoch = 0.0

        released: List[str] = []
        for strategy in self.strategies.values():
            if strategy.is_tripped and strategy.tripped_by_scope is KillSwitchScope.PORTFOLIO_LEVEL:
                self._reset_strategy(strategy)
                released.append(strategy.strategy_id)
        return released

    def _clear_strategy_latch(self, strategy_id: str) -> List[str]:
        self._reset_strategy(self.strategies[strategy_id])
        return [strategy_id]

    @staticmethod
    def _reset_strategy(strategy: StrategyState) -> None:
        strategy.is_tripped = False
        strategy.action_taken = None
        strategy.tripped_time_epoch = 0.0
        strategy.tripped_by_scope = None
        strategy.tripped_reason = None

    def _record_re_enable(
        self,
        scope: str,
        strategy_id: Optional[str],
        operator_id: object,
        reason: object,
        granted: bool,
        released: Tuple[str, ...] = (),
        rejection: Optional[str] = None,
    ) -> bool:
        self.re_enable_log.append(ReEnableEventLog(
            timestamp=_utc_now_iso(),
            scope=scope,
            strategy_id=strategy_id,
            operator_id=operator_id if isinstance(operator_id, str) else repr(operator_id),
            reason=reason if isinstance(reason, str) else repr(reason),
            granted=granted,
            released_strategies=released,
            rejection_reason=rejection,
        ))
        if not granted:
            logger.warning(
                "KILL SWITCH RE-ENABLE REFUSED [%s/%s] (operator=%r): %s",
                scope, strategy_id, operator_id, rejection,
            )
        return granted
