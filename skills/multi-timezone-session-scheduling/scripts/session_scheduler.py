"""
multi-timezone-session-scheduling: IANA zone-aware multi-exchange session scheduler.

Resolves an exchange's *local* trading session to exact UTC instants for a given calendar
date, recomputing the UTC offset on every query so that Daylight Saving Time transitions --
including the Northern/Southern hemisphere inversion and the US/EU desynchronisation
windows -- are picked up automatically rather than baked in at deployment time.

Scope and non-goals
-------------------
* This module answers "what are this exchange's session boundaries in UTC on date D, and
  what session state is the exchange in right now?".
* It is **weekday-based only**: it has no holiday calendar and no half-day calendar, so a
  public holiday or an early close is reported as a normal session, and a session that opens
  on a Sunday evening (CME Globex opens 17:00 CT Sunday) is reported closed. Compose it with
  `global-exchange-holiday-calendar-handling` before using the result as a trading gate.
* Deep DST forensics (nanosecond epochs, US/EU desync-window enumeration) live in
  `daylight-saving-time-transition-handling`; this module only *flags* a session boundary
  that lands on a skipped or repeated local wall time.
"""
from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
from typing import Dict, List, Optional, Tuple
import zoneinfo
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc


class SessionScheduleError(ValueError):
    """
    Raised for an unknown exchange code, an invalid schedule, or a naive query timestamp.

    Subclasses `ValueError` so callers written against the previous `ValueError` contract
    of `get_session_utc()` keep working.
    """


class MarketSessionState(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR_TRADING = "REGULAR_TRADING"
    BREAK = "BREAK"
    POST_MARKET = "POST_MARKET"
    MARKET_CLOSED = "MARKET_CLOSED"


@dataclass(frozen=True)
class ExchangeSchedule:
    """
    An exchange's session expressed in *local wall time*, never as a UTC offset.

    Attributes:
        exchange_code: Exchange identifier, conventionally the ISO 10383 MIC.
        iana_timezone: IANA tz database key (e.g. `America/New_York`). A fixed offset such
            as `UTC-5` is exactly the anti-pattern this skill exists to prevent.
        open_time: Local start of continuous (regular) trading.
        close_time: Local end of continuous trading.
        pre_market_time: Local *start* of the pre-market window; it ends at `open_time`.
        post_market_time: Local *end* of the post-market window; it starts at `close_time`.
        breaks: Intraday halts as (start, end) local times -- e.g. the Tokyo Stock Exchange
            lunch break, 11:30-12:30 JST. Must lie inside the session and must not overlap.
            Without these, a scheduler reports REGULAR_TRADING while the exchange is not
            matching orders at all.
        spans_midnight: True when `close_time` falls on the local calendar day *after*
            `open_time` (an overnight futures session). Cannot be combined with
            `breaks` / `pre_market_time` / `post_market_time` -- see `_validate_schedule`.
    """

    exchange_code: str
    iana_timezone: str
    open_time: datetime.time
    close_time: datetime.time
    pre_market_time: Optional[datetime.time] = None
    post_market_time: Optional[datetime.time] = None
    breaks: Tuple[Tuple[datetime.time, datetime.time], ...] = field(default_factory=tuple)
    spans_midnight: bool = False


@dataclass(frozen=True)
class ResolvedSession:
    """
    One exchange's session for one local calendar date, resolved to UTC instants.

    `trading_windows_utc` are half-open `[start, end)` intervals with intraday breaks
    removed; `open_utc`/`close_utc` are the outer session bounds. `nonexistent_boundaries`
    and `ambiguous_boundaries` name any boundary that landed on a DST-skipped or
    DST-repeated local wall time and was therefore resolved with the PEP 495 `fold=0`
    default -- on those boundaries the returned instant is a convention, not a fact.
    """

    exchange_code: str
    local_date: datetime.date
    open_utc: datetime.datetime
    close_utc: datetime.datetime
    trading_windows_utc: Tuple[Tuple[datetime.datetime, datetime.datetime], ...]
    pre_market_utc: Optional[datetime.datetime] = None
    post_market_utc: Optional[datetime.datetime] = None
    nonexistent_boundaries: Tuple[str, ...] = field(default_factory=tuple)
    ambiguous_boundaries: Tuple[str, ...] = field(default_factory=tuple)


# Sources for the shipped defaults are cited in `references/standards.md`. These are
# *continuous trading* hours -- opening/closing auction windows are deliberately excluded,
# because an auction is not a period in which a resting limit order trades continuously.
DEFAULT_EXCHANGE_SCHEDULES: Dict[str, ExchangeSchedule] = {
    # NYSE Core Trading 09:30-16:00 ET. Early Trading is 07:00 ET *on NYSE itself*; the
    # familiar 04:00 ET start belongs to NYSE Arca (ARCX) and Nasdaq (XNAS), so override
    # `pre_market_time` when modelling those venues or a broker's consolidated extended hours.
    "XNYS": ExchangeSchedule(
        exchange_code="XNYS",
        iana_timezone="America/New_York",
        open_time=datetime.time(9, 30),
        close_time=datetime.time(16, 0),
        pre_market_time=datetime.time(7, 0),
        post_market_time=datetime.time(20, 0),
    ),
    "XLON": ExchangeSchedule(
        exchange_code="XLON",
        iana_timezone="Europe/London",
        open_time=datetime.time(8, 0),
        close_time=datetime.time(16, 30),
    ),
    "XNSE": ExchangeSchedule(
        exchange_code="XNSE",
        iana_timezone="Asia/Kolkata",
        open_time=datetime.time(9, 15),
        close_time=datetime.time(15, 30),
        pre_market_time=datetime.time(9, 0),
    ),
    # TSE extended the afternoon session close from 15:00 to 15:30 JST on 2024-11-05 and
    # retains the 11:30-12:30 lunch break.
    "XTKS": ExchangeSchedule(
        exchange_code="XTKS",
        iana_timezone="Asia/Tokyo",
        open_time=datetime.time(9, 0),
        close_time=datetime.time(15, 30),
        breaks=((datetime.time(11, 30), datetime.time(12, 30)),),
    ),
    "XASX": ExchangeSchedule(
        exchange_code="XASX",
        iana_timezone="Australia/Sydney",
        open_time=datetime.time(10, 0),
        close_time=datetime.time(16, 0),
    ),
}


def _resolve_timezone(tz_key: str, exchange_code: str) -> ZoneInfo:
    """Resolves an IANA key, converting the lazy lookup failure into an actionable error."""
    try:
        return ZoneInfo(tz_key)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise SessionScheduleError(
            f"{exchange_code}: '{tz_key}' is not a resolvable IANA time zone. Use a tz "
            f"database key such as 'America/New_York', not a fixed UTC offset. On hosts "
            f"with no system tz database (notably Windows) install the `tzdata` package."
        ) from exc


def _localize(
    naive: datetime.datetime, tz: ZoneInfo
) -> Tuple[datetime.datetime, bool, bool]:
    """
    Attaches `tz` to a naive local wall time and classifies it against the DST transition.

    Returns `(aware, is_nonexistent, is_ambiguous)`. Per PEP 495 the result carries
    `fold=0`: a repeated wall time resolves to its FIRST occurrence and a skipped wall time
    is normalised by the pre-transition offset. Both cases are reported rather than
    silently accepted, because on those two days a year the returned instant is a guess.
    """
    aware = naive.replace(tzinfo=tz)
    # A skipped wall time does not survive a round trip through UTC.
    is_nonexistent = aware.astimezone(_UTC).astimezone(tz).replace(tzinfo=None) != naive
    # A repeated wall time has two distinct UTC offsets (fold=0 vs fold=1). Under PEP 495 a
    # *skipped* wall time also yields two offsets, so ambiguity is the fold disagreement
    # that remains once the gap case is excluded -- the two are mutually exclusive.
    is_ambiguous = (
        not is_nonexistent and aware.utcoffset() != aware.replace(fold=1).utcoffset()
    )
    return aware, is_nonexistent, is_ambiguous


def _validate_schedule(schedule: ExchangeSchedule) -> ExchangeSchedule:
    """Validates a schedule eagerly, so a bad zone or ordering fails at registration."""
    code = (schedule.exchange_code or "").strip().upper()
    if not code:
        raise SessionScheduleError("ExchangeSchedule.exchange_code must be non-empty.")

    _resolve_timezone(schedule.iana_timezone, code)

    for label, value in (
        ("open_time", schedule.open_time),
        ("close_time", schedule.close_time),
        ("pre_market_time", schedule.pre_market_time),
        ("post_market_time", schedule.post_market_time),
    ):
        if label in ("open_time", "close_time") and not isinstance(value, datetime.time):
            raise SessionScheduleError(f"{code}: {label} must be a datetime.time.")
        if value is not None and not isinstance(value, datetime.time):
            raise SessionScheduleError(f"{code}: {label} must be a datetime.time or None.")
        if isinstance(value, datetime.time) and value.tzinfo is not None:
            raise SessionScheduleError(
                f"{code}: {label} must be a naive local wall time; the zone comes from "
                f"iana_timezone, and a tz-aware time object would pin a fixed offset."
            )

    if schedule.spans_midnight:
        if schedule.breaks or schedule.pre_market_time or schedule.post_market_time:
            raise SessionScheduleError(
                f"{code}: spans_midnight cannot be combined with breaks, pre_market_time, "
                f"or post_market_time -- those are anchored to a single local date. Model "
                f"an overnight session with intraday halts as separate ExchangeSchedules."
            )
    elif schedule.close_time <= schedule.open_time:
        raise SessionScheduleError(
            f"{code}: close_time {schedule.close_time} is not after open_time "
            f"{schedule.open_time}. Set spans_midnight=True for an overnight session."
        )

    if schedule.pre_market_time is not None and schedule.pre_market_time > schedule.open_time:
        raise SessionScheduleError(
            f"{code}: pre_market_time {schedule.pre_market_time} is after open_time "
            f"{schedule.open_time}; it marks the START of the pre-market window."
        )
    if schedule.post_market_time is not None and schedule.post_market_time < schedule.close_time:
        raise SessionScheduleError(
            f"{code}: post_market_time {schedule.post_market_time} is before close_time "
            f"{schedule.close_time}; it marks the END of the post-market window."
        )

    previous_end: Optional[datetime.time] = None
    for pair in schedule.breaks:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise SessionScheduleError(f"{code}: each break must be a (start, end) pair.")
        start, end = pair
        if not isinstance(start, datetime.time) or not isinstance(end, datetime.time):
            raise SessionScheduleError(f"{code}: each break must be a (time, time) pair.")
        if end <= start:
            raise SessionScheduleError(
                f"{code}: break {start}-{end} does not end after it starts."
            )
        if start < schedule.open_time or end > schedule.close_time:
            raise SessionScheduleError(
                f"{code}: break {start}-{end} falls outside the session "
                f"{schedule.open_time}-{schedule.close_time}."
            )
        if previous_end is not None and start < previous_end:
            raise SessionScheduleError(
                f"{code}: breaks must be sorted and non-overlapping; {start}-{end} overlaps "
                f"a break ending {previous_end}."
            )
        previous_end = end

    return schedule


class MultiTimezoneSessionScheduler:
    """
    Resolves multi-exchange trading sessions to UTC, recomputing offsets on every query.

    Args:
        custom_schedules: Replaces the shipped defaults. Pass `{}` for an empty registry;
            the mapping is copied, so registering a schedule never mutates the caller's
            dict or the module-level `DEFAULT_EXCHANGE_SCHEDULES`.
        strict: When True, a session boundary landing on a DST-skipped or DST-repeated
            local wall time raises `SessionScheduleError` instead of being resolved with
            the `fold=0` default and flagged on the `ResolvedSession`.
    """

    def __init__(
        self,
        custom_schedules: Optional[Dict[str, ExchangeSchedule]] = None,
        strict: bool = False,
    ) -> None:
        source = DEFAULT_EXCHANGE_SCHEDULES if custom_schedules is None else custom_schedules
        self.schedules: Dict[str, ExchangeSchedule] = {}
        for schedule in source.values():
            validated = _validate_schedule(schedule)
            self.schedules[validated.exchange_code.strip().upper()] = validated
        self.strict = strict

    def register_schedule(self, schedule: ExchangeSchedule) -> None:
        """Validates and registers a schedule on THIS instance. Re-registering replaces it."""
        validated = _validate_schedule(schedule)
        self.schedules[validated.exchange_code.strip().upper()] = validated

    def _get(self, exchange_code: str) -> Tuple[str, ExchangeSchedule]:
        code = (exchange_code or "").strip().upper()
        if code not in self.schedules:
            raise SessionScheduleError(
                f"Unknown exchange code: '{exchange_code}'. Registered: "
                f"{sorted(self.schedules)}"
            )
        return code, self.schedules[code]

    def resolve_session(
        self, exchange_code: str, local_date: datetime.date
    ) -> ResolvedSession:
        """
        Resolves one exchange-local calendar date to UTC session bounds and trading windows.

        The UTC offset is recomputed from the tz database for `local_date`, so a date on
        either side of a DST transition yields a different UTC instant for the same local
        open time. Raises `SessionScheduleError` for an unknown code, and -- in strict mode
        -- for a boundary that lands on a skipped or repeated local wall time.
        """
        code, sched = self._get(exchange_code)
        if isinstance(local_date, datetime.datetime) or not isinstance(
            local_date, datetime.date
        ):
            raise SessionScheduleError(
                f"{code}: local_date must be a datetime.date (not a datetime), got "
                f"{type(local_date).__name__}. A datetime would silently carry a wall clock "
                f"whose zone may differ from the exchange's."
            )
        tz = _resolve_timezone(sched.iana_timezone, code)
        close_date = (
            local_date + datetime.timedelta(days=1) if sched.spans_midnight else local_date
        )

        nonexistent: List[str] = []
        ambiguous: List[str] = []

        def to_utc(on_date: datetime.date, wall: datetime.time, label: str) -> datetime.datetime:
            aware, is_nonexistent, is_ambiguous = _localize(
                datetime.datetime.combine(on_date, wall), tz
            )
            if is_nonexistent or is_ambiguous:
                message = (
                    f"{code}: local {label} {wall} on {on_date} is "
                    f"{'skipped' if is_nonexistent else 'repeated'} by a DST transition; "
                    f"resolved with the fold=0 default to {aware.astimezone(_UTC).isoformat()}."
                )
                if self.strict:
                    raise SessionScheduleError(message)
                logger.warning(message)
                (nonexistent if is_nonexistent else ambiguous).append(label)
            return aware.astimezone(_UTC)

        open_utc = to_utc(local_date, sched.open_time, "open_time")
        close_utc = to_utc(close_date, sched.close_time, "close_time")

        windows: List[Tuple[datetime.datetime, datetime.datetime]] = []
        cursor = open_utc
        for index, (start, end) in enumerate(sched.breaks):
            break_start = to_utc(local_date, start, f"break[{index}].start")
            break_end = to_utc(local_date, end, f"break[{index}].end")
            if break_start > cursor:
                windows.append((cursor, break_start))
            cursor = max(cursor, break_end)
        if close_utc > cursor:
            windows.append((cursor, close_utc))

        pre_utc = (
            to_utc(local_date, sched.pre_market_time, "pre_market_time")
            if sched.pre_market_time is not None
            else None
        )
        post_utc = (
            to_utc(close_date, sched.post_market_time, "post_market_time")
            if sched.post_market_time is not None
            else None
        )

        return ResolvedSession(
            exchange_code=code,
            local_date=local_date,
            open_utc=open_utc,
            close_utc=close_utc,
            trading_windows_utc=tuple(windows),
            pre_market_utc=pre_utc,
            post_market_utc=post_utc,
            nonexistent_boundaries=tuple(nonexistent),
            ambiguous_boundaries=tuple(ambiguous),
        )

    def get_session_utc(
        self, exchange_code: str, local_date: datetime.date
    ) -> Tuple[datetime.datetime, datetime.datetime]:
        """
        Outer UTC open/close bounds for an exchange on a local calendar date.

        Intraday breaks are NOT reflected here -- use `resolve_session().trading_windows_utc`
        when the caller needs the periods the exchange is actually matching.
        """
        resolved = self.resolve_session(exchange_code, local_date)
        return resolved.open_utc, resolved.close_utc

    def get_market_status(
        self, exchange_code: str, query_time_utc: Optional[datetime.datetime] = None
    ) -> MarketSessionState:
        """
        Classifies an instant into a session state for one exchange.

        All boundaries are half-open `[start, end)`: at exactly `close_utc` the exchange is
        no longer in REGULAR_TRADING. `query_time_utc` must be timezone-aware -- a naive
        datetime is rejected rather than assumed to be UTC, because silently guessing a
        zone is the failure mode this skill exists to prevent.

        Weekday-based only: public holidays and early closes are NOT modelled, so a
        non-CLOSED result is a necessary but not sufficient condition for trading.
        """
        code, sched = self._get(exchange_code)

        now_utc = query_time_utc if query_time_utc is not None else datetime.datetime.now(_UTC)
        if not isinstance(now_utc, datetime.datetime):
            raise SessionScheduleError(
                f"query_time_utc must be a datetime.datetime, got {type(now_utc).__name__}."
            )
        if now_utc.tzinfo is None or now_utc.tzinfo.utcoffset(now_utc) is None:
            raise SessionScheduleError(
                "query_time_utc must be timezone-aware. Pass "
                "datetime.datetime.now(datetime.timezone.utc), not datetime.utcnow()."
            )
        now_utc = now_utc.astimezone(_UTC)

        tz = _resolve_timezone(sched.iana_timezone, code)
        local_date = now_utc.astimezone(tz).date()

        # An overnight session that opened on the previous local date is still the session
        # in progress, so both anchor dates must be considered.
        anchors = [local_date]
        if sched.spans_midnight:
            anchors.append(local_date - datetime.timedelta(days=1))

        for anchor in anchors:
            if anchor.weekday() >= 5:
                continue
            resolved = self.resolve_session(code, anchor)

            if any(start <= now_utc < end for start, end in resolved.trading_windows_utc):
                return MarketSessionState.REGULAR_TRADING
            if resolved.open_utc <= now_utc < resolved.close_utc:
                return MarketSessionState.BREAK
            if resolved.pre_market_utc is not None and (
                resolved.pre_market_utc <= now_utc < resolved.open_utc
            ):
                return MarketSessionState.PRE_MARKET
            if resolved.post_market_utc is not None and (
                resolved.close_utc <= now_utc < resolved.post_market_utc
            ):
                return MarketSessionState.POST_MARKET

        return MarketSessionState.MARKET_CLOSED

    def calculate_exchange_gap_minutes(
        self, exchange_a: str, exchange_b: str, local_date: datetime.date
    ) -> float:
        """
        Minutes from exchange A's close to exchange B's open, both resolved on `local_date`.

        Negative means the sessions overlap. `local_date` is interpreted as each exchange's
        OWN local calendar date, which is the useful convention for a handoff ("Monday's
        Tokyo session into Monday's London session") but means the two instants can be far
        apart in UTC. The result is not constant year-round: it moves by an hour during the
        windows where the two regions' DST states disagree, which is precisely why it must
        be recomputed per date rather than cached.
        """
        _, close_a_utc = self.get_session_utc(exchange_a, local_date)
        open_b_utc, _ = self.get_session_utc(exchange_b, local_date)
        return (open_b_utc - close_a_utc).total_seconds() / 60.0


# --- Backward-compatible module-level helpers -------------------------------------------
def exchange_session_utc(
    local_date: datetime.date,
    open_time: datetime.time,
    close_time: datetime.time,
    exchange_tz: str,
) -> Tuple[datetime.datetime, datetime.datetime]:
    """
    Converts one day's local open/close wall times to UTC for an ad-hoc IANA zone.

    Boundaries landing on a DST-skipped or DST-repeated wall time are resolved with the
    PEP 495 `fold=0` default and logged; use `MultiTimezoneSessionScheduler(strict=True)`
    when such a boundary must fail loudly instead.
    """
    tz = _resolve_timezone(exchange_tz, "exchange_session_utc")
    resolved: List[datetime.datetime] = []
    for label, wall in (("open_time", open_time), ("close_time", close_time)):
        aware, is_nonexistent, is_ambiguous = _localize(
            datetime.datetime.combine(local_date, wall), tz
        )
        if is_nonexistent or is_ambiguous:
            logger.warning(
                "%s: local %s %s on %s is %s by a DST transition; resolved with fold=0.",
                exchange_tz,
                label,
                wall,
                local_date,
                "skipped" if is_nonexistent else "repeated",
            )
        resolved.append(aware.astimezone(_UTC))
    return resolved[0], resolved[1]


def cross_exchange_gap_minutes(
    close_a_utc: datetime.datetime, open_b_utc: datetime.datetime
) -> float:
    """
    Minutes from exchange A's UTC close to exchange B's UTC open; negative means overlap.

    Both arguments must be timezone-aware, otherwise the subtraction either raises or --
    if both are naive -- silently compares two different zones' wall clocks as if they
    were the same instant.
    """
    for label, value in (("close_a_utc", close_a_utc), ("open_b_utc", open_b_utc)):
        if not isinstance(value, datetime.datetime):
            raise SessionScheduleError(f"{label} must be a datetime.datetime.")
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise SessionScheduleError(
                f"{label} must be timezone-aware; comparing naive datetimes from two "
                f"exchanges silently mixes wall clocks."
            )
    return (open_b_utc - close_a_utc).total_seconds() / 60.0


__all__ = [
    "DEFAULT_EXCHANGE_SCHEDULES",
    "ExchangeSchedule",
    "MarketSessionState",
    "MultiTimezoneSessionScheduler",
    "ResolvedSession",
    "SessionScheduleError",
    "cross_exchange_gap_minutes",
    "exchange_session_utc",
]
