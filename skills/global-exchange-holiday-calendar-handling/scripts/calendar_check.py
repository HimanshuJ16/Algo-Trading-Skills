"""
global-exchange-holiday-calendar-handling: multi-exchange session resolution,
half-day / early-close detection, explicit listing-venue resolution, and
DST-correct UTC session timing.

Design principle
----------------
This module must never invent a trading session. Every ``SessionInfo`` it returns
carries the ``source`` it came from, and when neither the calendar library nor the
static fallback has data for an (exchange, date) pair the result is
``SessionStatus.UNKNOWN_NO_CALENDAR_DATA`` with ``open_utc``/``close_utc`` set to
``None`` -- not a guessed session. Silently substituting one exchange's hours for
another is the precise failure this skill exists to prevent.

The static tables below are a *degraded mode*, not a calendar. They are pinned to
the years listed in ``FALLBACK_COVERAGE_YEARS`` and refuse to answer outside that
range, so a stale checkout fails loudly instead of quietly returning last year's
holidays. ``exchange_calendars`` (or an equivalent maintained calendar source) is
the intended primary and must be installed for production use.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
from typing import Any, Dict, Mapping, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class CalendarError(Exception):
    """Base class for calendar-resolution failures raised by this module."""


class CalendarDataUnavailable(CalendarError):
    """No authoritative session data exists for the requested exchange/date."""


class UnresolvedInstrumentExchange(CalendarError):
    """An instrument's listing venue could not be resolved without guessing."""


class SessionStatus(str, Enum):
    REGULAR_SESSION = "REGULAR_SESSION"
    HALF_DAY_EARLY_CLOSE = "HALF_DAY_EARLY_CLOSE"
    FULL_DAY_HOLIDAY = "FULL_DAY_HOLIDAY"
    WEEKEND_CLOSED = "WEEKEND_CLOSED"
    #: Neither the calendar library nor the static fallback covers this
    #: (exchange, date). Callers must treat this as "unknown", never as "closed".
    UNKNOWN_NO_CALENDAR_DATA = "UNKNOWN_NO_CALENDAR_DATA"


class CalendarSource(str, Enum):
    EXCHANGE_CALENDARS = "EXCHANGE_CALENDARS"
    STATIC_FALLBACK = "STATIC_FALLBACK"
    NONE = "NONE"


#: Statuses on which orders may be routed / signals evaluated.
TRADEABLE_STATUSES = frozenset(
    {SessionStatus.REGULAR_SESSION, SessionStatus.HALF_DAY_EARLY_CLOSE}
)


@dataclass
class SessionInfo:
    """Resolved session state for one exchange on one calendar date.

    ``open_utc``/``close_utc`` are timezone-aware UTC datetimes, or ``None`` when
    the exchange is closed or the session is unknown. ``source`` records which
    calendar produced the answer so a wrong decision is traceable to its origin
    rather than presenting as an unexplained missed session.
    """

    exchange_code: str
    date: datetime.date
    status: SessionStatus
    open_utc: Optional[datetime.datetime]
    close_utc: Optional[datetime.datetime]
    notes: str
    source: "CalendarSource" = CalendarSource.NONE

    @property
    def is_tradeable(self) -> bool:
        """Whether the exchange holds a session (full or half day) on this date.

        Raises `CalendarDataUnavailable` on an unknown session rather than
        returning False. Returning False here would be the same silent collapse
        of "unknown" into "closed" that this module exists to prevent, and it
        would bypass the check in `GlobalExchangeCalendarManager.is_trading_day`
        for any caller reading this attribute directly.
        """
        if self.status is SessionStatus.UNKNOWN_NO_CALENDAR_DATA:
            raise CalendarDataUnavailable(
                f"Session state for {self.exchange_code} on {self.date} is "
                f"unknown: {self.notes}"
            )
        return self.status in TRADEABLE_STATUSES


@dataclass(frozen=True)
class ExchangeProfile:
    """Static per-exchange session geometry used only in degraded mode.

    ``weekmask`` is a 7-character Monday-first string of '0'/'1' matching the
    ``numpy.busdaycalendar`` convention used by ``exchange_calendars``. It is NOT
    assumed to be Monday-Friday: the Saudi Exchange trades Sunday-Thursday, and
    the Tel Aviv Stock Exchange traded Sunday-Thursday until 2026-01-05.
    """

    tz: str
    weekmask: str
    regular_open_local: datetime.time
    regular_close_local: datetime.time


#: Years the static fallback tables below are sourced for. A query outside this
#: range yields UNKNOWN_NO_CALENDAR_DATA rather than a stale or extrapolated answer.
FALLBACK_COVERAGE_YEARS: Tuple[int, ...] = (2026,)

#: Only exchanges with BOTH a sourced profile and a sourced holiday table below
#: are answerable in degraded mode; see ``FALLBACK_COVERED_EXCHANGES``. Anything
#: else resolves to UNKNOWN_NO_CALENDAR_DATA instead of borrowing NYSE's hours.
#: Sources: NYSE published market hours (nyse.com/markets/hours-calendars);
#: NSE equity session 09:15-15:30 IST, matching the `exchange_calendars` XBOM
#: definition for the same market.
EXCHANGE_PROFILES: Dict[str, ExchangeProfile] = {
    "XNYS": ExchangeProfile(
        tz="America/New_York",
        weekmask="1111100",
        regular_open_local=datetime.time(9, 30),
        regular_close_local=datetime.time(16, 0),
    ),
    "XNSE": ExchangeProfile(
        tz="Asia/Kolkata",
        weekmask="1111100",
        regular_open_local=datetime.time(9, 15),
        regular_close_local=datetime.time(15, 30),
    ),
}

#: `exchange_calendars` ships no NSE calendar -- India is covered by XBOM (BSE)
#: only. Requesting XNSE from the library raises InvalidCalendarName, so we warn
#: and use the static profile rather than substituting another venue's calendar.
CODES_ABSENT_FROM_EXCHANGE_CALENDARS = frozenset({"XNSE"})

#: Full-day closures. XNYS from the NYSE published 2026 holiday calendar; XNSE
#: from the NSE 2026 trading-holiday list. Muhurat trading (Sun 2026-11-08) is a
#: live NSE session and is deliberately absent from this holiday table -- see
#: ExchangeProfile.weekmask on why weekday alone must not decide market state.
FALLBACK_HOLIDAYS: Dict[str, Dict[str, str]] = {
    "XNYS": {
        "2026-01-01": "New Year's Day",
        "2026-01-19": "Martin Luther King Jr. Day",
        "2026-02-16": "Washington's Birthday",
        "2026-04-03": "Good Friday",
        "2026-05-25": "Memorial Day",
        "2026-06-19": "Juneteenth National Independence Day",
        "2026-07-03": "Independence Day (Observed)",
        "2026-09-07": "Labor Day",
        "2026-11-26": "Thanksgiving Day",
        "2026-12-25": "Christmas Day",
    },
    "XNSE": {
        "2026-01-26": "Republic Day",
        "2026-03-03": "Holi",
        "2026-03-26": "Shri Ram Navami",
        "2026-03-31": "Shri Mahavir Jayanti",
        "2026-04-03": "Good Friday",
        "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
        "2026-05-01": "Maharashtra Day",
        "2026-05-28": "Bakri Id",
        "2026-06-26": "Muharram",
        "2026-09-14": "Ganesh Chaturthi",
        "2026-10-02": "Mahatma Gandhi Jayanti",
        "2026-10-20": "Dussehra",
        "2026-11-10": "Diwali-Balipratipada",
        "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev",
        "2026-12-25": "Christmas",
    },
}

#: Early closes, expressed in EXCHANGE-LOCAL wall-clock time and converted to UTC
#: against the exchange's IANA zone at resolution time. Storing UTC directly would
#: reintroduce the fixed-offset bug this skill warns about: NYSE's 13:00 close is
#: 18:00 UTC under EST but 17:00 UTC under EDT.
#: Source: NYSE 2026 early closings (1:00 p.m. ET).
FALLBACK_HALF_DAYS: Dict[str, Dict[str, datetime.time]] = {
    "XNYS": {
        "2026-11-27": datetime.time(13, 0),  # Day after Thanksgiving
        "2026-12-24": datetime.time(13, 0),  # Christmas Eve
    }
}

#: Sessions held OUTSIDE the exchange's normal trading week. These are real open
#: markets that a weekmask alone reports as closed. Their session times are not
#: published in the annual holiday calendar (NSE announces Muhurat timings by
#: separate circular), so this table records only that a session exists -- the
#: resolver returns UNKNOWN_NO_CALENDAR_DATA for them, which makes the caller go
#: and look up the hours instead of silently skipping a live trading day.
FALLBACK_SPECIAL_SESSIONS: Dict[str, Dict[str, str]] = {
    "XNSE": {
        "2026-11-08": "Diwali Muhurat trading (Sunday); timings by NSE circular",
    },
}

#: An exchange is answerable in degraded mode only when it has a session profile
#: AND a sourced holiday table. Without this invariant, adding a profile alone
#: would make the fallback report REGULAR_SESSION on that exchange's holidays --
#: fabricating a session, which is exactly what this module must never do.
FALLBACK_COVERED_EXCHANGES = frozenset(EXCHANGE_PROFILES) & frozenset(FALLBACK_HOLIDAYS)

#: Listing-venue resolution by symbol suffix. Absence of a suffix is NOT taken to
#: mean "US-listed"; unmapped symbols raise instead.
SUFFIX_TO_EXCHANGE: Dict[str, str] = {
    ".NS": "XNSE",
    ".BO": "XBOM",
    ".L": "XLON",
    ".T": "XTKS",
    ".HK": "XHKG",
    ".SR": "XSAU",
    ".TA": "XTAE",
}

#: ADR / cross-listing resolution is explicit configuration, never a heuristic.
#: An ADR trades on its LISTING venue's calendar, not its issuer's home calendar.
DEFAULT_ADR_LISTING_VENUES: Dict[str, str] = {
    "INFY": "XNYS",  # Infosys ADR
    "WIT": "XNYS",   # Wipro ADR
    "HDB": "XNYS",   # HDFC Bank ADR
    "IBN": "XNYS",   # ICICI Bank ADR
}


def _weekmask_allows(weekmask: str, query_date: datetime.date) -> bool:
    """True if `weekmask` (Monday-first, 7 chars) marks this weekday as open."""
    return weekmask[query_date.weekday()] == "1"


def _local_time_to_utc(
    query_date: datetime.date, local_time: datetime.time, tz_name: str
) -> datetime.datetime:
    """Convert an exchange-local wall clock time to UTC for a specific date.

    Resolving through the IANA zone per-date is what keeps cross-exchange timing
    correct through the weeks when two regions' DST transitions do not coincide.
    """
    local_dt = datetime.datetime.combine(
        query_date, local_time, tzinfo=ZoneInfo(tz_name)
    )
    return local_dt.astimezone(datetime.timezone.utc)


def _ensure_utc(value: datetime.datetime) -> datetime.datetime:
    """Normalise a library timestamp to an aware UTC datetime.

    ``exchange_calendars`` builds its schedule with tz-aware UTC timestamps, but a
    naive value from a differently-configured source would silently poison every
    downstream comparison, so assume-UTC-and-mark is done explicitly here.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


def _index_contains_date(index: Any, query_date: datetime.date) -> bool:
    """True if a calendar's session DatetimeIndex contains `query_date`.

    Compares on normalised date components so a tz-aware or nanosecond-resolution
    index does not silently miss an equal calendar date.
    """
    return any(
        (getattr(ts, "year", None), getattr(ts, "month", None), getattr(ts, "day", None))
        == (query_date.year, query_date.month, query_date.day)
        for ts in index
    )


class GlobalExchangeCalendarManager:
    """Resolves session status and UTC session bounds across multiple exchanges.

    Prefers a maintained calendar library (``exchange_calendars`` by default) and
    degrades to the pinned static tables only when the library is unavailable,
    always reporting which source produced the answer.
    """

    def __init__(
        self,
        calendar_lib: Optional[Any] = None,
        adr_listing_venues: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.calendar_lib = calendar_lib
        self._lib_import_attempted = calendar_lib is not None
        self.adr_listing_venues: Dict[str, str] = dict(
            DEFAULT_ADR_LISTING_VENUES
            if adr_listing_venues is None
            else adr_listing_venues
        )

    def _get_lib(self) -> Optional[Any]:
        """Return the calendar library, attempting the import at most once."""
        if self.calendar_lib is not None:
            return self.calendar_lib
        if self._lib_import_attempted:
            return None
        self._lib_import_attempted = True
        try:
            import exchange_calendars as cal_lib
        except ImportError:
            logger.warning(
                "exchange_calendars is not installed; falling back to pinned static "
                "tables covering %s only. Install a maintained calendar library "
                "before relying on this in production.",
                FALLBACK_COVERAGE_YEARS,
            )
            return None
        self.calendar_lib = cal_lib
        return cal_lib

    def map_instrument_to_exchange(self, symbol: str) -> str:
        """Resolve a symbol to its primary LISTING exchange ISO (MIC) code.

        Resolution order: explicit ADR/cross-listing override, then venue suffix.
        A symbol matching neither raises `UnresolvedInstrumentExchange` -- guessing
        a default venue is how an ADR ends up traded against its issuer's home
        calendar instead of the calendar it actually settles on.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise UnresolvedInstrumentExchange("symbol must be a non-empty string")

        sym_upper = symbol.strip().upper()

        if sym_upper in self.adr_listing_venues:
            return self.adr_listing_venues[sym_upper]

        for suffix, code in SUFFIX_TO_EXCHANGE.items():
            if sym_upper.endswith(suffix):
                return code

        raise UnresolvedInstrumentExchange(
            f"Cannot resolve listing exchange for {symbol!r} without guessing. "
            f"Add it to adr_listing_venues or use a suffixed symbol "
            f"({', '.join(sorted(SUFFIX_TO_EXCHANGE))})."
        )

    def get_session_info(
        self, exchange_code: str, query_date: datetime.date
    ) -> SessionInfo:
        """Resolve session status and UTC session bounds for one exchange/date."""
        if not isinstance(exchange_code, str) or not exchange_code.strip():
            raise ValueError("exchange_code must be a non-empty string")
        _reject_datetime(query_date, exchange_code)

        code = exchange_code.strip().upper()

        lib = self._get_lib()
        if lib is not None:
            info = self._session_info_from_library(lib, code, query_date)
            if info is not None:
                return info

        return self._session_info_from_fallback(code, query_date)

    def _session_info_from_library(
        self, lib: Any, code: str, query_date: datetime.date
    ) -> Optional[SessionInfo]:
        """Resolve via the calendar library, or return None to allow fallback."""
        if code in CODES_ABSENT_FROM_EXCHANGE_CALENDARS:
            logger.warning(
                "%s has no calendar in exchange_calendars; using the static profile. "
                "Do not substitute another venue's calendar for it.",
                code,
            )
            return None

        try:
            cal = lib.get_calendar(code)
        except Exception as exc:  # the library raises its own CalendarError subclasses
            logger.error(
                "Calendar %r unavailable from %s (%s: %s); falling back to static tables.",
                code,
                getattr(lib, "__name__", "calendar library"),
                type(exc).__name__,
                exc,
            )
            return None

        try:
            if not cal.is_session(query_date):
                # `is_session` is authoritative for whether the market is open;
                # the weekmask only labels *why* it is closed. That label is
                # approximate for exchanges whose trading week changed over time
                # (XTAE moved off Sunday-Thursday on 2026-01-05), because
                # `cal.weekmask` reports the base mask rather than the one in
                # force on `query_date`. The tradeable/not-tradeable answer is
                # unaffected.
                status = (
                    SessionStatus.FULL_DAY_HOLIDAY
                    if _weekmask_allows(cal.weekmask, query_date)
                    else SessionStatus.WEEKEND_CLOSED
                )
                return SessionInfo(
                    exchange_code=code,
                    date=query_date,
                    status=status,
                    open_utc=None,
                    close_utc=None,
                    notes="Non-session per exchange_calendars",
                    source=CalendarSource.EXCHANGE_CALENDARS,
                )

            open_ts = _ensure_utc(cal.session_open(query_date).to_pydatetime())
            close_ts = _ensure_utc(cal.session_close(query_date).to_pydatetime())

            # `exchange_calendars` exposes early closes as the `early_closes`
            # DatetimeIndex. There is no `is_half_day()` method on ExchangeCalendar
            # -- calling one raises AttributeError and degrades every lookup.
            status = (
                SessionStatus.HALF_DAY_EARLY_CLOSE
                if _index_contains_date(cal.early_closes, query_date)
                else SessionStatus.REGULAR_SESSION
            )

            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=status,
                open_utc=open_ts,
                close_utc=close_ts,
                notes="Session derived via exchange_calendars",
                source=CalendarSource.EXCHANGE_CALENDARS,
            )
        except (AttributeError, LookupError, ValueError, TypeError) as exc:
            logger.error(
                "exchange_calendars lookup failed for %s on %s (%s: %s); "
                "falling back to static tables.",
                code,
                query_date,
                type(exc).__name__,
                exc,
            )
            return None

    def _session_info_from_fallback(
        self, code: str, query_date: datetime.date
    ) -> SessionInfo:
        """Resolve from the pinned static tables, or report unknown."""
        if code not in FALLBACK_COVERED_EXCHANGES:
            return self._unknown(
                code,
                query_date,
                f"No calendar library available and no sourced static coverage for "
                f"{code} (covered: {sorted(FALLBACK_COVERED_EXCHANGES)})",
            )
        profile = EXCHANGE_PROFILES[code]
        if query_date.year not in FALLBACK_COVERAGE_YEARS:
            return self._unknown(
                code,
                query_date,
                f"Static fallback covers {FALLBACK_COVERAGE_YEARS} only; "
                f"{query_date.year} is out of coverage",
            )

        date_str = query_date.isoformat()

        special = FALLBACK_SPECIAL_SESSIONS.get(code, {}).get(date_str)
        if special is not None:
            # Checked BEFORE the weekmask: these sessions fall outside the normal
            # trading week, so a weekmask test would report a live market closed.
            return self._unknown(
                code,
                query_date,
                f"Special session outside the normal trading week ({special}); "
                f"session times must be sourced from the exchange notice",
            )

        if not _weekmask_allows(profile.weekmask, query_date):
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.WEEKEND_CLOSED,
                open_utc=None,
                close_utc=None,
                notes=f"Outside trading week (weekmask {profile.weekmask})",
                source=CalendarSource.STATIC_FALLBACK,
            )

        holiday_name = FALLBACK_HOLIDAYS.get(code, {}).get(date_str)
        if holiday_name is not None:
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.FULL_DAY_HOLIDAY,
                open_utc=None,
                close_utc=None,
                notes=f"Holiday: {holiday_name}",
                source=CalendarSource.STATIC_FALLBACK,
            )

        open_utc = _local_time_to_utc(
            query_date, profile.regular_open_local, profile.tz
        )

        early_close_local = FALLBACK_HALF_DAYS.get(code, {}).get(date_str)
        if early_close_local is not None:
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.HALF_DAY_EARLY_CLOSE,
                open_utc=open_utc,
                close_utc=_local_time_to_utc(
                    query_date, early_close_local, profile.tz
                ),
                notes=f"Half-day early close at {early_close_local.isoformat()} local",
                source=CalendarSource.STATIC_FALLBACK,
            )

        return SessionInfo(
            exchange_code=code,
            date=query_date,
            status=SessionStatus.REGULAR_SESSION,
            open_utc=open_utc,
            close_utc=_local_time_to_utc(
                query_date, profile.regular_close_local, profile.tz
            ),
            notes="Regular trading session",
            source=CalendarSource.STATIC_FALLBACK,
        )

    @staticmethod
    def _unknown(code: str, query_date: datetime.date, reason: str) -> SessionInfo:
        logger.error("No session data for %s on %s: %s", code, query_date, reason)
        return SessionInfo(
            exchange_code=code,
            date=query_date,
            status=SessionStatus.UNKNOWN_NO_CALENDAR_DATA,
            open_utc=None,
            close_utc=None,
            notes=reason,
            source=CalendarSource.NONE,
        )

    def is_trading_day(self, exchange_code: str, query_date: datetime.date) -> bool:
        """True if the exchange holds a session (full or half day) on this date.

        Raises `CalendarDataUnavailable` when the session is unknown. Collapsing
        "unknown" into False would silently suppress a whole trading day; the
        caller must decide whether to halt, escalate, or fetch a better calendar.
        """
        return self.get_session_info(exchange_code, query_date).is_tradeable


def _reject_datetime(value: Any, exchange_code: str) -> None:
    """Require a `date`, not a `datetime`, at every public boundary.

    A `datetime` names an instant, and one instant falls on different calendar
    dates in different exchange timezones; calling `.date()` on the caller's
    behalf would quietly assume the caller's zone is the exchange's.
    """
    if isinstance(value, datetime.datetime):
        raise TypeError(
            f"Pass a datetime.date for {exchange_code}, not a datetime.datetime -- "
            "convert the instant in the exchange's own timezone first."
        )
    if not isinstance(value, datetime.date):
        raise TypeError("query_date must be a datetime.date")


def is_trading_day(
    exchange_code: str,
    date: datetime.date,
    calendar_lib: Optional[Any] = None,
) -> bool:
    """Module-level convenience wrapper around `GlobalExchangeCalendarManager`."""
    mgr = GlobalExchangeCalendarManager(calendar_lib=calendar_lib)
    return mgr.is_trading_day(exchange_code, date)


def session_open_close(
    exchange_code: str,
    date: datetime.date,
    calendar_lib: Optional[Any] = None,
) -> Tuple[Optional[datetime.datetime], Optional[datetime.datetime]]:
    """Return (open_utc, close_utc), or (None, None) if the exchange is closed.

    Raises `CalendarDataUnavailable` when the session is unknown, so an unknown
    calendar cannot be mistaken for a closed market.
    """
    mgr = GlobalExchangeCalendarManager(calendar_lib=calendar_lib)
    info = mgr.get_session_info(exchange_code, date)
    if info.status is SessionStatus.UNKNOWN_NO_CALENDAR_DATA:
        raise CalendarDataUnavailable(
            f"No session data for {exchange_code} on {date}: {info.notes}"
        )
    if not info.is_tradeable:
        return None, None
    return info.open_utc, info.close_utc
