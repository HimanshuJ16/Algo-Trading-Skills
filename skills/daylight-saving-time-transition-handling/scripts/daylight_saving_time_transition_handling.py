import datetime
import zoneinfo
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ExchangeScheduleSpec:
    exchange_id: str
    exchange_name: str
    iana_timezone: str                  # e.g. 'America/New_York', 'Europe/London', 'Asia/Tokyo'
    local_open_time: str                # 'HH:MM' e.g. '09:30'
    local_close_time: str               # 'HH:MM' e.g. '16:00'

@dataclass
class UtcSessionWindow:
    exchange_id: str
    target_date: str                    # 'YYYY-MM-DD'
    is_dst_active: bool
    utc_open_iso: str
    utc_close_iso: str
    utc_open_ns: int
    utc_close_ns: int
    session_duration_hours: float

@dataclass
class DstTransitionAuditReport:
    target_date: str
    sessions: List[UtcSessionWindow]
    is_us_eu_desync_window: bool        # True during the 2-week March/Oct offset shift
    us_eu_overlap_hours: float
    warnings: List[str]

class DstTransitionHandlerEngine:
    """
    Quantitative market schedule and timezone engine for handling Daylight Saving Time (DST) shifts
    across US, EU, and Asian exchanges, calculating UTC session opens, and detecting 2-week desynchronization windows.
    """
    def __init__(self):
        self.exchanges: Dict[str, ExchangeScheduleSpec] = {}

    def register_exchange(self, spec: ExchangeScheduleSpec):
        self.exchanges[spec.exchange_id] = spec

    def calculate_utc_session(self, exchange_id: str, target_date_str: str) -> UtcSessionWindow:
        """
        Calculates UTC open/close timestamps and nanosecond epoch integers for an exchange on target_date_str.
        """
        if exchange_id not in self.exchanges:
            raise ValueError(f"Exchange {exchange_id} not registered.")

        spec = self.exchanges[exchange_id]
        tz = zoneinfo.ZoneInfo(spec.iana_timezone)

        date_parts = [int(x) for x in target_date_str.split("-")]
        year, month, day = date_parts[0], date_parts[1], date_parts[2]

        open_h, open_m = [int(x) for x in spec.local_open_time.split(":")]
        close_h, close_m = [int(x) for x in spec.local_close_time.split(":")]

        local_open_dt = datetime.datetime(year, month, day, open_h, open_m, tzinfo=tz)
        local_close_dt = datetime.datetime(year, month, day, close_h, close_m, tzinfo=tz)

        utc_open_dt = local_open_dt.astimezone(datetime.timezone.utc)
        utc_close_dt = local_close_dt.astimezone(datetime.timezone.utc)

        utc_open_ns = int(utc_open_dt.timestamp() * 1_000_000_000)
        utc_close_ns = int(utc_close_dt.timestamp() * 1_000_000_000)

        duration_hours = round((utc_close_ns - utc_open_ns) / (3600.0 * 1_000_000_000), 2)
        is_dst = bool(local_open_dt.dst())

        return UtcSessionWindow(
            exchange_id=exchange_id,
            target_date=target_date_str,
            is_dst_active=is_dst,
            utc_open_iso=utc_open_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            utc_close_iso=utc_close_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            utc_open_ns=utc_open_ns,
            utc_close_ns=utc_close_ns,
            session_duration_hours=duration_hours
        )

    def audit_global_dst_transitions(self, target_date_str: str) -> DstTransitionAuditReport:
        """
        Audits UTC market sessions across registered exchanges on target_date_str,
        detecting 2-week March/October US-EU DST desynchronization windows.
        """
        sessions = [self.calculate_utc_session(ex_id, target_date_str) for ex_id in self.exchanges]
        
        # Check US vs EU DST state (NYSE vs LSE)
        us_session = next((s for s in sessions if s.exchange_id == "NYSE"), None)
        eu_session = next((s for s in sessions if s.exchange_id == "LSE"), None)

        is_desync = False
        overlap_hours = 0.0
        warnings = []

        if us_session and eu_session:
            # Under standard alignment (both DST or both Standard), US is in DST iff EU is in DST
            if us_session.is_dst_active != eu_session.is_dst_active:
                is_desync = True
                msg = (
                    f"2-WEEK US-EU DST DESYNCHRONIZATION WINDOW ACTIVE on {target_date_str}! "
                    f"US DST={us_session.is_dst_active}, EU DST={eu_session.is_dst_active}. Recalibrating cross-border arb timers!"
                )
                warnings.append(msg)
                logger.warning(msg)

            # Calculate UTC Overlap Window
            overlap_open = max(us_session.utc_open_ns, eu_session.utc_open_ns)
            overlap_close = min(us_session.utc_close_ns, eu_session.utc_close_ns)
            if overlap_close > overlap_open:
                overlap_hours = round((overlap_close - overlap_open) / (3600.0 * 1_000_000_000), 2)

        return DstTransitionAuditReport(
            target_date=target_date_str,
            sessions=sessions,
            is_us_eu_desync_window=is_desync,
            us_eu_overlap_hours=overlap_hours,
            warnings=warnings
        )
