"""
multi-timezone-session-scheduling: Production-grade IANA zone-aware multi-exchange scheduler,
DST transition handling (spring forward / fall back), cross-exchange market overlap calculator,
and UTC session status evaluator.
"""
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class MarketSessionState(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    REGULAR_TRADING = "REGULAR_TRADING"
    POST_MARKET = "POST_MARKET"
    MARKET_CLOSED = "MARKET_CLOSED"


@dataclass
class ExchangeSchedule:
    exchange_code: str
    iana_timezone: str
    open_time: datetime.time
    close_time: datetime.time
    pre_market_time: Optional[datetime.time] = None
    post_market_time: Optional[datetime.time] = None


DEFAULT_EXCHANGE_SCHEDULES: Dict[str, ExchangeSchedule] = {
    "XNYS": ExchangeSchedule(
        exchange_code="XNYS",
        iana_timezone="America/New_York",
        open_time=datetime.time(9, 30),
        close_time=datetime.time(16, 0),
        pre_market_time=datetime.time(4, 0),
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
    ),
    "XTKS": ExchangeSchedule(
        exchange_code="XTKS",
        iana_timezone="Asia/Tokyo",
        open_time=datetime.time(9, 0),
        close_time=datetime.time(15, 0),
    ),
    "XASX": ExchangeSchedule(
        exchange_code="XASX",
        iana_timezone="Australia/Sydney",
        open_time=datetime.time(10, 0),
        close_time=datetime.time(16, 0),
    ),
}


class MultiTimezoneSessionScheduler:
    """
    Manages multi-exchange session schedules across IANA timezones,
    DST boundaries, and cross-market sequential handoffs.
    """

    def __init__(self, custom_schedules: Optional[Dict[str, ExchangeSchedule]] = None):
        self.schedules = custom_schedules or DEFAULT_EXCHANGE_SCHEDULES

    def register_schedule(self, schedule: ExchangeSchedule):
        self.schedules[schedule.exchange_code.upper()] = schedule

    def get_session_utc(
        self, exchange_code: str, local_date: datetime.date
    ) -> Tuple[datetime.datetime, datetime.datetime]:
        """
        Calculates exact UTC open and close timestamps for a specific exchange and calendar date.
        Fully accounts for DST shifts on that specific date.
        """
        code = exchange_code.upper()
        if code not in self.schedules:
            raise ValueError(f"Unknown exchange code: '{exchange_code}'")

        sched = self.schedules[code]
        tz = ZoneInfo(sched.iana_timezone)

        # Build zone-aware datetimes
        open_local = datetime.datetime.combine(local_date, sched.open_time, tzinfo=tz)
        close_local = datetime.datetime.combine(local_date, sched.close_time, tzinfo=tz)

        # Convert to UTC
        open_utc = open_local.astimezone(ZoneInfo("UTC"))
        close_utc = close_local.astimezone(ZoneInfo("UTC"))

        return open_utc, close_utc

    def get_market_status(
        self, exchange_code: str, query_time_utc: Optional[datetime.datetime] = None
    ) -> MarketSessionState:
        now_utc = query_time_utc or datetime.datetime.now(datetime.timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=datetime.timezone.utc)

        code = exchange_code.upper()
        if code not in self.schedules:
            return MarketSessionState.MARKET_CLOSED

        sched = self.schedules[code]
        tz = ZoneInfo(sched.iana_timezone)
        local_dt = now_utc.astimezone(tz)
        local_date = local_dt.date()

        # Weekend check
        if local_date.weekday() >= 5:
            return MarketSessionState.MARKET_CLOSED

        open_utc, close_utc = self.get_session_utc(code, local_date)

        if open_utc <= now_utc <= close_utc:
            return MarketSessionState.REGULAR_TRADING

        # Pre-market check
        if sched.pre_market_time:
            pre_local = datetime.datetime.combine(local_date, sched.pre_market_time, tzinfo=tz)
            pre_utc = pre_local.astimezone(ZoneInfo("UTC"))
            if pre_utc <= now_utc < open_utc:
                return MarketSessionState.PRE_MARKET

        # Post-market check
        if sched.post_market_time:
            post_local = datetime.datetime.combine(local_date, sched.post_market_time, tzinfo=tz)
            post_utc = post_local.astimezone(ZoneInfo("UTC"))
            if close_utc < now_utc <= post_utc:
                return MarketSessionState.POST_MARKET

        return MarketSessionState.MARKET_CLOSED

    def calculate_exchange_gap_minutes(
        self, exchange_a: str, exchange_b: str, local_date: datetime.date
    ) -> float:
        """
        Calculates UTC minute gap between close of exchange A and open of exchange B.
        A negative value indicates overlapping sessions.
        """
        _, close_a_utc = self.get_session_utc(exchange_a, local_date)
        open_b_utc, _ = self.get_session_utc(exchange_b, local_date)
        return (open_b_utc - close_a_utc).total_seconds() / 60.0


# Backward compatibility functions
def exchange_session_utc(local_date, open_time: datetime.time, close_time: datetime.time, exchange_tz: str):
    tz = ZoneInfo(exchange_tz)
    open_local = datetime.datetime.combine(local_date, open_time, tzinfo=tz)
    close_local = datetime.datetime.combine(local_date, close_time, tzinfo=tz)
    return open_local.astimezone(ZoneInfo("UTC")), close_local.astimezone(ZoneInfo("UTC"))


def cross_exchange_gap_minutes(close_a_utc, open_b_utc):
    return (open_b_utc - close_a_utc).total_seconds() / 60.0
