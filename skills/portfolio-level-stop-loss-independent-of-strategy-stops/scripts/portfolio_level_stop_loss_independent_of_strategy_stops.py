"""
portfolio-level-stop-loss-independent-of-strategy-stops: an independent portfolio-level
stop-loss engine that aggregates NAV across every sub-strategy, evaluates daily and
peak-to-trough drawdown against pre-set limits, and latches a trading lockout that only a
human can clear.

Design invariants (see SKILL.md "Common Pitfalls" for the rationale behind each):

* **Fail closed, never fail open.** A `NaN` mark, an `Inf` cash balance or a non-positive
  equity baseline halts trading (`HALTED_INVALID_INPUT`) instead of silently returning a
  healthy report. Every threshold comparison against `NaN` is false, so an unchecked
  non-finite input turns both drawdown limits off at once with no outward signal.
* **A halt on unevaluable data does not auto-liquidate.** `HALTED_INVALID_INPUT` and
  `HALTED_STALE_PRICES` block new risk but leave `positions_to_flatten_count == 0`: the
  engine cannot know the portfolio is actually down, and market-flattening a book on a bad
  tick is itself a loss event. Only a *computed* drawdown breach flattens.
* **The lockout latches.** A breach sets a latch that survives NAV recovery, a flat book and
  a new trading day. Recomputing the lock from scratch on every call would silently
  re-enable trading the moment the next day's start-of-day equity reset the daily drawdown
  to zero.
* **Re-enabling clears the latch, not the breach.** `human_re_enable()` requires a non-blank
  operator and reason, is refused for an unlisted operator when `authorized_operators` is
  configured, and is appended to `re_enable_log` whether granted or refused. If the
  portfolio state still breaches, the next evaluation re-latches.
* **NAV valuation is explicit.** A cash-funded equity book values as cash + market value; a
  margined derivatives book values as cash + unrealized P&L, because `quantity * price` is
  notional there, not equity. Guessing wrong inflates NAV and disables the stop entirely.
* **State transitions are atomic.** A re-entrant lock guards the latch so a strategy loop, a
  risk poller and an operator endpoint cannot race it.

Scope limits (deliberately *not* handled here):

* No FX conversion. All cash, prices and equity baselines must already be expressed in one
  reporting currency - see `multi-currency-pnl-and-fx-conversion`.
* No order gating. This engine decides *whether* the portfolio must stop; enforcing
  reduce-only order flow while locked belongs to
  `kill-switch-and-drawdown-circuit-breakers`.
"""
import datetime
import logging
import math
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


class PortfolioStopStatus(str, Enum):
    """Machine-readable outcome of one portfolio stop evaluation.

    Subclasses `str`, so callers comparing `report.status` against the plain string literals
    (e.g. `"DAILY_DRAWDOWN_BREACH_FLATTEN"`) keep working unchanged.
    """

    HEALTHY = "PORTFOLIO_NAV_HEALTHY"
    DAILY_BREACH = "DAILY_DRAWDOWN_BREACH_FLATTEN"
    PEAK_BREACH = "PEAK_DRAWDOWN_BREACH_FLATTEN"
    ENGINE_DISABLED = "ENGINE_DISABLED"
    HALTED_INVALID_INPUT = "HALTED_INVALID_INPUT"
    HALTED_STALE_PRICES = "HALTED_STALE_PRICES"


class NavValuationMode(str, Enum):
    """How position value contributes to portfolio NAV.

    `CASH_PLUS_MARKET_VALUE` - cash-funded (equity/spot) books, where buying moved cash out
    and the position's market value replaces it.

    `CASH_PLUS_UNREALIZED_PNL` - margined books (futures, CFDs, perpetual swaps), where
    `quantity * current_price` is *notional exposure*, not equity. Adding notional to cash
    reports a leveraged book as enormously profitable, and the stop then never fires.
    """

    CASH_PLUS_MARKET_VALUE = "CASH_PLUS_MARKET_VALUE"
    CASH_PLUS_UNREALIZED_PNL = "CASH_PLUS_UNREALIZED_PNL"


def _is_finite(*values: object) -> bool:
    """True only if every value is a real, finite number (rejects NaN, +/-Inf, non-numerics)."""
    for value in values:
        try:
            if not math.isfinite(float(value)):  # type: ignore[arg-type]
                return False
        except (TypeError, ValueError):
            return False
    return True


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass
class PortfolioLevelStopLossIndependentOfStrategyStopsConfig:
    """Risk policy for the independent portfolio stop.

    The drawdown limits are **fractions**, not percentage points: `0.05` is 5%. They are
    *your* risk policy - no rule surveyed in `references/standards.md` mandates a drawdown
    number for a trading firm. `__post_init__` rejects out-of-range values because a limit
    passed as `5` (meaning "5%") reads as 500% and silently disables the breaker for the life
    of the process.
    """

    enabled: bool = True
    max_daily_drawdown_pct: float = 0.05   # 5% of start-of-day equity
    max_peak_drawdown_pct: float = 0.10    # 10% of the equity high-water mark
    auto_flatten_on_breach: bool = True
    nav_valuation_mode: NavValuationMode = NavValuationMode.CASH_PLUS_MARKET_VALUE
    # None disables the staleness gate. Set it whenever marks arrive from a feed that can go
    # quiet: an engine re-evaluating yesterday's prices reports healthy forever.
    max_price_staleness_s: Optional[float] = None
    # Empty tuple = any non-blank operator identity may re-enable (still audited).
    authorized_operators: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("max_daily_drawdown_pct", "max_peak_drawdown_pct"):
            value = getattr(self, field_name)
            if not _is_finite(value):
                raise ValueError(f"{field_name} must be a finite number, got {value!r}")
            if not 0.0 < float(value) <= 1.0:
                raise ValueError(
                    f"{field_name} must be a fraction in (0, 1] - 0.05 means 5%, not 5. "
                    f"Got {value!r}."
                )
        self.nav_valuation_mode = NavValuationMode(self.nav_valuation_mode)
        if self.max_price_staleness_s is not None:
            if (not _is_finite(self.max_price_staleness_s)
                    or float(self.max_price_staleness_s) <= 0.0):
                raise ValueError(
                    f"max_price_staleness_s must be a positive number or None, got "
                    f"{self.max_price_staleness_s!r}"
                )
        self.authorized_operators = tuple(self.authorized_operators)


@dataclass
class StrategyPosition:
    """One sub-strategy's open position, marked to market.

    `unrealized_pnl` is read only under `NavValuationMode.CASH_PLUS_UNREALIZED_PNL`;
    `quantity * current_price` is read only under `CASH_PLUS_MARKET_VALUE`. Populate the one
    your account type actually implies - the engine will not silently substitute the other.
    """

    strategy_id: str
    symbol: str
    quantity: float
    current_price: float
    unrealized_pnl: float
    # Epoch seconds at which `current_price` was observed. Required only when
    # `max_price_staleness_s` is configured.
    price_epoch_s: Optional[float] = None


@dataclass
class PortfolioState:
    """Portfolio snapshot for one evaluation.

    Capital flows are why a healthy book gets liquidated by a naive drawdown check: a settled
    withdrawal lowers NAV without being a loss. Report flows here (positive for deposits,
    negative for withdrawals) and they are removed from NAV before drawdown is measured.
    `capital_flow_since_peak` equals `capital_flow_since_sod` when the high-water mark was set
    today, and covers a longer window when it was set on an earlier day.

    All monetary values must be in a single reporting currency, and should come from the
    broker/custodian's account state rather than the bot's internal bookkeeping - MiFID II
    RTS 6 Art. 17(3) frames that reconciliation duty for EU investment firms.
    """

    start_of_day_equity: float
    peak_equity: float
    current_cash: float
    open_positions: List[StrategyPosition] = field(default_factory=list)
    capital_flow_since_sod: float = 0.0
    capital_flow_since_peak: float = 0.0
    # Evaluation timestamp (epoch seconds). Required only when `max_price_staleness_s` is set.
    as_of_epoch_s: Optional[float] = None


@dataclass
class PortfolioStopReport:
    """Structured, auditable outcome of one evaluation.

    `daily_drawdown_pct` / `peak_drawdown_pct` are rounded for readability, but the breach
    flags are decided on the unrounded values: a reported `0.05` with `is_daily_breached`
    False means the true drawdown sat just below the limit.
    """

    current_nav: float
    daily_drawdown_pct: float
    peak_drawdown_pct: float
    is_daily_breached: bool
    is_peak_breached: bool
    is_trading_locked: bool
    positions_to_flatten_count: int
    status: str                          # one of the PortfolioStopStatus values
    audit_notes: str
    # NAV after removing capital flows - the figure daily drawdown was measured on.
    nav_for_drawdown: float = 0.0
    # True when the lock comes from a previously latched breach, not from this evaluation.
    is_latched: bool = False
    evaluated_at: Optional[str] = None


@dataclass
class ReEnableEventLog:
    """Audit record for every operator attempt to clear the lockout, granted or refused."""

    timestamp: str
    operator_id: str
    reason: str
    cleared_status: Optional[str]
    granted: bool
    rejection_reason: Optional[str] = None


class PortfolioLevelStopLossIndependentOfStrategyStops:
    """
    Independent portfolio-level stop-loss engine monitoring aggregated daily and peak-to-trough
    drawdowns, triggering emergency global position flattening independent of sub-strategy stops.

    The engine is deliberately not a strategy component: it consumes an externally supplied
    `PortfolioState` and owns exactly one piece of mutable state - the latched lockout - so a
    bug in any strategy cannot disable it.
    """

    def __init__(
        self,
        config: Optional[PortfolioLevelStopLossIndependentOfStrategyStopsConfig] = None
    ):
        self.config = config or PortfolioLevelStopLossIndependentOfStrategyStopsConfig()
        self._lock = threading.RLock()
        self._latched_status: Optional[PortfolioStopStatus] = None
        self.re_enable_log: List[ReEnableEventLog] = []

    # ---------------------------------------------------------------- latch state

    @property
    def is_trading_locked(self) -> bool:
        """True while a breach or fail-closed halt is latched and un-cleared."""
        with self._lock:
            return self._latched_status is not None

    @property
    def latched_status(self) -> Optional[str]:
        """The status that latched the lockout, or None if the engine is not locked."""
        with self._lock:
            return self._latched_status.value if self._latched_status else None

    def human_re_enable(self, operator_id: str, reason: str) -> bool:
        """Clear the latched lockout. Returns True only if the request was granted.

        This clears the *latch*, not the breach: if the state passed to the next
        `evaluate_portfolio_stop()` still exceeds a limit, the engine re-latches immediately.
        After a peak-drawdown halt that is the expected outcome - resuming requires the
        operator to deliberately re-baseline `peak_equity` in their own state store, which is
        a risk decision this engine will not make for them.
        """
        with self._lock:
            cleared = self._latched_status.value if self._latched_status else None
            rejection: Optional[str] = None

            if not isinstance(operator_id, str) or not operator_id.strip():
                rejection = "Blank operator identity."
            elif not isinstance(reason, str) or not reason.strip():
                rejection = "Blank re-enable reason."
            elif (self.config.authorized_operators
                    and operator_id.strip() not in self.config.authorized_operators):
                rejection = f"Operator {operator_id!r} is not in authorized_operators."
            elif self._latched_status is None:
                rejection = "Engine is not locked; nothing to re-enable."

            granted = rejection is None
            self.re_enable_log.append(ReEnableEventLog(
                timestamp=_utc_now_iso(),
                operator_id=operator_id if isinstance(operator_id, str) else repr(operator_id),
                reason=reason if isinstance(reason, str) else repr(reason),
                cleared_status=cleared,
                granted=granted,
                rejection_reason=rejection,
            ))

            if not granted:
                logger.warning(
                    "PORTFOLIO STOP RE-ENABLE REFUSED (operator=%r, latched=%s): %s",
                    operator_id, cleared, rejection,
                )
                return False

            self._latched_status = None
            logger.critical(
                "PORTFOLIO STOP LOCKOUT CLEARED by operator=%r (was %s), reason=%r. Breach "
                "conditions are NOT cleared; the next evaluation re-latches if the portfolio "
                "still breaches.",
                operator_id, cleared, reason,
            )
            return True

    # ---------------------------------------------------------------- legacy shim

    def execute(self) -> bool:
        """
        Legacy execution method for backward compatibility. Reports only whether the engine is
        configured on - it is not a risk check and must never be used as one.
        """
        return self.config.enabled

    # ---------------------------------------------------------------- evaluation

    def evaluate_portfolio_stop(
        self, state: PortfolioState
    ) -> PortfolioStopReport:
        """
        Evaluates portfolio NAV, daily drawdown %, and peak drawdown % to enforce independent
        stop-loss limits, latching a trading lockout on any breach or unevaluable input.
        """
        if not self.config.enabled:
            # A disabled engine says so rather than reporting "healthy" - an explicit
            # ENGINE_DISABLED is what a reviewer needs to find in the audit trail.
            return self._report(
                status=PortfolioStopStatus.ENGINE_DISABLED,
                current_nav=state.current_cash if _is_finite(state.current_cash) else 0.0,
                nav_for_drawdown=0.0,
                daily_dd_pct=0.0,
                peak_dd_pct=0.0,
                is_daily_breached=False,
                is_peak_breached=False,
                flatten_count=0,
                is_latched=False,
                notes="Engine is disabled; no portfolio stop-loss protection is active.",
                is_trading_locked=False,
            )

        # --- Fail closed on anything that cannot be evaluated ------------------------
        invalid = self._validate_state(state)
        if invalid is not None:
            return self._halt(PortfolioStopStatus.HALTED_INVALID_INPUT, invalid)

        stale = self._stale_price_reason(state)
        if stale is not None:
            return self._halt(PortfolioStopStatus.HALTED_STALE_PRICES, stale)

        # --- Aggregated portfolio NAV ------------------------------------------------
        if self.config.nav_valuation_mode is NavValuationMode.CASH_PLUS_UNREALIZED_PNL:
            position_component = sum(p.unrealized_pnl for p in state.open_positions)
        else:
            position_component = sum(p.quantity * p.current_price for p in state.open_positions)
        current_nav = state.current_cash + position_component

        if not _is_finite(current_nav):
            # Reachable through overflow even when every input was individually finite.
            return self._halt(
                PortfolioStopStatus.HALTED_INVALID_INPUT,
                "Aggregated NAV is not finite (overflow in position valuation).",
            )

        # --- Drawdowns, measured on NAV net of capital flows --------------------------
        # A settled deposit raises NAV without being a gain; a withdrawal lowers it without
        # being a loss. Removing the flow keeps the comparison like-for-like against the
        # baseline equity that was captured before the flow occurred.
        nav_vs_sod = current_nav - state.capital_flow_since_sod
        nav_vs_peak = current_nav - state.capital_flow_since_peak

        sod_eq = float(state.start_of_day_equity)
        daily_dd_pct = max(0.0, (sod_eq - nav_vs_sod) / sod_eq)

        # The high-water mark cannot sit below the day's opening equity; if the caller's peak
        # store lags, fall back to start-of-day equity so the peak limit is never loosened.
        peak_eq = max(sod_eq, float(state.peak_equity))
        peak_dd_pct = max(0.0, (peak_eq - nav_vs_peak) / peak_eq)

        is_daily_breached = daily_dd_pct >= self.config.max_daily_drawdown_pct
        is_peak_breached = peak_dd_pct >= self.config.max_peak_drawdown_pct

        if is_daily_breached:
            status = PortfolioStopStatus.DAILY_BREACH
        elif is_peak_breached:
            status = PortfolioStopStatus.PEAK_BREACH
        else:
            status = PortfolioStopStatus.HEALTHY

        notes = (
            f"INDEPENDENT PORTFOLIO STOP EVALUATION [{status.value}]: Current NAV = "
            f"${current_nav:,.2f} (mode={self.config.nav_valuation_mode.value}; NAV net of "
            f"capital flows: daily=${nav_vs_sod:,.2f}, peak=${nav_vs_peak:,.2f}; SOD Equity = "
            f"${sod_eq:,.2f}, Peak Equity = ${peak_eq:,.2f}). "
            f"Daily DD = {daily_dd_pct:.2%} (Limit = {self.config.max_daily_drawdown_pct:.2%}), "
            f"Peak DD = {peak_dd_pct:.2%} (Limit = {self.config.max_peak_drawdown_pct:.2%})."
        )

        with self._lock:
            was_latched = self._latched_status is not None
            breach_latched = self._latched_status in (
                PortfolioStopStatus.DAILY_BREACH, PortfolioStopStatus.PEAK_BREACH
            )
            newly_breached = False
            if status in (PortfolioStopStatus.DAILY_BREACH, PortfolioStopStatus.PEAK_BREACH):
                if not breach_latched:
                    # A fail-closed halt already blocked new risk but issued no flatten,
                    # because the engine could not tell whether the book was actually down.
                    # Now that it can, the breach supersedes the halt and the liquidation
                    # must still be requested.
                    self._latched_status = status
                    newly_breached = True
            elif was_latched:
                # NAV recovered, the book was flattened, or a new day reset the daily
                # baseline - none of which is authority to resume trading.
                notes = (
                    f"{notes} LATCHED LOCKOUT REMAINS ACTIVE from prior breach "
                    f"[{self._latched_status.value}]; human_re_enable() is required."
                )
            effective_status = self._latched_status or status
            is_trading_locked = self._latched_status is not None

        flatten_count = (
            len(state.open_positions)
            if (newly_breached and self.config.auto_flatten_on_breach)
            else 0
        )
        notes = (
            f"{notes} Trading Lockout = {is_trading_locked}, "
            f"Positions to Flatten = {flatten_count}."
        )

        if newly_breached:
            logger.critical("EMERGENCY PORTFOLIO STOP LOSS TRIGGERED: %s", notes)
        elif is_trading_locked:
            logger.warning("PORTFOLIO STOP LOCKOUT STILL LATCHED: %s", notes)
        else:
            logger.info("%s", notes)

        return self._report(
            status=effective_status,
            current_nav=current_nav,
            nav_for_drawdown=nav_vs_sod,
            daily_dd_pct=daily_dd_pct,
            peak_dd_pct=peak_dd_pct,
            is_daily_breached=is_daily_breached,
            is_peak_breached=is_peak_breached,
            flatten_count=flatten_count,
            is_latched=was_latched and not newly_breached,
            notes=notes,
            is_trading_locked=is_trading_locked,
        )

    # ---------------------------------------------------------------- internals

    def _validate_state(self, state: PortfolioState) -> Optional[str]:
        """Returns a human-readable reason the state cannot be evaluated, or None."""
        if not _is_finite(state.current_cash):
            return f"current_cash is not finite ({state.current_cash!r})."
        if not _is_finite(state.start_of_day_equity) or float(state.start_of_day_equity) <= 0.0:
            return (
                f"start_of_day_equity must be a positive, finite number (got "
                f"{state.start_of_day_equity!r}); the daily drawdown denominator is undefined "
                f"otherwise."
            )
        if not _is_finite(state.peak_equity) or float(state.peak_equity) <= 0.0:
            return f"peak_equity must be a positive, finite number (got {state.peak_equity!r})."
        if not _is_finite(state.capital_flow_since_sod, state.capital_flow_since_peak):
            return "capital_flow_since_sod / capital_flow_since_peak must be finite."
        for position in state.open_positions:
            if not _is_finite(position.quantity, position.current_price, position.unrealized_pnl):
                return (
                    f"Position {position.strategy_id}/{position.symbol} carries a non-finite "
                    f"quantity/current_price/unrealized_pnl; NAV cannot be evaluated."
                )
        return None

    def _stale_price_reason(self, state: PortfolioState) -> Optional[str]:
        """Returns a reason the marks are too old to trust, or None if the gate is off/passing."""
        limit = self.config.max_price_staleness_s
        if limit is None or not state.open_positions:
            return None
        if not _is_finite(state.as_of_epoch_s):
            return (
                "max_price_staleness_s is configured but state.as_of_epoch_s is missing or "
                "non-finite; price age cannot be established."
            )
        for position in state.open_positions:
            if not _is_finite(position.price_epoch_s):
                return (
                    f"Position {position.strategy_id}/{position.symbol} has no usable "
                    f"price_epoch_s while the staleness gate is enabled."
                )
            age_s = float(state.as_of_epoch_s) - float(position.price_epoch_s)
            if age_s > float(limit):
                return (
                    f"Position {position.strategy_id}/{position.symbol} mark is {age_s:.1f}s "
                    f"old (limit {float(limit):.1f}s); a stale mark hides a live drawdown."
                )
        return None

    def _halt(self, status: PortfolioStopStatus, reason: str) -> PortfolioStopReport:
        """Latch a fail-closed halt. Blocks new risk; deliberately does not auto-flatten."""
        with self._lock:
            was_latched = self._latched_status is not None
            if not was_latched:
                self._latched_status = status
            effective_status = self._latched_status or status

        notes = (
            f"FAIL-CLOSED PORTFOLIO STOP HALT [{effective_status.value}]: {reason} Trading "
            f"Lockout = True, Positions to Flatten = 0 (the engine will not market-flatten on "
            f"data it cannot evaluate; escalate to a human)."
        )
        logger.critical("%s", notes)
        return self._report(
            status=effective_status,
            current_nav=0.0,
            nav_for_drawdown=0.0,
            daily_dd_pct=0.0,
            peak_dd_pct=0.0,
            is_daily_breached=False,
            is_peak_breached=False,
            flatten_count=0,
            is_latched=was_latched,
            notes=notes,
            is_trading_locked=True,
        )

    def _report(
        self,
        status: PortfolioStopStatus,
        current_nav: float,
        nav_for_drawdown: float,
        daily_dd_pct: float,
        peak_dd_pct: float,
        is_daily_breached: bool,
        is_peak_breached: bool,
        flatten_count: int,
        is_latched: bool,
        notes: str,
        is_trading_locked: bool,
    ) -> PortfolioStopReport:
        return PortfolioStopReport(
            current_nav=round(current_nav, 2),
            daily_drawdown_pct=round(daily_dd_pct, 6),
            peak_drawdown_pct=round(peak_dd_pct, 6),
            is_daily_breached=is_daily_breached,
            is_peak_breached=is_peak_breached,
            is_trading_locked=is_trading_locked,
            positions_to_flatten_count=flatten_count,
            status=status.value,
            audit_notes=notes,
            nav_for_drawdown=round(nav_for_drawdown, 2),
            is_latched=is_latched,
            evaluated_at=_utc_now_iso(),
        )
