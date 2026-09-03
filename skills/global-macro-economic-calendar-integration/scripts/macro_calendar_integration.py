"""Global macro economic calendar integration.

Decision engine that answers one question for a live trading system: **may I
trade right now, given the scheduled macroeconomic release calendar?** It also
computes the standardised surprise of a release once the actual print is out.

Two things this module treats as safety-critical:

* **It fails closed.** An empty calendar, a calendar whose as-of timestamp is
  older than the configured tolerance, and an event carrying an impact severity
  the engine does not recognise all block trading rather than silently reading
  as "no event scheduled". A macro blackout gate that cannot distinguish
  "nothing scheduled" from "the feed never loaded" is not a risk control. The
  2025 US federal shutdown is the concrete case: scheduled BLS releases slipped
  by weeks and the October 2025 CPI and Employment Situation reports were
  cancelled outright, so a cached calendar can be both confidently wrong and
  indefinitely stale.
* **It does not look ahead.** ``actual_release`` is only read once
  ``as_of_utc >= release_timestamp_utc``. A calendar row fetched today carries
  the actual for a release that, in a backtest replaying that row, has not
  happened yet.

Timestamps are **epoch seconds UTC** throughout. Build them with
:func:`parse_release_timestamp` or :func:`release_timestamp_from_local` — both
refuse the naive-timestamp inputs that silently shift a blackout window by the
host's UTC offset.

See ``references/standards.md`` for the sourced release times and the
surprise-index definition this module implements.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# --- Impact severities ---------------------------------------------------------
# Canonical severity vocabulary. Anything outside this set is rejected rather
# than skipped: a vendor code the engine does not understand must not read as
# "no blackout". Trading Economics, for example, encodes importance as the
# integers 1/2/3, not as these strings -- map it with normalize_impact_severity.
HIGH_IMPACT = "HIGH_IMPACT"
MEDIUM_IMPACT = "MEDIUM_IMPACT"
LOW_IMPACT = "LOW_IMPACT"
VALID_IMPACT_SEVERITIES: Tuple[str, ...] = (HIGH_IMPACT, MEDIUM_IMPACT, LOW_IMPACT)

# Severities that can open a blackout window. LOW_IMPACT never blocks.
BLOCKING_SEVERITIES: Tuple[str, ...] = (HIGH_IMPACT, MEDIUM_IMPACT)

_SEVERITY_ALIASES: Dict[str, str] = {
    "1": LOW_IMPACT,
    "LOW": LOW_IMPACT,
    "LOW IMPACT": LOW_IMPACT,
    "LOW_IMPACT": LOW_IMPACT,
    "2": MEDIUM_IMPACT,
    "MEDIUM": MEDIUM_IMPACT,
    "MEDIUM IMPACT": MEDIUM_IMPACT,
    "MEDIUM_IMPACT": MEDIUM_IMPACT,
    "MODERATE": MEDIUM_IMPACT,
    "3": HIGH_IMPACT,
    "HIGH": HIGH_IMPACT,
    "HIGH IMPACT": HIGH_IMPACT,
    "HIGH_IMPACT": HIGH_IMPACT,
}

# --- Audit statuses ------------------------------------------------------------
STATUS_PERMITTED = "MACRO_TRADING_PERMITTED"
STATUS_BLACKOUT = "MACRO_BLACKOUT_ACTIVE"
STATUS_CALENDAR_UNAVAILABLE = "MACRO_CALENDAR_UNAVAILABLE"
STATUS_CALENDAR_STALE = "MACRO_CALENDAR_STALE"

DEFAULT_SURPRISE_LOOKBACK_SEC = 86_400.0


def normalize_impact_severity(value: object) -> str:
    """Map a vendor impact/importance code onto the canonical severity vocabulary.

    Accepts the integers and strings ``1``/``2``/``3`` used by Trading Economics'
    ``Importance`` field, the bare words ``low``/``medium``/``high``, and the
    canonical ``*_IMPACT`` names. Case and surrounding whitespace are ignored.

    Args:
        value: The vendor-supplied severity code.

    Returns:
        One of ``HIGH_IMPACT``, ``MEDIUM_IMPACT``, ``LOW_IMPACT``.

    Raises:
        ValueError: If the code is not recognised. Deliberately noisy -- mapping
            an unknown code onto "low" would silently disable the blackout for
            whatever the vendor actually meant.
    """
    if isinstance(value, bool):
        raise ValueError(f"impact severity must not be a bool, got {value!r}.")
    if isinstance(value, int):
        key = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise ValueError(f"Unrecognised impact severity {value!r}.")
        key = str(int(value))
    elif isinstance(value, str):
        key = value.strip().upper().replace("-", "_")
    else:
        raise ValueError(f"Unrecognised impact severity {value!r}.")

    normalized = _SEVERITY_ALIASES.get(key) or _SEVERITY_ALIASES.get(key.replace("_", " "))
    if normalized is None:
        raise ValueError(
            f"Unrecognised impact severity {value!r}. Expected one of "
            f"{VALID_IMPACT_SEVERITIES} or a vendor code this function maps "
            f"(1/2/3, low/medium/high). Unknown codes are rejected rather than "
            f"defaulted, because defaulting one to 'low' silently removes a "
            f"blackout the vendor asked for."
        )
    return normalized


def parse_release_timestamp(value: str) -> float:
    """Parse an ISO-8601 release timestamp into epoch seconds UTC.

    Rejects timestamps carrying no UTC offset. Several calendar vendors document
    their timestamps as UTC but serialise them without a designator -- Trading
    Economics returns ``"2023-03-30T00:00:00"``. ``datetime.fromisoformat`` maps
    that to a *naive* datetime, and ``naive.timestamp()`` then resolves it in the
    **host's** local zone, so the same calendar produces a different blackout
    window on a London box than on a Mumbai box, with no error raised.

    Args:
        value: ISO-8601 timestamp with an explicit offset, e.g.
            ``'2026-01-28T19:00:00Z'`` or ``'2026-01-28T14:00:00-05:00'``.

    Returns:
        Epoch seconds UTC.

    Raises:
        ValueError: If the string is unparseable or carries no UTC offset. If
            your vendor documents UTC but omits the designator, append ``'Z'``
            at the ingestion boundary; do not let this function guess.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"release timestamp must be a non-empty ISO-8601 string, got {value!r}."
        )
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Unparseable ISO-8601 release timestamp {value!r}: {exc}") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"Release timestamp {value!r} has no UTC offset. A naive timestamp is "
            f"resolved against the host's local timezone, which shifts the blackout "
            f"window by the host offset without raising. Append 'Z' if the vendor "
            f"documents the field as UTC, or use release_timestamp_from_local()."
        )
    return parsed.timestamp()


def release_timestamp_from_local(local_wall_clock_iso: str, iana_timezone: str) -> float:
    """Convert a local wall-clock release time into epoch seconds UTC.

    Use this when the schedule of record is published in local time, which is the
    norm: the FOMC statement is issued "For release at 2:00 p.m. EDT" (EST in
    winter), and BLS publishes the CPI and the Employment Situation at 8:30 a.m.
    Eastern. Those are fixed *wall-clock* times, so their UTC instant moves by an
    hour twice a year. Hard-coding 18:00Z for the FOMC puts the blackout an hour
    wrong for every winter meeting.

    US and EU transition dates also differ -- the US switches on the second Sunday
    of March and the first Sunday of November (15 U.S.C. 260a) while the EU
    switches on the last Sunday of March and of October (Directive 2000/84/EC) --
    so for a few weeks each year the offset between an ET release and a CET
    release is not its usual value either.

    Args:
        local_wall_clock_iso: Naive ISO-8601 local time, e.g.
            ``'2026-01-28T14:00:00'``. Must NOT carry an offset; the zone comes
            from ``iana_timezone``.
        iana_timezone: IANA zone name, e.g. ``'America/New_York'``.

    Returns:
        Epoch seconds UTC.

    Raises:
        ValueError: If the string carries an offset, is unparseable, the zone is
            unknown, or the wall-clock time is ambiguous or non-existent because
            it falls inside a DST transition.
    """
    if not isinstance(local_wall_clock_iso, str) or not local_wall_clock_iso.strip():
        raise ValueError(
            f"local_wall_clock_iso must be a non-empty ISO-8601 string, got "
            f"{local_wall_clock_iso!r}."
        )
    try:
        naive = datetime.fromisoformat(local_wall_clock_iso.strip())
    except ValueError as exc:
        raise ValueError(
            f"Unparseable local wall-clock time {local_wall_clock_iso!r}: {exc}"
        ) from exc
    if naive.tzinfo is not None:
        raise ValueError(
            f"{local_wall_clock_iso!r} already carries an offset. Pass a naive "
            f"wall-clock time here, or use parse_release_timestamp() instead."
        )
    try:
        zone = ZoneInfo(iana_timezone)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError on a bad key
        raise ValueError(f"Unknown IANA timezone {iana_timezone!r}: {exc}") from exc

    early = naive.replace(tzinfo=zone, fold=0)
    late = naive.replace(tzinfo=zone, fold=1)
    if early.utcoffset() != late.utcoffset():
        raise ValueError(
            f"{local_wall_clock_iso!r} is ambiguous in {iana_timezone!r} -- it occurs "
            f"twice across the autumn DST transition. Supply the release time with an "
            f"explicit offset via parse_release_timestamp() instead."
        )
    # A non-existent (spring-forward gap) local time round-trips to a different
    # wall clock than the one supplied.
    if early.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError(
            f"{local_wall_clock_iso!r} does not exist in {iana_timezone!r} -- it falls "
            f"inside the spring-forward DST gap."
        )
    return early.timestamp()


@dataclass
class MacroEconomicEvent:
    """One scheduled macroeconomic release.

    Args:
        event_id: Stable, unique identifier from the calendar vendor. Used to
            deduplicate and to replace an event when the schedule changes.
        event_name: Human label, e.g. ``'FOMC_RATE_DECISION'``, ``'US_CPI_YOY'``.
        currency: ISO currency the release is denominated against, e.g. ``'USD'``.
            Only consulted when the caller passes ``relevant_currencies``.
        release_timestamp_utc: Scheduled release instant, **epoch seconds UTC**.
            Build it with :func:`parse_release_timestamp` or
            :func:`release_timestamp_from_local`.
        impact_severity: One of :data:`VALID_IMPACT_SEVERITIES`. Run vendor codes
            through :func:`normalize_impact_severity` first.
        consensus_forecast: Survey median expectation, in the release's own units.
        forecast_std_dev: Scale used to standardise the surprise -- the sample
            standard deviation of this indicator's *past* surprises, in the same
            units as the release. Required to obtain a comparable surprise index;
            omitting it yields ``None``, never a fabricated substitute.
        actual_release: The printed value. Never read before
            ``release_timestamp_utc``.
        higher_actual_is_positive_surprise: ``False`` for indicators where a
            higher print is worse news (unemployment rate, initial jobless
            claims), which flips the sign of the surprise.
        pre_event_buffer_override_sec: Per-event pre-release buffer, overriding
            the engine default. Set it for events whose risk window is not
            standard or not symmetric.
        post_event_buffer_override_sec: Per-event post-release buffer. FOMC
            decision day is the canonical case: the statement lands at 2:00 p.m.
            ET and the Chair's press conference begins at 2:30 p.m. ET, so a
            15-minute post buffer reopens trading 15 minutes before the second,
            frequently larger, move.
    """

    event_id: str
    event_name: str
    currency: str
    release_timestamp_utc: float
    impact_severity: str
    consensus_forecast: Optional[float] = None
    forecast_std_dev: Optional[float] = None
    actual_release: Optional[float] = None
    # Fields below were appended in 2.0.0 and default to the the older behaviour,
    # so existing keyword and positional construction keeps working unchanged.
    higher_actual_is_positive_surprise: bool = True
    pre_event_buffer_override_sec: Optional[float] = None
    post_event_buffer_override_sec: Optional[float] = None

    def validate(self) -> None:
        """Raise ``ValueError`` if this event cannot be used as a blackout trigger."""
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError(f"event_id must be a non-empty string, got {self.event_id!r}.")
        if not isinstance(self.event_name, str) or not self.event_name.strip():
            raise ValueError(f"event {self.event_id}: event_name must be a non-empty string.")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError(
                f"event {self.event_id}: currency must be a non-empty string, e.g. 'USD'."
            )
        if isinstance(self.release_timestamp_utc, bool) or not isinstance(
            self.release_timestamp_utc, (int, float)
        ):
            raise ValueError(
                f"event {self.event_id}: release_timestamp_utc must be epoch seconds "
                f"UTC, got {self.release_timestamp_utc!r}."
            )
        if not math.isfinite(self.release_timestamp_utc):
            raise ValueError(
                f"event {self.event_id}: release_timestamp_utc must be finite, got "
                f"{self.release_timestamp_utc!r}. NaN compares False against every "
                f"window bound, which would silently disable this event's blackout."
            )
        if self.impact_severity not in VALID_IMPACT_SEVERITIES:
            raise ValueError(
                f"event {self.event_id}: impact_severity {self.impact_severity!r} is not "
                f"one of {VALID_IMPACT_SEVERITIES}. Map vendor codes with "
                f"normalize_impact_severity() -- an unrecognised severity must not be "
                f"treated as 'no blackout'."
            )
        for name, value in (
            ("consensus_forecast", self.consensus_forecast),
            ("actual_release", self.actual_release),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(
                    f"event {self.event_id}: {name} must be a finite number or None, got "
                    f"{value!r}. A NaN propagates silently into the surprise index."
                )
        if self.forecast_std_dev is not None and (
            isinstance(self.forecast_std_dev, bool)
            or not isinstance(self.forecast_std_dev, (int, float))
            or not math.isfinite(self.forecast_std_dev)
            or self.forecast_std_dev <= 0
        ):
            raise ValueError(
                f"event {self.event_id}: forecast_std_dev must be a finite positive "
                f"number or None, got {self.forecast_std_dev!r}."
            )
        for name, value in (
            ("pre_event_buffer_override_sec", self.pre_event_buffer_override_sec),
            ("post_event_buffer_override_sec", self.post_event_buffer_override_sec),
        ):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(
                    f"event {self.event_id}: {name} must be a finite non-negative number "
                    f"of seconds or None, got {value!r}."
                )


@dataclass
class MacroCalendarAuditReport:
    """Result of one macro calendar audit.

    Gate execution on :attr:`is_trading_permitted`, never on
    :attr:`is_blackout_active`. They differ: a stale or empty calendar sets
    ``is_trading_permitted=False`` with ``is_blackout_active=False``, because no
    blackout is known to be running -- the engine simply cannot vouch for the
    calendar. A caller that branches on ``is_blackout_active`` trades straight
    through a feed outage.
    """

    current_timestamp_utc: float
    is_trading_permitted: bool
    is_blackout_active: bool
    active_blackout_event: Optional[MacroEconomicEvent]
    should_cancel_open_limit_orders: bool
    macro_surprise_index: Optional[float]  # standardised: (Actual - Forecast) / StdDev
    status: str
    audit_notes: str
    # Fields below were appended in 2.0.0 and all carry defaults.
    blackout_started_at_utc: Optional[float] = None
    blackout_ends_at_utc: Optional[float] = None
    active_blackout_events: List[MacroEconomicEvent] = field(default_factory=list)
    macro_surprise_raw: Optional[float] = None  # Actual - Forecast, unstandardised
    surprise_source_event: Optional[MacroEconomicEvent] = None
    next_event: Optional[MacroEconomicEvent] = None
    seconds_to_next_event: Optional[float] = None
    calendar_as_of_utc: Optional[float] = None


class GlobalMacroCalendarEngine:
    """Enforces pre/post-release trading blackouts from a macro event calendar.

    An audit is a pure function of the registered calendar and the timestamp
    handed to it, so a backtest replay and a live risk loop can share the code
    path.

    ``should_cancel_open_limit_orders`` is **level-triggered**: it stays ``True``
    for every audit taken inside a blackout, not only the first. Edge-detect it
    if your venue rate-limits or charges for cancels; see
    ``broker-api-idempotent-cancel-requests``.

    Args:
        pre_event_buffer_sec: Seconds before a HIGH_IMPACT release at which the
            blackout opens.
        post_event_buffer_sec: Seconds after a HIGH_IMPACT release at which it
            closes. Both bounds are inclusive.
        medium_pre_event_buffer_sec: Pre-release buffer for MEDIUM_IMPACT events.
            ``None`` reuses the HIGH_IMPACT value, which is an earlier
            behaviour; set it explicitly to differentiate the two tiers.
        medium_post_event_buffer_sec: Post-release buffer for MEDIUM_IMPACT events.
        max_calendar_age_sec: If set, an audit whose calendar as-of timestamp is
            older than this blocks trading with ``MACRO_CALENDAR_STALE``. ``None``
            disables the check, so every audit then trusts whatever is in memory
            -- set it for any live deployment.
        require_non_empty_calendar: When ``True`` (default) an audit against an
            empty calendar blocks trading with ``MACRO_CALENDAR_UNAVAILABLE``
            rather than reporting "clear". Set ``False`` only for a deliberately
            eventless fixture.
        surprise_lookback_sec: How far back a permitted-status audit looks for the
            most recent released event when reporting a surprise. ``None`` removes
            the bound, which will keep reporting a months-old print.
        calendar_as_of_utc: Epoch seconds UTC at which the calendar was last
            refreshed from the vendor. Also settable via
            :meth:`set_calendar_as_of` and :meth:`replace_events`.

    Raises:
        ValueError: On any non-finite or negative buffer, a non-positive
            ``max_calendar_age_sec``, or a negative ``surprise_lookback_sec``. A
            negative pre-event buffer inverts the window and silently disables
            the blackout.
    """

    def __init__(
        self,
        pre_event_buffer_sec: float = 900.0,   # 15 minutes before event
        post_event_buffer_sec: float = 900.0,  # 15 minutes after event
        medium_pre_event_buffer_sec: Optional[float] = None,
        medium_post_event_buffer_sec: Optional[float] = None,
        max_calendar_age_sec: Optional[float] = None,
        require_non_empty_calendar: bool = True,
        surprise_lookback_sec: Optional[float] = DEFAULT_SURPRISE_LOOKBACK_SEC,
        calendar_as_of_utc: Optional[float] = None,
    ) -> None:
        self.pre_event_buffer_sec = self._validate_buffer(
            pre_event_buffer_sec, "pre_event_buffer_sec"
        )
        self.post_event_buffer_sec = self._validate_buffer(
            post_event_buffer_sec, "post_event_buffer_sec"
        )
        self.medium_pre_event_buffer_sec = (
            self.pre_event_buffer_sec
            if medium_pre_event_buffer_sec is None
            else self._validate_buffer(
                medium_pre_event_buffer_sec, "medium_pre_event_buffer_sec"
            )
        )
        self.medium_post_event_buffer_sec = (
            self.post_event_buffer_sec
            if medium_post_event_buffer_sec is None
            else self._validate_buffer(
                medium_post_event_buffer_sec, "medium_post_event_buffer_sec"
            )
        )
        if max_calendar_age_sec is not None and (
            isinstance(max_calendar_age_sec, bool)
            or not isinstance(max_calendar_age_sec, (int, float))
            or not math.isfinite(max_calendar_age_sec)
            or max_calendar_age_sec <= 0
        ):
            raise ValueError(
                f"max_calendar_age_sec must be a finite positive number of seconds or "
                f"None, got {max_calendar_age_sec!r}."
            )
        self.max_calendar_age_sec = max_calendar_age_sec
        self.require_non_empty_calendar = bool(require_non_empty_calendar)
        if surprise_lookback_sec is not None and (
            isinstance(surprise_lookback_sec, bool)
            or not isinstance(surprise_lookback_sec, (int, float))
            or not math.isfinite(surprise_lookback_sec)
            or surprise_lookback_sec < 0
        ):
            raise ValueError(
                f"surprise_lookback_sec must be a finite non-negative number of seconds "
                f"or None, got {surprise_lookback_sec!r}."
            )
        self.surprise_lookback_sec = surprise_lookback_sec
        self.scheduled_events: List[MacroEconomicEvent] = []
        self.calendar_as_of_utc: Optional[float] = None
        if calendar_as_of_utc is not None:
            self.set_calendar_as_of(calendar_as_of_utc)

    @staticmethod
    def _validate_buffer(value: float, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a number of seconds, got {value!r}.")
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"{name} must be a finite non-negative number of seconds, got {value!r}. "
                f"A negative buffer inverts the blackout window, which reads as 'never "
                f"in blackout'."
            )
        return float(value)

    # --- Calendar management ---------------------------------------------------

    def set_calendar_as_of(self, as_of_utc: float) -> None:
        """Record when the calendar was last refreshed from the vendor.

        Args:
            as_of_utc: Epoch seconds UTC of the successful refresh. Stamp the time
                the vendor response was *accepted*, not the time the request was
                sent -- a request that times out must not advance this.

        Raises:
            ValueError: If ``as_of_utc`` is not a finite number.
        """
        if (
            isinstance(as_of_utc, bool)
            or not isinstance(as_of_utc, (int, float))
            or not math.isfinite(as_of_utc)
        ):
            raise ValueError(
                f"calendar as-of timestamp must be finite epoch seconds UTC, got "
                f"{as_of_utc!r}."
            )
        self.calendar_as_of_utc = float(as_of_utc)

    def add_event(self, event: MacroEconomicEvent) -> None:
        """Register one scheduled release.

        Args:
            event: The event to register. Validated on entry.

        Raises:
            ValueError: If the event is invalid, or its ``event_id`` is already
                registered. Duplicates are rejected rather than merged: a
                re-ingested calendar row carrying a *changed* release time would
                otherwise leave two contradictory windows in the list. Use
                :meth:`remove_event` then :meth:`add_event`, or
                :meth:`replace_events`, for a schedule change.
        """
        if not isinstance(event, MacroEconomicEvent):
            raise ValueError(
                f"add_event expects a MacroEconomicEvent, got {type(event).__name__}."
            )
        event.validate()
        if any(e.event_id == event.event_id for e in self.scheduled_events):
            raise ValueError(
                f"event_id {event.event_id!r} is already registered. Remove or replace "
                f"it explicitly rather than registering a second window for it."
            )
        self.scheduled_events.append(event)
        self.scheduled_events.sort(key=lambda e: (e.release_timestamp_utc, e.event_id))

    def remove_event(self, event_id: str) -> bool:
        """Drop a scheduled release, e.g. one the statistical agency cancelled.

        Args:
            event_id: Identifier of the event to remove.

        Returns:
            ``True`` if an event was removed, ``False`` if none matched.
        """
        before = len(self.scheduled_events)
        self.scheduled_events = [e for e in self.scheduled_events if e.event_id != event_id]
        removed = len(self.scheduled_events) != before
        if removed:
            logger.info("Removed scheduled macro event %s from the calendar.", event_id)
        return removed

    def replace_events(
        self,
        events: Iterable[MacroEconomicEvent],
        as_of_utc: Optional[float] = None,
    ) -> None:
        """Atomically swap the whole calendar for a freshly fetched one.

        This is the correct primitive for a periodic vendor refresh: release dates
        are revised and sometimes withdrawn, so merging a new response into an old
        calendar leaves cancelled events behind, still enforcing blackouts for
        prints that will never happen.

        Args:
            events: The complete new set of scheduled releases.
            as_of_utc: Refresh timestamp to record. Omit only if you stamp it
                separately via :meth:`set_calendar_as_of`.

        Raises:
            ValueError: If any event is invalid or two share an ``event_id``. The
                existing calendar is left untouched when validation fails.
        """
        staged: List[MacroEconomicEvent] = []
        seen: Set[str] = set()
        for event in events:
            if not isinstance(event, MacroEconomicEvent):
                raise ValueError(
                    f"replace_events expects MacroEconomicEvent instances, got "
                    f"{type(event).__name__}."
                )
            event.validate()
            if event.event_id in seen:
                raise ValueError(
                    f"duplicate event_id {event.event_id!r} in replacement calendar."
                )
            seen.add(event.event_id)
            staged.append(event)
        staged.sort(key=lambda e: (e.release_timestamp_utc, e.event_id))
        self.scheduled_events = staged
        if as_of_utc is not None:
            self.set_calendar_as_of(as_of_utc)

    # --- Window arithmetic -----------------------------------------------------

    def buffers_for(self, event: MacroEconomicEvent) -> Tuple[float, float]:
        """Return the ``(pre, post)`` blackout buffers in seconds for one event.

        Per-event overrides win over the severity defaults.
        """
        if event.impact_severity == MEDIUM_IMPACT:
            pre, post = self.medium_pre_event_buffer_sec, self.medium_post_event_buffer_sec
        else:
            pre, post = self.pre_event_buffer_sec, self.post_event_buffer_sec
        if event.pre_event_buffer_override_sec is not None:
            pre = float(event.pre_event_buffer_override_sec)
        if event.post_event_buffer_override_sec is not None:
            post = float(event.post_event_buffer_override_sec)
        return pre, post

    def blackout_window_for(self, event: MacroEconomicEvent) -> Tuple[float, float]:
        """Return the inclusive ``(start, end)`` epoch-second blackout window."""
        pre, post = self.buffers_for(event)
        return event.release_timestamp_utc - pre, event.release_timestamp_utc + post

    # --- Surprise metrics ------------------------------------------------------

    def calculate_surprise_index(
        self,
        event: MacroEconomicEvent,
        as_of_utc: Optional[float] = None,
    ) -> Optional[float]:
        """Standardised macro surprise: ``(Actual - Forecast) / ForecastStdDev``.

        Standardisation is the whole point of the metric. Scotti (2016) divides
        each surprise by the sample standard deviation of that indicator's past
        surprises precisely "because units of measurement vary across
        macroeconomic variables". A CPI year-on-year miss of 0.2 percentage points
        and a payrolls miss of 70 thousand jobs are only comparable once both are
        expressed in standard deviations. This method therefore returns ``None``
        when ``forecast_std_dev`` is unavailable rather than substituting 1.0; the
        raw difference remains available from :meth:`raw_surprise`, correctly
        labelled.

        Args:
            event: The release to score.
            as_of_utc: Evaluation instant. When supplied, the actual print is
                ignored until ``as_of_utc >= event.release_timestamp_utc``.
                Passing ``None`` disables that look-ahead guard and is only
                appropriate for post-hoc research over a settled calendar -- the
                audit path always supplies it.

        Returns:
            The standardised surprise rounded to 4 decimals, sign-flipped when
            ``higher_actual_is_positive_surprise`` is ``False``, or ``None`` if it
            cannot be computed.
        """
        raw = self.raw_surprise(event, as_of_utc=as_of_utc)
        if raw is None:
            return None
        std_dev = event.forecast_std_dev
        if std_dev is None or not math.isfinite(std_dev) or std_dev <= 0:
            logger.warning(
                "Event %s: forecast_std_dev is %r, so no standardised surprise index is "
                "available. The raw difference is %.6g in the release's own units and is "
                "NOT comparable across indicators.",
                event.event_id,
                std_dev,
                raw,
            )
            return None
        value = raw / std_dev
        if not math.isfinite(value):
            logger.warning(
                "Event %s: surprise index is non-finite; returning None.", event.event_id
            )
            return None
        return round(value, 4)

    def raw_surprise(
        self,
        event: MacroEconomicEvent,
        as_of_utc: Optional[float] = None,
    ) -> Optional[float]:
        """Unstandardised surprise ``Actual - Forecast`` in the release's own units.

        Carries the same look-ahead guard and sign convention as
        :meth:`calculate_surprise_index`. Never compare this figure across
        different indicators -- the units differ.
        """
        if event.actual_release is None or event.consensus_forecast is None:
            return None
        if as_of_utc is not None and as_of_utc < event.release_timestamp_utc:
            logger.debug(
                "Event %s: actual_release withheld -- as_of %.0f precedes the scheduled "
                "release at %.0f.",
                event.event_id,
                as_of_utc,
                event.release_timestamp_utc,
            )
            return None
        if not math.isfinite(event.actual_release) or not math.isfinite(
            event.consensus_forecast
        ):
            logger.warning(
                "Event %s: non-finite actual_release/consensus_forecast; returning None "
                "rather than propagating NaN into a risk decision.",
                event.event_id,
            )
            return None
        diff = float(event.actual_release) - float(event.consensus_forecast)
        return diff if event.higher_actual_is_positive_surprise else -diff

    # --- Audit -----------------------------------------------------------------

    def audit_macro_trading_status(
        self,
        current_time_utc: float,
        relevant_currencies: Optional[Sequence[str]] = None,
    ) -> MacroCalendarAuditReport:
        """Decide whether trading is permitted at ``current_time_utc``.

        Args:
            current_time_utc: Evaluation instant, epoch seconds UTC.
            relevant_currencies: Optional whitelist of event currencies to
                consider. ``None`` (default) evaluates every event, which is the
                safe choice: major US releases move non-USD assets, so scoping a
                blackout to the instrument's own currency reopens trading into the
                very move you are avoiding. Narrow it only with evidence that the
                excluded releases do not move your book.

        Returns:
            A :class:`MacroCalendarAuditReport`. Gate on ``is_trading_permitted``.

        Raises:
            ValueError: If ``current_time_utc`` is not a finite number, or
                ``relevant_currencies`` is supplied but contains no usable codes.
        """
        if (
            isinstance(current_time_utc, bool)
            or not isinstance(current_time_utc, (int, float))
            or not math.isfinite(current_time_utc)
        ):
            raise ValueError(
                f"current_time_utc must be finite epoch seconds UTC, got "
                f"{current_time_utc!r}."
            )
        current_time_utc = float(current_time_utc)

        currency_filter: Optional[Set[str]] = None
        if relevant_currencies is not None:
            if isinstance(relevant_currencies, str):
                raise ValueError(
                    "relevant_currencies must be a sequence of currency codes, not a "
                    "bare string -- 'USD' would be read as the characters U, S, D."
                )
            currency_filter = {
                c.strip().upper() for c in relevant_currencies if c and c.strip()
            }
            if not currency_filter:
                raise ValueError(
                    "relevant_currencies was supplied but contains no usable currency "
                    "codes. Pass None to evaluate every event rather than an empty "
                    "sequence, which would disable the blackout entirely."
                )

        unavailable = self._calendar_unavailable_report(current_time_utc)
        if unavailable is not None:
            return unavailable

        active: List[Tuple[float, float, MacroEconomicEvent]] = []
        for event in self.scheduled_events:
            if event.impact_severity not in BLOCKING_SEVERITIES:
                continue
            if (
                currency_filter is not None
                and event.currency.strip().upper() not in currency_filter
            ):
                continue
            start, end = self.blackout_window_for(event)
            if start <= current_time_utc <= end:
                active.append((start, end, event))

        if active:
            return self._blackout_report(current_time_utc, active)
        return self._permitted_report(current_time_utc, currency_filter)

    # --- Report builders -------------------------------------------------------

    def _calendar_unavailable_report(
        self, current_time_utc: float
    ) -> Optional[MacroCalendarAuditReport]:
        """Fail closed when the calendar itself cannot be trusted."""
        reason: Optional[str] = None
        status = STATUS_CALENDAR_UNAVAILABLE

        if self.require_non_empty_calendar and not self.scheduled_events:
            reason = (
                "no macro events are registered. An empty calendar is indistinguishable "
                "from a calendar that never loaded, so trading is blocked rather than "
                "reported clear."
            )
        elif self.max_calendar_age_sec is not None:
            if self.calendar_as_of_utc is None:
                status = STATUS_CALENDAR_STALE
                reason = (
                    "max_calendar_age_sec is configured but the calendar carries no "
                    "as-of timestamp, so its freshness cannot be established."
                )
            else:
                age = current_time_utc - self.calendar_as_of_utc
                if age < 0:
                    logger.warning(
                        "Calendar as-of %.0f is ahead of the audit time %.0f -- check for "
                        "clock skew between the calendar refresher and this host.",
                        self.calendar_as_of_utc,
                        current_time_utc,
                    )
                elif age > self.max_calendar_age_sec:
                    status = STATUS_CALENDAR_STALE
                    reason = (
                        f"calendar is {age:.0f}s old against a "
                        f"{self.max_calendar_age_sec:.0f}s tolerance. Release schedules "
                        f"are revised and withdrawn -- the October 2025 US CPI and "
                        f"Employment Situation reports were cancelled outright -- so a "
                        f"stale calendar is not evidence of a clear window."
                    )

        if reason is None:
            return None

        notes = f"MACRO CALENDAR NOT USABLE: {reason} Trading BLOCKED (fail-closed)."
        logger.error(notes)
        return MacroCalendarAuditReport(
            current_timestamp_utc=current_time_utc,
            is_trading_permitted=False,
            is_blackout_active=False,
            active_blackout_event=None,
            should_cancel_open_limit_orders=True,
            macro_surprise_index=None,
            status=status,
            audit_notes=notes,
            calendar_as_of_utc=self.calendar_as_of_utc,
        )

    def _blackout_report(
        self,
        current_time_utc: float,
        active: List[Tuple[float, float, MacroEconomicEvent]],
    ) -> MacroCalendarAuditReport:
        """Build the blackout report, honouring the most restrictive overlap."""
        # Overlapping releases are routine -- US CPI and Retail Sales have shared an
        # 8:30 a.m. ET slot. The window that governs is the one ending LAST, so a
        # caller scheduling its resume from blackout_ends_at_utc does not restart
        # into a second window that is still open.
        active.sort(key=lambda item: (item[1], item[2].event_id))
        _, governing_end, governing_event = active[-1]
        earliest_start = min(item[0] for item in active)
        events = [
            item[2]
            for item in sorted(
                active, key=lambda i: (i[2].release_timestamp_utc, i[2].event_id)
            )
        ]

        surprise = self.calculate_surprise_index(governing_event, as_of_utc=current_time_utc)
        raw = self.raw_surprise(governing_event, as_of_utc=current_time_utc)
        overlap_note = (
            f" {len(events)} overlapping events active; window governed by the latest to "
            f"close."
            if len(events) > 1
            else ""
        )
        notes = (
            f"MACRO RISK BLACKOUT ACTIVE [{governing_event.event_name} - "
            f"{governing_event.currency}]: Release Time = "
            f"{governing_event.release_timestamp_utc}. Window {earliest_start:.0f} to "
            f"{governing_end:.0f}. Trading PAUSED. Mass Cancel Limit Orders "
            f"TRIGGERED.{overlap_note}"
        )
        logger.warning(notes)
        return MacroCalendarAuditReport(
            current_timestamp_utc=current_time_utc,
            is_trading_permitted=False,
            is_blackout_active=True,
            active_blackout_event=governing_event,
            should_cancel_open_limit_orders=True,
            macro_surprise_index=surprise,
            status=STATUS_BLACKOUT,
            audit_notes=notes,
            blackout_started_at_utc=earliest_start,
            blackout_ends_at_utc=governing_end,
            active_blackout_events=events,
            macro_surprise_raw=raw,
            surprise_source_event=governing_event if raw is not None else None,
            next_event=self._next_event_after(current_time_utc, None),
            seconds_to_next_event=self._seconds_to_next_event(current_time_utc, None),
            calendar_as_of_utc=self.calendar_as_of_utc,
        )

    def _permitted_report(
        self,
        current_time_utc: float,
        currency_filter: Optional[Set[str]],
    ) -> MacroCalendarAuditReport:
        """Build the clear report, including the most recent released surprise."""
        source = self._most_recent_released_event(current_time_utc, currency_filter)
        surprise: Optional[float] = None
        raw: Optional[float] = None
        if source is not None:
            surprise = self.calculate_surprise_index(source, as_of_utc=current_time_utc)
            raw = self.raw_surprise(source, as_of_utc=current_time_utc)

        next_event = self._next_event_after(current_time_utc, currency_filter)
        notes = (
            f"MACRO CALENDAR CLEAR: Current time {current_time_utc} is outside all macro "
            f"blackout windows."
        )
        if next_event is not None:
            start, _ = self.blackout_window_for(next_event)
            notes += (
                f" Next blocking event {next_event.event_name} at "
                f"{next_event.release_timestamp_utc}; blackout opens at {start:.0f}."
            )
        # Debug, not info: a live risk loop audits on every tick, and an info line
        # per audit buries the warnings that matter.
        logger.debug(notes)
        return MacroCalendarAuditReport(
            current_timestamp_utc=current_time_utc,
            is_trading_permitted=True,
            is_blackout_active=False,
            active_blackout_event=None,
            should_cancel_open_limit_orders=False,
            macro_surprise_index=surprise,
            status=STATUS_PERMITTED,
            audit_notes=notes,
            macro_surprise_raw=raw,
            surprise_source_event=source if raw is not None else None,
            next_event=next_event,
            seconds_to_next_event=self._seconds_to_next_event(
                current_time_utc, currency_filter
            ),
            calendar_as_of_utc=self.calendar_as_of_utc,
        )

    # --- Lookups ---------------------------------------------------------------

    def _blocking_events(
        self, currency_filter: Optional[Set[str]]
    ) -> List[MacroEconomicEvent]:
        return [
            e
            for e in self.scheduled_events
            if e.impact_severity in BLOCKING_SEVERITIES
            and (currency_filter is None or e.currency.strip().upper() in currency_filter)
        ]

    def _next_event_after(
        self, current_time_utc: float, currency_filter: Optional[Set[str]]
    ) -> Optional[MacroEconomicEvent]:
        for event in self._blocking_events(currency_filter):
            if event.release_timestamp_utc > current_time_utc:
                return event
        return None

    def _seconds_to_next_event(
        self, current_time_utc: float, currency_filter: Optional[Set[str]]
    ) -> Optional[float]:
        nxt = self._next_event_after(current_time_utc, currency_filter)
        return None if nxt is None else nxt.release_timestamp_utc - current_time_utc

    def _most_recent_released_event(
        self, current_time_utc: float, currency_filter: Optional[Set[str]]
    ) -> Optional[MacroEconomicEvent]:
        """Latest already-released event carrying both an actual and a forecast."""
        horizon = (
            None
            if self.surprise_lookback_sec is None
            else current_time_utc - self.surprise_lookback_sec
        )
        best: Optional[MacroEconomicEvent] = None
        for event in self.scheduled_events:
            if event.release_timestamp_utc > current_time_utc:
                break  # scheduled_events is sorted ascending by release time
            if horizon is not None and event.release_timestamp_utc < horizon:
                continue
            if (
                currency_filter is not None
                and event.currency.strip().upper() not in currency_filter
            ):
                continue
            if event.actual_release is None or event.consensus_forecast is None:
                continue
            best = event
        return best
