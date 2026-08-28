"""US day-trading margin compliance: legacy pattern-day-trader tracking plus the
intraday margin standard that replaced it.

**Rule currency (verify before relying on this module).** The SEC approved
SR-FINRA-2025-017 on 14 April 2026 (Release 34-105226). Effective **4 June
2026** it deletes FINRA Rule 4210(f)(8)(B) in its entirety -- the "day trading"
and "pattern day trader" definitions, the four-day-trades-in-five-business-days
count, the 6 percent de minimis test, the $25,000 minimum equity requirement and
day-trading buying power all cease to be FINRA requirements. Rule 4210(d)(2)
(intraday margin) replaces them.

The day-trade counting in this module therefore models a **broker house
policy**, not a live FINRA mandate. It remains operationally relevant because:

* FINRA permits members "for an interim period to continue to apply the current
  day trading margin requirements where they deem appropriate -- for example, by
  account" while they prepare to implement the new provisions. That phase-in
  runs to **20 October 2027** (Regulatory Notice 26-10).
* Rule 4210(d)(1) lets a member formulate its own, stricter house margin
  requirements at any time, with no end date.

So the gate below is driven by a :class:`DayTradePolicy` carrying its own source
and as-of date, and every decision reports whether that policy has been
confirmed against the broker. It never asserts that a count-based restriction is
currently required by FINRA.

Sources
-------
* Rule text, including the deleted paragraphs: SR-FINRA-2025-017, Exhibit 5
  https://www.finra.org/sites/default/files/2025-12/SR-FINRA-2025-017.pdf
* Effective date and phase-in: FINRA Regulatory Notice 26-10
  https://www.finra.org/rules-guidance/notices/26-10
"""
from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:  # Python 3.9+ standard library; no third-party dependency.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
# Library convention: silent unless the host application configures logging. The
# same warnings are always returned structurally on the decision object, which
# is the programmatic contract.
logger.addHandler(logging.NullHandler())

# --- Legacy FINRA Rule 4210(f)(8)(B) parameters (deleted 2026-06-04) --------
# Retained as the default *house policy* parameters because brokers still
# applying day-trading requirements during the phase-in apply these numbers.
PDT_EQUITY_THRESHOLD = 25_000.0
PDT_TRADE_LIMIT = 4  # the 4th day trade in the window was the designation trigger
PDT_WINDOW_BUSINESS_DAYS = 5
PDT_DE_MINIMIS_TRADE_FRACTION = 0.06  # former (f)(8)(B)(ii) 6 percent test

# --- Rule-change milestones ------------------------------------------------
PDT_RULE_DELETED_EFFECTIVE = datetime.date(2026, 6, 4)
PDT_PHASE_IN_END = datetime.date(2027, 10, 20)

# --- Rule 4210(d)(2) intraday margin parameters (effective 2026-06-04) ------
INTRADAY_DEFICIT_PROMPT_BUSINESS_DAYS = 5   # (d)(2)(D) 90-day freeze trigger
INTRADAY_DEFICIT_EXPIRY_BUSINESS_DAYS = 15  # (d)(2)(C)(iii)
INTRADAY_DEFICIT_FREEZE_CALENDAR_DAYS = 90  # (d)(2)(D)
DE_MINIMIS_DEFICIT_EQUITY_FRACTION = 0.05   # (d)(2)(D)(i): lesser of 5% ...
DE_MINIMIS_DEFICIT_CAP = 1_000.0            # (d)(2)(D)(i): ... or $1,000

DEFAULT_MARKET_TIMEZONE = "America/New_York"

# Fractional-share quantities do not subtract exactly in binary floating point.
# Without a tolerance a lot can survive its own closing execution as a ~1e-17
# residue and then be "closed" again by the next execution, inventing a day
# trade on an already-flat position.
QUANTITY_EPSILON = 1e-9

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
_BUY_SIDES = frozenset({"BUY", "B", "BUY_TO_OPEN", "BUY_TO_CLOSE", "BUY_TO_COVER", "BTO", "BTC"})
_SELL_SIDES = frozenset({"SELL", "S", "SELL_SHORT", "SELL_TO_OPEN", "SELL_TO_CLOSE", "STO", "STC", "SS"})


class PDTComplianceError(ValueError):
    """Base class for every error this module raises."""


class PDTInputError(PDTComplianceError):
    """An execution, date or equity value was malformed or unusable."""


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DayTradePolicy:
    """A *broker* day-trade policy, with provenance.

    ``confirmed_with_broker`` is deliberately tri-state:

    * ``True``  -- the broker was asked and still applies a count-based policy.
    * ``False`` -- the broker was asked and has migrated to intraday margin; the
      gate then allows every day trade and says why.
    * ``None``  -- nobody has checked. Decisions carry a warning and the gate
      still blocks, because failing open on an unverified compliance control is
      the worse error.
    """

    name: str
    source: str
    source_as_of: str
    equity_threshold: float = PDT_EQUITY_THRESHOLD
    max_day_trades_in_window: int = PDT_TRADE_LIMIT
    window_business_days: int = PDT_WINDOW_BUSINESS_DAYS
    # The former 6 percent test is an *exemption*: enabling it permits trades
    # that would otherwise be blocked. Brokers retaining a house policy do not
    # uniformly retain the exemption, so it is opt-in and off by default.
    apply_de_minimis_exemption: bool = False
    de_minimis_trade_fraction: float = PDT_DE_MINIMIS_TRADE_FRACTION
    confirmed_with_broker: Optional[bool] = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.equity_threshold) or self.equity_threshold < 0:
            raise PDTInputError(
                f"equity_threshold must be finite and >= 0, got {self.equity_threshold!r}")
        if self.max_day_trades_in_window < 1:
            raise PDTInputError(
                f"max_day_trades_in_window must be >= 1, got {self.max_day_trades_in_window!r}")
        if self.window_business_days < 1:
            raise PDTInputError(
                f"window_business_days must be >= 1, got {self.window_business_days!r}")
        if not 0.0 <= self.de_minimis_trade_fraction <= 1.0:
            raise PDTInputError(
                f"de_minimis_trade_fraction must be in [0, 1], got {self.de_minimis_trade_fraction!r}")


#: Parameters of the deleted FINRA rule, as brokers applied them before
#: 2026-06-04 and as some continue to apply them during the phase-in.
LEGACY_FINRA_PDT_POLICY = DayTradePolicy(
    name="legacy-finra-4210-f8B",
    source=(
        "FINRA Rule 4210(f)(8)(B) as in force until 2026-06-04; deleted by "
        "SR-FINRA-2025-017 (SEC Release 34-105226, 2026-04-14)"
    ),
    source_as_of="2026-06-04",
)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeExecution:
    """One fill. ``timestamp`` is timezone-aware; ``trade_date`` is that instant
    expressed in the market's timezone, which is the date the day-trade test
    uses."""

    symbol: str
    side: str  # normalised to SIDE_BUY or SIDE_SELL
    quantity: float
    timestamp: datetime.datetime
    trade_date: datetime.date


@dataclass(frozen=True)
class DayTradeRecord:
    """One same-day round trip: a closing execution that offset quantity opened
    the same trading day in the same symbol."""

    symbol: str
    open_timestamp: datetime.datetime
    close_timestamp: datetime.datetime
    trade_date: datetime.date
    quantity: float = 0.0


@dataclass(frozen=True)
class PDTGateDecision:
    """Auditable record of one pre-trade day-trade check."""

    blocked: bool
    reason: str
    as_of_date: datetime.date
    rolling_day_trade_count: int
    max_day_trades_in_window: int
    window_business_days: int
    equity: float
    equity_threshold: float
    policy_name: str
    policy_source: str
    designated_pattern_day_trader: bool
    total_trades_in_window: int = 0
    day_trade_ratio: Optional[float] = None
    warnings: Tuple[str, ...] = ()

    def as_log_record(self) -> Dict[str, object]:
        """Flat dict for structured logging or audit persistence."""
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "as_of_date": self.as_of_date.isoformat(),
            "rolling_day_trade_count": self.rolling_day_trade_count,
            "max_day_trades_in_window": self.max_day_trades_in_window,
            "window_business_days": self.window_business_days,
            "equity": self.equity,
            "equity_threshold": self.equity_threshold,
            "policy_name": self.policy_name,
            "policy_source": self.policy_source,
            "designated_pattern_day_trader": self.designated_pattern_day_trader,
            "total_trades_in_window": self.total_trades_in_window,
            "day_trade_ratio": self.day_trade_ratio,
            "warnings": list(self.warnings),
        }


@dataclass
class _OpenLot:
    """Unmatched opening quantity awaiting an offsetting execution."""

    side: str
    quantity: float
    timestamp: datetime.datetime
    trade_date: datetime.date


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------
def _normalise_side(side: str) -> str:
    if not isinstance(side, str) or not side.strip():
        raise PDTInputError(f"side must be a non-empty string, got {side!r}")
    token = side.strip().upper().replace("-", "_").replace(" ", "_")
    if token in _BUY_SIDES:
        return SIDE_BUY
    if token in _SELL_SIDES:
        return SIDE_SELL
    raise PDTInputError(
        f"unrecognised side {side!r}; expected a buy/sell token such as 'BUY', "
        f"'SELL', 'BUY_TO_COVER' or 'SELL_SHORT'"
    )


def _validate_quantity(quantity: float) -> float:
    try:
        qty = float(quantity)
    except (TypeError, ValueError) as exc:
        raise PDTInputError(f"quantity must be numeric, got {quantity!r}") from exc
    if not math.isfinite(qty) or qty <= 0:
        raise PDTInputError(f"quantity must be finite and > 0, got {quantity!r}")
    return qty


def _validate_amount(value: float, label: str) -> float:
    """Coerce a currency amount. A decimal *string* is accepted because broker
    payloads routinely carry equity that way; anything non-finite or
    unparseable raises."""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise PDTInputError(f"{label} must be numeric, got {value!r}") from exc
    if not math.isfinite(amount):
        raise PDTInputError(f"{label} must be finite, got {value!r}")
    return amount


def _coerce_date(value: datetime.date) -> datetime.date:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    raise PDTInputError(f"expected a date, got {value!r}")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class PDTComplianceEngine:
    """Tracks same-day round trips under a broker day-trade policy and gates the
    next day trade.

    What it deliberately does not do:

    * It is not the authoritative count. The broker's own counter -- where the
      broker still publishes one -- governs; use :meth:`reconcile_broker_count`.
    * It supplies no holiday calendar. Without ``holidays`` the business-day
      window excludes weekends only, and every decision says so in
      ``warnings``.
    * It does not compute Rule 4210(d)(2) intraday margin. See
      :func:`intraday_margin_deficit` in this module for that side.
    * It does not prune history. ``day_trade_history`` and
      ``execution_history`` grow with every fill and each window query scans
      them; drop records older than the window plus a retention margin if the
      account's fill rate makes that matter.
    """

    def __init__(
        self,
        equity_threshold: Optional[float] = None,
        window_business_days: Optional[int] = None,
        policy: DayTradePolicy = LEGACY_FINRA_PDT_POLICY,
        holidays: Optional[Iterable[datetime.date]] = None,
        market_timezone: str = DEFAULT_MARKET_TIMEZONE,
        assume_naive_is_market_local: bool = False,
    ) -> None:
        """``equity_threshold`` and ``window_business_days`` override the
        policy's own values when supplied, preserving the 1.x signature."""
        overrides: Dict[str, object] = {}
        if equity_threshold is not None:
            overrides["equity_threshold"] = _validate_amount(equity_threshold, "equity_threshold")
        if window_business_days is not None:
            overrides["window_business_days"] = int(window_business_days)
        self.policy = replace(policy, **overrides) if overrides else policy

        self.equity_threshold = self.policy.equity_threshold
        self.window_business_days = self.policy.window_business_days
        self.market_timezone = market_timezone
        self.assume_naive_is_market_local = assume_naive_is_market_local
        self._tz = self._resolve_timezone(market_timezone)
        self.holidays: Set[datetime.date] = {_coerce_date(d) for d in (holidays or ())}

        self.open_positions: Dict[str, List[_OpenLot]] = {}
        self.day_trade_history: List[DayTradeRecord] = []
        self.execution_history: List[TradeExecution] = []
        self._designated_pattern_day_trader = False

    # -- timezone and calendar ---------------------------------------------
    @staticmethod
    def _resolve_timezone(name: str) -> Optional[datetime.tzinfo]:
        if ZoneInfo is None:  # pragma: no cover - Python < 3.9
            return None
        try:
            return ZoneInfo(name)
        except Exception as exc:  # KeyError / ZoneInfoNotFoundError / ValueError
            raise PDTInputError(
                f"unknown market timezone {name!r}; pass a valid IANA name and ensure "
                f"tzdata is available"
            ) from exc

    def _market_date(self, timestamp: datetime.datetime) -> datetime.date:
        """The trading date of an instant, in the market's timezone.

        A naive timestamp is rejected by default: a 19:30 America/New_York fill
        expressed in UTC lands on the *next* calendar date, which silently
        splits a same-session round trip across two dates and under-counts day
        trades -- the dangerous direction of error.
        """
        if not isinstance(timestamp, datetime.datetime):
            raise PDTInputError(f"timestamp must be a datetime, got {timestamp!r}")
        if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
            if not self.assume_naive_is_market_local:
                raise PDTInputError(
                    "timestamp is timezone-naive; localise it, or construct the engine with "
                    "assume_naive_is_market_local=True if the naive values are already "
                    f"{self.market_timezone} wall-clock times"
                )
            return timestamp.date()
        if self._tz is None:  # pragma: no cover - Python < 3.9 fallback
            return timestamp.date()
        return timestamp.astimezone(self._tz).date()

    def _is_business_day(self, day: datetime.date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def _window_dates(self, as_of_date: datetime.date) -> List[datetime.date]:
        """The ``window_business_days`` business days ending at ``as_of_date``.

        If ``as_of_date`` is not itself a business day the window ends on the
        preceding business day, so a Saturday check sees the same window Friday
        did rather than silently shifting it by a day.
        """
        cursor = as_of_date
        guard = 0
        while not self._is_business_day(cursor):
            cursor -= datetime.timedelta(days=1)
            guard += 1
            if guard > 30:
                raise PDTInputError(
                    f"no business day found within 30 days before {as_of_date}; check the "
                    f"holiday calendar"
                )
        dates: List[datetime.date] = []
        while len(dates) < self.window_business_days:
            if self._is_business_day(cursor):
                dates.append(cursor)
            cursor -= datetime.timedelta(days=1)
        return dates

    def business_days_between(
        self, start_date: datetime.date, end_date: datetime.date
    ) -> int:
        """Business days strictly after ``start_date`` through ``end_date``.

        Negative when ``end_date`` precedes ``start_date``, so a future-dated
        record is distinguishable from a same-day one rather than collapsing to
        zero.
        """
        start_date = _coerce_date(start_date)
        end_date = _coerce_date(end_date)
        if end_date == start_date:
            return 0
        step = 1 if end_date > start_date else -1
        cursor, count = start_date, 0
        while cursor != end_date:
            cursor += datetime.timedelta(days=step)
            if self._is_business_day(cursor):
                count += step
        return count

    # -- recording ----------------------------------------------------------
    def record_execution(
        self,
        symbol: str,
        side: str,
        quantity: float,
        timestamp: datetime.datetime,
    ) -> Optional[DayTradeRecord]:
        """Record one fill; return a :class:`DayTradeRecord` if it closed
        quantity opened the same trading day.

        Matching is *quantity-aware* and consumes same-day opening lots before
        overnight ones. That ordering implements the carve-out of the former
        rule -- "a 'long' security position held overnight and sold the next day
        **prior to any new purchase** of the same security" is not a day trade.
        Overnight quantity is therefore only consumed once no same-day opening
        quantity remains, i.e. exactly when no new same-day purchase preceded
        the sale. A single-slot model gets this wrong in both directions: it
        discards scale-ins and then invents day trades out of the resulting
        position drift.

        Counting convention: one closing execution yields at most one day trade
        however many same-day lots it offsets, and ``quantity`` on the record is
        the same-day quantity closed. Brokers differ in their conventions; the
        broker's counter is authoritative.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise PDTInputError(f"symbol must be a non-empty string, got {symbol!r}")
        sym = symbol.strip().upper()
        normalised_side = _normalise_side(side)
        qty = _validate_quantity(quantity)
        trade_date = self._market_date(timestamp)

        self.execution_history.append(
            TradeExecution(
                symbol=sym,
                side=normalised_side,
                quantity=qty,
                timestamp=timestamp,
                trade_date=trade_date,
            )
        )

        lots = self.open_positions.setdefault(sym, [])
        remaining = qty
        same_day_closed = 0.0
        earliest_open: Optional[datetime.datetime] = None

        # Two passes, FIFO within each: same-day opening lots first, then
        # overnight lots. See the carve-out discussion in the docstring.
        for same_day_pass in (True, False):
            if remaining <= QUANTITY_EPSILON:
                break
            for lot in lots:
                if remaining <= QUANTITY_EPSILON:
                    break
                if lot.side == normalised_side or lot.quantity <= QUANTITY_EPSILON:
                    continue
                if (lot.trade_date == trade_date) is not same_day_pass:
                    continue
                matched = min(remaining, lot.quantity)
                if same_day_pass:
                    same_day_closed += matched
                    if earliest_open is None or lot.timestamp < earliest_open:
                        earliest_open = lot.timestamp
                remaining -= matched
                lot.quantity -= matched
        self.open_positions[sym] = [lot for lot in lots if lot.quantity > QUANTITY_EPSILON]
        lots = self.open_positions[sym]

        if remaining > QUANTITY_EPSILON:
            # Opening quantity: a scale-in, a fresh position, or the far side of
            # a reversal. All three must be retained, or later executions are
            # matched against a position that does not exist.
            lots.append(
                _OpenLot(
                    side=normalised_side,
                    quantity=remaining,
                    timestamp=timestamp,
                    trade_date=trade_date,
                )
            )

        if same_day_closed <= QUANTITY_EPSILON:
            return None

        record = DayTradeRecord(
            symbol=sym,
            open_timestamp=earliest_open if earliest_open is not None else timestamp,
            close_timestamp=timestamp,
            trade_date=trade_date,
            quantity=same_day_closed,
        )
        self.day_trade_history.append(record)
        logger.info("Day trade recorded: %s %s on %s", sym, same_day_closed, trade_date)

        if self.get_rolling_day_trade_count(trade_date) >= self.policy.max_day_trades_in_window:
            if not self._designated_pattern_day_trader:
                logger.warning(
                    "Day-trade count reached %d in the rolling %d-business-day window on %s; a "
                    "broker still applying a count-based policy would designate this account",
                    self.policy.max_day_trades_in_window,
                    self.window_business_days,
                    trade_date,
                )
            self._designated_pattern_day_trader = True
        return record

    # -- state --------------------------------------------------------------
    @property
    def designated_pattern_day_trader(self) -> bool:
        """Sticky. Under the former rule the designation did not lapse when the
        rolling window emptied -- the minimum equity had to be maintained "at
        all times". Clear it only on the broker's confirmation, via
        :meth:`set_broker_designation`."""
        return self._designated_pattern_day_trader

    def set_broker_designation(self, designated: bool) -> None:
        """Adopt the broker's designation flag as authoritative."""
        designated = bool(designated)
        if designated != self._designated_pattern_day_trader:
            logger.info("PDT designation set to %s from broker report", designated)
        self._designated_pattern_day_trader = designated

    def get_rolling_day_trade_count(self, as_of_date: datetime.date) -> int:
        """Day trades within the business-day window ending at ``as_of_date``.

        ``as_of_date`` is required. Anchoring the window on "the last recorded
        trade" (1.x behaviour) kept a months-old history inside the window
        forever; anchoring it on wall-clock "today" silently mis-evaluates a
        backtest. Both are wrong for a compliance gate, so the caller states the
        date.
        """
        window = set(self._window_dates(_coerce_date(as_of_date)))
        return sum(1 for record in self.day_trade_history if record.trade_date in window)

    def get_total_trades_in_window(self, as_of_date: datetime.date) -> int:
        """Executions in the same window -- the denominator of the former 6
        percent test."""
        window = set(self._window_dates(_coerce_date(as_of_date)))
        return sum(1 for execution in self.execution_history if execution.trade_date in window)

    # -- gate ---------------------------------------------------------------
    def evaluate_day_trade_gate(
        self, current_equity: float, as_of_date: datetime.date
    ) -> PDTGateDecision:
        """Decide whether the *next* day trade may be placed, with provenance.

        Blocking condition under the legacy parameters: equity below the
        threshold, and either the account is already designated or the proposed
        trade would be the ``max_day_trades_in_window``-th within the window.
        """
        equity = _validate_amount(current_equity, "current_equity")
        as_of = _coerce_date(as_of_date)
        count = self.get_rolling_day_trade_count(as_of)
        total_trades = self.get_total_trades_in_window(as_of)
        ratio = (count / total_trades) if total_trades else None
        warnings: List[str] = []

        if not self.holidays:
            warnings.append(
                "No exchange holiday calendar supplied: the business-day window excludes "
                "weekends only and will be wrong across an exchange holiday. See "
                "global-exchange-holiday-calendar-handling."
            )
        if any(record.trade_date > as_of for record in self.day_trade_history):
            warnings.append(
                f"Day-trade history contains records dated after as_of_date "
                f"{as_of.isoformat()}; they are excluded from the window."
            )
        if self.policy.confirmed_with_broker is None and as_of >= PDT_RULE_DELETED_EFFECTIVE:
            warnings.append(
                f"FINRA Rule 4210(f)(8)(B) was deleted effective "
                f"{PDT_RULE_DELETED_EFFECTIVE.isoformat()}; this count-based policy is "
                f"unconfirmed against the broker. Confirm whether the broker still applies "
                f"day-trading requirements during the phase-in ending "
                f"{PDT_PHASE_IN_END.isoformat()}, and monitor Rule 4210(d)(2) intraday margin."
            )
        if as_of > PDT_PHASE_IN_END and self.policy.confirmed_with_broker:
            warnings.append(
                f"The FINRA phase-in ended {PDT_PHASE_IN_END.isoformat()}; a count-based "
                f"restriction after that date can only be a broker house requirement under "
                f"Rule 4210(d)(1)."
            )

        def decide(blocked: bool, reason: str) -> PDTGateDecision:
            decision = PDTGateDecision(
                blocked=blocked,
                reason=reason,
                as_of_date=as_of,
                rolling_day_trade_count=count,
                max_day_trades_in_window=self.policy.max_day_trades_in_window,
                window_business_days=self.window_business_days,
                equity=equity,
                equity_threshold=self.policy.equity_threshold,
                policy_name=self.policy.name,
                policy_source=self.policy.source,
                designated_pattern_day_trader=self._designated_pattern_day_trader,
                total_trades_in_window=total_trades,
                day_trade_ratio=ratio,
                warnings=tuple(warnings),
            )
            emit = logger.warning if blocked else logger.info
            emit("Day-trade gate: %s", decision.as_log_record())
            return decision

        if self.policy.confirmed_with_broker is False:
            return decide(
                False,
                f"Broker confirmed it no longer applies a count-based day-trading policy "
                f"(policy '{self.policy.name}'). Rule 4210(d)(2) intraday margin governs instead.",
            )

        if equity >= self.policy.equity_threshold:
            reason = (
                f"Equity ${equity:,.2f} >= ${self.policy.equity_threshold:,.2f} threshold "
                f"(rolling day trades = {count}). Day trade allowed."
            )
            if self._designated_pattern_day_trader:
                reason += (
                    " Account is designated: the threshold must be maintained at all times, "
                    "not merely met at this moment."
                )
            return decide(False, reason)

        projected_ratio = (count + 1) / (total_trades + 1)
        if (
            self.policy.apply_de_minimis_exemption
            and total_trades > 0
            and projected_ratio <= self.policy.de_minimis_trade_fraction
        ):
            return decide(
                False,
                f"Day trade allowed: projected day-trade share {projected_ratio:.2%} is within "
                f"the de minimis {self.policy.de_minimis_trade_fraction:.0%} exemption enabled "
                f"on policy '{self.policy.name}'.",
            )

        if self._designated_pattern_day_trader:
            return decide(
                True,
                f"PDT VETO: account is designated a pattern day trader and equity "
                f"${equity:,.2f} < ${self.policy.equity_threshold:,.2f}. A designated account "
                f"must maintain the minimum equity at all times before day trading resumes.",
            )

        would_be_nth = count + 1
        if would_be_nth >= self.policy.max_day_trades_in_window:
            return decide(
                True,
                f"PDT VETO: equity ${equity:,.2f} < ${self.policy.equity_threshold:,.2f} and this "
                f"would be day trade {would_be_nth} of a maximum "
                f"{self.policy.max_day_trades_in_window} in the rolling "
                f"{self.window_business_days}-business-day window ending {as_of.isoformat()}.",
            )

        return decide(
            False,
            f"Day trade allowed (rolling count {count}, limit "
            f"{self.policy.max_day_trades_in_window} in {self.window_business_days} business days).",
        )

    def would_breach_pdt(
        self, current_equity: float, as_of_date: datetime.date
    ) -> Tuple[bool, str]:
        """``(blocked, reason)`` view of :meth:`evaluate_day_trade_gate`."""
        decision = self.evaluate_day_trade_gate(current_equity, as_of_date)
        return decision.blocked, decision.reason

    # -- reconciliation -----------------------------------------------------
    def reconcile_broker_count(
        self, broker_count: Optional[int], as_of_date: datetime.date
    ) -> bool:
        """Compare the local count with the broker's.

        ``broker_count=None`` means the broker publishes no counter -- Alpaca,
        for instance, removed ``daytrade_count``, ``pattern_day_trader``,
        ``last_daytrade_count``, ``daytrading_buying_power`` and
        ``last_daytrading_buying_power`` from its API by 2026-07-06 on adopting
        intraday margin. That is not a reconciliation failure, but it does mean
        the local count is unverified, so this returns ``False`` and logs why.
        """
        local_count = self.get_rolling_day_trade_count(as_of_date)
        if broker_count is None:
            logger.warning(
                "No broker day-trade counter available to reconcile against (local count=%d). "
                "The broker may have migrated to Rule 4210(d)(2) intraday margin.",
                local_count,
            )
            return False
        if isinstance(broker_count, bool) or not isinstance(broker_count, int) or broker_count < 0:
            raise PDTInputError(
                f"broker_count must be a non-negative int or None, got {broker_count!r}")
        if local_count != broker_count:
            logger.warning(
                "PDT count desync: local=%d broker=%d as of %s. The broker's count governs.",
                local_count,
                broker_count,
                _coerce_date(as_of_date).isoformat(),
            )
            return False
        return True


# ---------------------------------------------------------------------------
# Rule 4210(d)(2) intraday margin -- the successor regime
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntradayMarginSnapshot:
    """Account state immediately after one transaction.

    ``iml_reducing`` marks a transaction that reduces the intraday margin level
    -- per Rule 4210(a)(18), one that reduces the amount the customer could
    withdraw while still meeting the maintenance margin requirement, including
    any withdrawal of cash or securities. Only such a transaction can create a
    deficit.
    """

    timestamp: datetime.datetime
    equity: float
    maintenance_margin_requirement: float
    iml_reducing: bool = True

    @property
    def intraday_margin_level(self) -> float:
        """IML: equity less the maintenance margin requirement (Rule 4210(a)(17))."""
        return self.equity - self.maintenance_margin_requirement


def intraday_margin_deficit(snapshots: Sequence[IntradayMarginSnapshot]) -> float:
    """The day's intraday margin deficit under Rule 4210(a)(19) and (d)(2)(B).

    The rule defines it as an amount "not less than the absolute value of the
    largest negative IML (if any) with respect to any IML-reducing transaction
    in such margin account during such day" -- so it is the magnitude of the
    worst negative IML following an IML-reducing transaction, and zero if none
    is negative.

    The member's own determination governs. Rule 4210(d)(2)(B) lets a member
    apply sweep-balance, market-value, "as of" and simultaneity policies this
    function knows nothing about, and requires the worst-case ordering wherever
    sequence cannot be demonstrated. Treat the result as *your* estimate, never
    as the broker's number.
    """
    worst = 0.0
    for snapshot in snapshots:
        if not snapshot.iml_reducing:
            continue
        level = snapshot.intraday_margin_level
        if not math.isfinite(level):
            raise PDTInputError(f"non-finite intraday margin level in snapshot {snapshot!r}")
        if level < worst:
            worst = level
    return abs(worst)


def is_de_minimis_deficit(deficit: float, equity: float) -> bool:
    """Rule 4210(d)(2)(D)(i): a deficit not exceeding the lesser of 5 percent of
    the equity in the margin account or $1,000 cannot establish a "practice" of
    failing to satisfy deficits promptly."""
    deficit = _validate_amount(deficit, "deficit")
    equity = _validate_amount(equity, "equity")
    if deficit <= 0:
        return True
    return deficit <= min(DE_MINIMIS_DEFICIT_EQUITY_FRACTION * equity, DE_MINIMIS_DEFICIT_CAP)


def deficit_freeze_deadline(
    deficit_date: datetime.date,
    holidays: Optional[Iterable[datetime.date]] = None,
) -> Tuple[datetime.date, datetime.date]:
    """``(prompt_deadline, expiry_date)`` for one intraday margin deficit.

    * ``prompt_deadline`` -- close of business on the 5th business day after the
      deficit. Failing to satisfy it *and* making a practice of failing to
      satisfy deficits promptly triggers the 90-calendar-day restriction on
      creating or increasing a short position or debit balance, Rule
      4210(d)(2)(D).
    * ``expiry_date`` -- the deficit remains outstanding until satisfied or
      until immediately after the close of business on the 15th business day
      after it arose, Rule 4210(d)(2)(C)(iii). Expiry is not a safe harbour:
      (d)(2)(D) applies "without regard to its expiration".

    Whether a customer "makes a practice" of failing is the member's
    determination; this function only dates the deadlines.
    """
    deficit_date = _coerce_date(deficit_date)
    holiday_set = {_coerce_date(d) for d in (holidays or ())}

    def add_business_days(start: datetime.date, count: int) -> datetime.date:
        cursor, added, guard = start, 0, 0
        while added < count:
            cursor += datetime.timedelta(days=1)
            guard += 1
            if guard > 365:
                raise PDTInputError(
                    f"could not find {count} business days within a year of {start}; "
                    f"check the holiday calendar"
                )
            if cursor.weekday() < 5 and cursor not in holiday_set:
                added += 1
        return cursor

    return (
        add_business_days(deficit_date, INTRADAY_DEFICIT_PROMPT_BUSINESS_DAYS),
        add_business_days(deficit_date, INTRADAY_DEFICIT_EXPIRY_BUSINESS_DAYS),
    )


# ---------------------------------------------------------------------------
# Backward-compatible shim
# ---------------------------------------------------------------------------
class DayTradeTracker:
    """Minimal wrapper kept for callers written against the 1.x helper."""

    def __init__(self, **engine_kwargs: object) -> None:
        self.engine = PDTComplianceEngine(**engine_kwargs)  # type: ignore[arg-type]

    def record_day_trade(self, trade_date: datetime.date) -> None:
        """Append a day trade directly, bypassing execution matching."""
        trade_date = _coerce_date(trade_date)
        self.engine.day_trade_history.append(
            DayTradeRecord(
                symbol="UNKNOWN",
                open_timestamp=datetime.datetime.combine(trade_date, datetime.time(9, 30)),
                close_timestamp=datetime.datetime.combine(trade_date, datetime.time(15, 0)),
                trade_date=trade_date,
                quantity=0.0,
            )
        )

    def would_breach(
        self, account_equity: float, as_of_date: Optional[datetime.date] = None
    ) -> bool:
        """``as_of_date`` defaults to today in the engine's market timezone.

        Pass it explicitly in a backtest -- wall-clock "today" is not the
        simulated date.
        """
        if as_of_date is None:
            tz = self.engine._tz
            as_of_date = (
                datetime.datetime.now(tz).date() if tz is not None else datetime.date.today()
            )
        breached, _ = self.engine.would_breach_pdt(account_equity, as_of_date)
        return breached
