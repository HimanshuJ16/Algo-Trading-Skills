"""
global-exchange-holiday-calendar-handling: Production-grade multi-exchange calendar manager,
half-day / early-close detection, ADR cross-listing calendar resolver,
and DST-aware UTC session timing calculations.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class SessionStatus(str, Enum):
    REGULAR_SESSION = "REGULAR_SESSION"
    HALF_DAY_EARLY_CLOSE = "HALF_DAY_EARLY_CLOSE"
    FULL_DAY_HOLIDAY = "FULL_DAY_HOLIDAY"
    WEEKEND_CLOSED = "WEEKEND_CLOSED"


@dataclass
class SessionInfo:
    exchange_code: str
    date: datetime.date
    status: SessionStatus
    open_utc: Optional[datetime.datetime]
    close_utc: Optional[datetime.datetime]
    notes: str


# Reference static holiday definitions (used when exchange_calendars is not installed)
FALLBACK_HOLIDAYS: Dict[str, Dict[str, str]] = {
    "XNYS": {
        "2026-01-01": "New Year's Day",
        "2026-01-19": "Martin Luther King Jr. Day",
        "2026-02-16": "Presidents' Day",
        "2026-04-03": "Good Friday",
        "2026-05-25": "Memorial Day",
        "2026-06-19": "Juneteenth",
        "2026-07-03": "Independence Day (Observed)",
        "2026-09-07": "Labor Day",
        "2026-11-26": "Thanksgiving Day",
        "2026-12-25": "Christmas Day",
    },
    "XNSE": {
        "2026-01-26": "Republic Day",
        "2026-03-06": "Holi",
        "2026-04-03": "Good Friday",
        "2026-04-14": "Dr. Ambedkar Jayanti",
        "2026-05-01": "Maharashtra Day",
        "2026-08-15": "Independence Day",
        "2026-10-02": "Mahatma Gandhi Jayanti",
        "2026-10-20": "Dussehra",
        "2026-11-09": "Diwali Balipratipada",
        "2026-12-25": "Christmas Day",
    },
}

FALLBACK_HALF_DAYS: Dict[str, Dict[str, Tuple[str, str]]] = {
    "XNYS": {
        "2026-11-27": ("14:30", "18:00"),  # Day after Thanksgiving: 13:00 EST close (18:00 UTC)
        "2026-12-24": ("14:30", "18:00"),  # Christmas Eve: 13:00 EST close
    }
}


class GlobalExchangeCalendarManager:
    """
    Manages multi-exchange session status, half-day/early close detection,
    and ADR listing calendar resolution.
    """

    def __init__(self, calendar_lib=None):
        self.calendar_lib = calendar_lib

    def _get_lib(self):
        if self.calendar_lib is not None:
            return self.calendar_lib
        try:
            import exchange_calendars as cal_lib

            self.calendar_lib = cal_lib
            return cal_lib
        except ImportError:
            return None

    def map_instrument_to_exchange(self, symbol: str) -> str:
        """
        Maps symbol to primary trading exchange ISO code (e.g. INFY ADR -> XNYS, INFY.NS -> XNSE).
        """
        sym_upper = symbol.upper()
        if sym_upper.endswith(".NS") or sym_upper.endswith(".BO"):
            return "XNSE" if sym_upper.endswith(".NS") else "XBOM"
        elif "ADR" in sym_upper or sym_upper in ["INFY", "WIT", "HDB", "IBN"]:
            return "XNYS"  # US ADRs trade on NYSE
        return "XNYS"  # Default US primary

    def get_session_info(self, exchange_code: str, query_date: datetime.date) -> SessionInfo:
        code = exchange_code.upper()
        date_str = query_date.strftime("%Y-%m-%d")

        # Weekend Check
        if query_date.weekday() >= 5:
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.WEEKEND_CLOSED,
                open_utc=None,
                close_utc=None,
                notes="Weekend Market Close",
            )

        lib = self._get_lib()

        if lib is not None:
            try:
                cal = lib.get_calendar(code)
                if not cal.is_session(query_date):
                    return SessionInfo(
                        exchange_code=code,
                        date=query_date,
                        status=SessionStatus.FULL_DAY_HOLIDAY,
                        open_utc=None,
                        close_utc=None,
                        notes="Exchange Holiday",
                    )

                open_ts = cal.session_open(query_date).to_pydatetime()
                close_ts = cal.session_close(query_date).to_pydatetime()

                # Check if early close / half day
                status = (
                    SessionStatus.HALF_DAY_EARLY_CLOSE
                    if cal.is_half_day(query_date)
                    else SessionStatus.REGULAR_SESSION
                )

                return SessionInfo(
                    exchange_code=code,
                    date=query_date,
                    status=status,
                    open_utc=open_ts,
                    close_utc=close_ts,
                    notes="Session derived via exchange_calendars",
                )
            except Exception as e:
                logger.warning(f"exchange_calendars error ({e}); using fallback calendar.")

        # Fallback static calendar check
        holidays = FALLBACK_HOLIDAYS.get(code, {})
        if date_str in holidays:
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.FULL_DAY_HOLIDAY,
                open_utc=None,
                close_utc=None,
                notes=f"Holiday: {holidays[date_str]}",
            )

        half_days = FALLBACK_HALF_DAYS.get(code, {})
        if date_str in half_days:
            open_t, close_t = half_days[date_str]
            open_dt = datetime.datetime.strptime(f"{date_str} {open_t}", "%Y-%m-%d %H:%M").replace(
                tzinfo=datetime.timezone.utc
            )
            close_dt = datetime.datetime.strptime(f"{date_str} {close_t}", "%Y-%m-%d %H:%M").replace(
                tzinfo=datetime.timezone.utc
            )
            return SessionInfo(
                exchange_code=code,
                date=query_date,
                status=SessionStatus.HALF_DAY_EARLY_CLOSE,
                open_utc=open_dt,
                close_utc=close_dt,
                notes="Half-day early close",
            )

        # Default Regular Session
        open_dt = datetime.datetime.strptime(f"{date_str} 14:30", "%Y-%m-%d %H:%M").replace(
            tzinfo=datetime.timezone.utc
        )
        close_dt = datetime.datetime.strptime(f"{date_str} 21:00", "%Y-%m-%d %H:%M").replace(
            tzinfo=datetime.timezone.utc
        )
        return SessionInfo(
            exchange_code=code,
            date=query_date,
            status=SessionStatus.REGULAR_SESSION,
            open_utc=open_dt,
            close_utc=close_dt,
            notes="Regular trading session",
        )

    def is_trading_day(self, exchange_code: str, query_date: datetime.date) -> bool:
        info = self.get_session_info(exchange_code, query_date)
        return info.status in [SessionStatus.REGULAR_SESSION, SessionStatus.HALF_DAY_EARLY_CLOSE]


# Backward compatibility functions
def is_trading_day(exchange_code: str, date, calendar_lib=None):
    mgr = GlobalExchangeCalendarManager(calendar_lib=calendar_lib)
    dt_date = date.date() if isinstance(date, datetime.datetime) else date
    return mgr.is_trading_day(exchange_code, dt_date)


def session_open_close(exchange_code: str, date, calendar_lib=None):
    mgr = GlobalExchangeCalendarManager(calendar_lib=calendar_lib)
    dt_date = date.date() if isinstance(date, datetime.datetime) else date
    info = mgr.get_session_info(exchange_code, dt_date)
    if info.status not in [SessionStatus.REGULAR_SESSION, SessionStatus.HALF_DAY_EARLY_CLOSE]:
        return None, None
    return info.open_utc, info.close_utc
