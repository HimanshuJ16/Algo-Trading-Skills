"""
daylight-saving-time-transition-handling: IANA zone-aware market session engine that
converts exchange-local session times to UTC (and UTC nanosecond epochs), flags
non-existent / ambiguous local wall times around DST transitions, and detects the
US-EU DST desynchronisation windows that shift the transatlantic overlap by one hour.

Rule sources (never hard-coded into the conversion path -- offsets always come from
the IANA tz database via `zoneinfo`):
  * US:  15 U.S.C. 260a -- DST from 02:00 *local* on the 2nd Sunday of March to
         02:00 local on the 1st Sunday of November.
  * EU:  Directive 2000/84/EC, Arts. 2-3 -- summer time from 01:00 *GMT* on the last
         Sunday of March to 01:00 GMT on the last Sunday of October, simultaneously
         in every Member State.
"""
import datetime
import logging
import zoneinfo
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc
_NS_PER_HOUR = 3600.0 * 1_000_000_000

# Exchange time zones that follow the US federal DST rule (15 U.S.C. 260a).
US_DST_TIMEZONES: Tuple[str, ...] = (
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Toronto",
)

# Exchange time zones that follow the EU summer-time rule (Directive 2000/84/EC).
# The UK is included: the Summer Time Order keeps Europe/London on the same
# last-Sunday-of-March / last-Sunday-of-October transitions post-Brexit.
# Deliberately excluded: Europe/Moscow and Europe/Istanbul observe no DST at all.
EU_DST_TIMEZONES: Tuple[str, ...] = (
    "Europe/London",
    "Europe/Dublin",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Amsterdam",
    "Europe/Brussels",
    "Europe/Madrid",
    "Europe/Rome",
    "Europe/Zurich",
    "Europe/Vienna",
    "Europe/Stockholm",
    "Europe/Oslo",
    "Europe/Copenhagen",
    "Europe/Helsinki",
    "Europe/Lisbon",
    "Europe/Warsaw",
    "Europe/Prague",
    "Europe/Budapest",
    "Europe/Athens",
)


class DstScheduleError(ValueError):
    """Raised for an invalid schedule registration or an unusable local session time."""


@dataclass
class ExchangeScheduleSpec:
    exchange_id: str
    exchange_name: str
    iana_timezone: str                  # e.g. 'America/New_York', 'Europe/London', 'Asia/Tokyo'
    local_open_time: str                # 'HH:MM' e.g. '09:30'
    local_close_time: str               # 'HH:MM' e.g. '16:00'
    spans_midnight: bool = False        # True for overnight sessions, e.g. CME 17:00 -> 16:00


@dataclass
class UtcSessionWindow:
    exchange_id: str
    target_date: str                    # 'YYYY-MM-DD'
    is_dst_active: bool                 # DST state at the session OPEN
    utc_open_iso: str
    utc_close_iso: str
    utc_open_ns: int
    utc_close_ns: int
    session_duration_hours: float
    # --- DST-transition diagnostics (default-valued: positional construction unchanged) ---
    is_dst_active_at_close: bool = False
    utc_offset_open_hours: float = 0.0
    utc_offset_close_hours: float = 0.0
    dst_shift_inside_session: bool = False   # transition falls between open and close
    local_open_is_nonexistent: bool = False  # wall time skipped by 'spring forward'
    local_open_is_ambiguous: bool = False    # wall time repeated by 'fall back'
    warnings: List[str] = field(default_factory=list)


@dataclass
class DstTransitionAuditReport:
    target_date: str
    sessions: List[UtcSessionWindow]
    is_us_eu_desync_window: bool        # True while US and EU DST states disagree
    us_eu_overlap_hours: float
    warnings: List[str]
    # --- audit provenance (default-valued: positional construction unchanged) ---
    us_exchange_id: Optional[str] = None
    eu_exchange_id: Optional[str] = None
    us_eu_offset_delta_hours: Optional[float] = None  # US UTC offset minus EU UTC offset


def _parse_hh_mm(value: str, label: str, exchange_id: str) -> Tuple[int, int]:
    """Parses an 'HH:MM' string, raising a message that names the offending exchange."""
    parts = value.split(":")
    if len(parts) != 2:
        raise DstScheduleError(f"{exchange_id}: {label} '{value}' is not in 'HH:MM' form.")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise DstScheduleError(
            f"{exchange_id}: {label} '{value}' is not in 'HH:MM' form."
        ) from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise DstScheduleError(f"{exchange_id}: {label} '{value}' is outside 00:00-23:59.")
    return hour, minute


def _parse_date(target_date_str: str) -> datetime.date:
    """Parses an ISO 'YYYY-MM-DD' date, rejecting other formats explicitly."""
    try:
        return datetime.date.fromisoformat(target_date_str)
    except (ValueError, TypeError) as exc:
        raise DstScheduleError(
            f"target_date '{target_date_str}' is not a valid ISO 'YYYY-MM-DD' date."
        ) from exc


def _localize(
    naive: datetime.datetime, tz: zoneinfo.ZoneInfo
) -> Tuple[datetime.datetime, bool, bool]:
    """
    Attaches `tz` to a naive wall time and classifies it against the DST transition.

    Returns (aware_datetime, is_nonexistent, is_ambiguous). Per PEP 495 the returned
    datetime uses fold=0, i.e. the FIRST occurrence of a repeated wall time; a
    non-existent wall time is normalised by the pre-transition offset. Both cases are
    reported rather than silently accepted.
    """
    aware = naive.replace(tzinfo=tz)
    # A skipped wall time does not survive a round trip through UTC.
    is_nonexistent = aware.astimezone(_UTC).astimezone(tz).replace(tzinfo=None) != naive
    # A repeated wall time has two distinct UTC offsets (fold=0 vs fold=1). Under PEP 495
    # a *skipped* wall time also yields two offsets, so ambiguity is the fold disagreement
    # that remains once the gap case is excluded -- the two are mutually exclusive.
    is_ambiguous = (
        not is_nonexistent and aware.utcoffset() != aware.replace(fold=1).utcoffset()
    )
    return aware, is_nonexistent, is_ambiguous


def _offset_hours(dt: datetime.datetime) -> float:
    """UTC offset of an aware datetime, in hours."""
    offset = dt.utcoffset() or datetime.timedelta(0)
    return round(offset.total_seconds() / 3600.0, 4)


@lru_cache(maxsize=512)
def _standard_offset(tz_key: str, year: int) -> datetime.timedelta:
    """
    The zone's winter (standard) UTC offset for `year`, taken as the minimum offset over
    quarterly UTC probes. Probing from UTC avoids the fold ambiguity of local wall times,
    and sampling all four quarters covers both hemispheres.
    """
    tz = zoneinfo.ZoneInfo(tz_key)
    offsets = []
    for month in (1, 4, 7, 10):
        probe = datetime.datetime(year, month, 1, 12, 0, tzinfo=_UTC).astimezone(tz)
        offsets.append(probe.utcoffset() or datetime.timedelta(0))
    return min(offsets)


def _is_summer_time(aware: datetime.datetime, tz_key: str) -> bool:
    """
    True when the zone's clocks are advanced relative to their own standard offset.

    Deliberately NOT `bool(dt.dst())`. The IANA database models some zones with *negative*
    DST -- Europe/Dublin defines Irish Standard Time as the summer offset and treats GMT as
    a negative-DST winter, so `dst()` there is truthy in January and falsy in July, exactly
    inverting the meaning a trading scheduler needs. Comparing against the zone's own
    standard offset is correct for negative-DST zones, for the southern hemisphere, and for
    fractional-hour DST zones such as Australia/Lord_Howe.
    """
    offset = aware.utcoffset() or datetime.timedelta(0)
    return offset > _standard_offset(tz_key, aware.year)


class DstTransitionHandlerEngine:
    """
    Market schedule and time zone engine for Daylight Saving Time transitions across US,
    EU, and Asian exchanges. Computes UTC session windows and nanosecond epochs, flags
    local wall times that are skipped or repeated by a DST transition, and detects the
    US-EU desynchronisation windows in March and October/November.

    Args:
        strict: when True, a session boundary that falls on a skipped ('spring forward')
            or repeated ('fall back') local wall time raises `DstScheduleError` instead
            of being resolved with the fold=0 default and flagged.
    """

    def __init__(self, strict: bool = False) -> None:
        self.exchanges: Dict[str, ExchangeScheduleSpec] = {}
        self.strict = strict

    def register_exchange(self, spec: ExchangeScheduleSpec) -> None:
        """Validates and registers an exchange schedule. Re-registering an id replaces it."""
        try:
            zoneinfo.ZoneInfo(spec.iana_timezone)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError) as exc:
            raise DstScheduleError(
                f"{spec.exchange_id}: '{spec.iana_timezone}' is not a resolvable IANA time "
                f"zone. On hosts without a system tz database (notably Windows) install the "
                f"`tzdata` package."
            ) from exc

        open_h, open_m = _parse_hh_mm(spec.local_open_time, "local_open_time", spec.exchange_id)
        close_h, close_m = _parse_hh_mm(spec.local_close_time, "local_close_time", spec.exchange_id)

        if not spec.spans_midnight and (close_h, close_m) <= (open_h, open_m):
            raise DstScheduleError(
                f"{spec.exchange_id}: local_close_time '{spec.local_close_time}' is not after "
                f"local_open_time '{spec.local_open_time}'. Set spans_midnight=True for an "
                f"overnight session."
            )

        self.exchanges[spec.exchange_id] = spec

    def calculate_utc_session(self, exchange_id: str, target_date_str: str) -> UtcSessionWindow:
        """
        Calculates the UTC open/close datetimes and nanosecond epochs for `exchange_id`
        on `target_date_str`, resolving the IANA offset independently at each boundary
        rather than caching one offset for the whole day.
        """
        if exchange_id not in self.exchanges:
            raise DstScheduleError(f"Exchange {exchange_id} not registered.")

        spec = self.exchanges[exchange_id]
        tz = zoneinfo.ZoneInfo(spec.iana_timezone)
        target_date = _parse_date(target_date_str)

        open_h, open_m = _parse_hh_mm(spec.local_open_time, "local_open_time", exchange_id)
        close_h, close_m = _parse_hh_mm(spec.local_close_time, "local_close_time", exchange_id)

        close_date = target_date + datetime.timedelta(days=1) if spec.spans_midnight else target_date

        naive_open = datetime.datetime.combine(target_date, datetime.time(open_h, open_m))
        naive_close = datetime.datetime.combine(close_date, datetime.time(close_h, close_m))

        local_open_dt, open_nonexistent, open_ambiguous = _localize(naive_open, tz)
        local_close_dt, close_nonexistent, close_ambiguous = _localize(naive_close, tz)

        warnings: List[str] = []
        for label, wall_time, nonexistent, ambiguous in (
            ("open", spec.local_open_time, open_nonexistent, open_ambiguous),
            ("close", spec.local_close_time, close_nonexistent, close_ambiguous),
        ):
            if nonexistent:
                msg = (
                    f"{exchange_id}: local session {label} {wall_time} does not exist on "
                    f"{target_date_str} in {spec.iana_timezone} -- the 'spring forward' "
                    f"transition skips this wall time. Resolved using the pre-transition offset."
                )
            elif ambiguous:
                msg = (
                    f"{exchange_id}: local session {label} {wall_time} occurs twice on "
                    f"{target_date_str} in {spec.iana_timezone} -- the 'fall back' transition "
                    f"repeats this wall time. Resolved to the FIRST (pre-transition) occurrence."
                )
            else:
                continue
            if self.strict:
                raise DstScheduleError(msg)
            warnings.append(msg)
            logger.warning(msg)

        utc_open_dt = local_open_dt.astimezone(_UTC)
        utc_close_dt = local_close_dt.astimezone(_UTC)

        utc_open_ns = int(utc_open_dt.timestamp() * 1_000_000_000)
        utc_close_ns = int(utc_close_dt.timestamp() * 1_000_000_000)

        duration_hours = round((utc_close_ns - utc_open_ns) / _NS_PER_HOUR, 2)

        offset_open = _offset_hours(local_open_dt)
        offset_close = _offset_hours(local_close_dt)
        dst_shift_inside_session = offset_open != offset_close
        if dst_shift_inside_session:
            msg = (
                f"{exchange_id}: a DST transition falls inside the {target_date_str} session "
                f"({offset_open:+g}h -> {offset_close:+g}h). Elapsed session length is "
                f"{duration_hours}h, not the nominal local-clock span; do not aggregate bars "
                f"on local wall time across this session."
            )
            warnings.append(msg)
            logger.warning(msg)

        return UtcSessionWindow(
            exchange_id=exchange_id,
            target_date=target_date_str,
            is_dst_active=_is_summer_time(local_open_dt, spec.iana_timezone),
            utc_open_iso=utc_open_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            utc_close_iso=utc_close_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            utc_open_ns=utc_open_ns,
            utc_close_ns=utc_close_ns,
            session_duration_hours=duration_hours,
            is_dst_active_at_close=_is_summer_time(local_close_dt, spec.iana_timezone),
            utc_offset_open_hours=offset_open,
            utc_offset_close_hours=offset_close,
            dst_shift_inside_session=dst_shift_inside_session,
            local_open_is_nonexistent=open_nonexistent,
            local_open_is_ambiguous=open_ambiguous,
            warnings=warnings,
        )

    def _resolve_leg(
        self, explicit_id: Optional[str], legacy_id: str, zones: Tuple[str, ...], label: str
    ) -> Tuple[Optional[str], List[str]]:
        """Resolves which registered exchange represents one side of the US-EU audit."""
        warnings: List[str] = []
        if explicit_id is not None:
            if explicit_id not in self.exchanges:
                raise DstScheduleError(f"Exchange {explicit_id} not registered.")
            return explicit_id, warnings
        if legacy_id in self.exchanges:
            return legacy_id, warnings

        candidates = [
            ex_id for ex_id, spec in self.exchanges.items() if spec.iana_timezone in zones
        ]
        if not candidates:
            return None, warnings
        if len(candidates) > 1:
            warnings.append(
                f"Multiple {label} exchanges registered ({', '.join(candidates)}); using "
                f"'{candidates[0]}' for the US-EU DST audit. Pass "
                f"{label.lower()}_exchange_id to choose explicitly."
            )
        return candidates[0], warnings

    def audit_global_dst_transitions(
        self,
        target_date_str: str,
        us_exchange_id: Optional[str] = None,
        eu_exchange_id: Optional[str] = None,
    ) -> DstTransitionAuditReport:
        """
        Audits UTC market sessions across all registered exchanges on `target_date_str` and
        detects the US-EU DST desynchronisation windows.

        The US and EU legs are resolved by IANA time zone (falling back to the legacy 'NYSE'
        and 'LSE' ids, or an explicit override), so the audit does not silently no-op when
        exchanges are registered under MIC codes such as XNYS/XLON.

        Window lengths follow from the two statutes and are NOT a fixed two weeks:
          * March: the US starts DST on the 2nd Sunday and the EU on the last Sunday, so the
            gap is 14 days in most years but 21 days whenever 1 March is a Sunday (e.g. 2020,
            2024, 2025, 2026, 2030, 2031).
          * Autumn: the EU ends summer time on the last Sunday of October and the US on the
            1st Sunday of November, so the gap is always exactly 7 days.
        """
        sessions = [self.calculate_utc_session(ex_id, target_date_str) for ex_id in self.exchanges]

        warnings: List[str] = []
        us_id, leg_warnings = self._resolve_leg(us_exchange_id, "NYSE", US_DST_TIMEZONES, "US")
        warnings.extend(leg_warnings)
        eu_id, leg_warnings = self._resolve_leg(eu_exchange_id, "LSE", EU_DST_TIMEZONES, "EU")
        warnings.extend(leg_warnings)

        us_session = next((s for s in sessions if s.exchange_id == us_id), None)
        eu_session = next((s for s in sessions if s.exchange_id == eu_id), None)

        is_desync = False
        overlap_hours = 0.0
        offset_delta: Optional[float] = None

        if us_session is None or eu_session is None:
            missing = "US" if us_session is None else "EU"
            msg = (
                f"US-EU DST desynchronisation audit SKIPPED for {target_date_str}: no registered "
                f"exchange resolved for the {missing} leg. Register a US and an EU exchange, or "
                f"pass us_exchange_id / eu_exchange_id explicitly."
            )
            warnings.append(msg)
            logger.warning(msg)
        else:
            offset_delta = round(
                us_session.utc_offset_open_hours - eu_session.utc_offset_open_hours, 4
            )
            # Outside a desync window the US and EU are either both on DST or both on
            # standard time; a mismatch is exactly the one-hour transatlantic shift.
            if us_session.is_dst_active != eu_session.is_dst_active:
                is_desync = True
                msg = (
                    f"US-EU DST DESYNCHRONISATION WINDOW ACTIVE on {target_date_str}! "
                    f"US ({us_session.exchange_id}) DST={us_session.is_dst_active}, "
                    f"EU ({eu_session.exchange_id}) DST={eu_session.is_dst_active}; UTC offset "
                    f"delta {offset_delta:+g}h. The transatlantic overlap is shifted by one "
                    f"hour -- recalibrate cross-border timers."
                )
                warnings.append(msg)
                logger.warning(msg)

            overlap_open = max(us_session.utc_open_ns, eu_session.utc_open_ns)
            overlap_close = min(us_session.utc_close_ns, eu_session.utc_close_ns)
            if overlap_close > overlap_open:
                overlap_hours = round((overlap_close - overlap_open) / _NS_PER_HOUR, 2)

        for session in sessions:
            warnings.extend(session.warnings)

        return DstTransitionAuditReport(
            target_date=target_date_str,
            sessions=sessions,
            is_us_eu_desync_window=is_desync,
            us_eu_overlap_hours=overlap_hours,
            warnings=warnings,
            us_exchange_id=us_id if us_session is not None else None,
            eu_exchange_id=eu_id if eu_session is not None else None,
            us_eu_offset_delta_hours=offset_delta,
        )
