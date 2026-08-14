"""
broker-api-deprecation-notice-monitoring: RFC 8594 ``Sunset`` / RFC 9745 ``Deprecation``
header scanner and developer-changelog monitor for impending broker API retirements.

This module is a **deadline detector**, and its failure modes are asymmetric: a false
positive costs an engineer a few minutes reading a changelog entry, a false negative
means a live trading bot keeps calling an endpoint that stops answering. Three
properties follow from that:

  1. **It never raises into the caller.** ``inspect_http_headers`` is designed to be
     hooked into an HTTP client's global response handler, so it runs on the same
     thread as every live order and market-data request. A monitor that throws there
     converts "an endpoint will break in 30 days" into "the bot breaks now". Both
     public entry points contain unexpected errors, log them with a traceback, and
     return ``None``.

  2. **The soonest credible deadline wins.** Changelog entries contain many dates —
     the publication date, unrelated release dates, support windows for the
     *replacement* API. Picking the first date found (or the latest) can silently
     classify a three-week deadline as ``NOTICE``. Candidate dates earlier than the
     entry's own publication date are discarded, and the earliest of what remains is
     used, because that is the date the desk has to act on.

  3. **Expiry is an instant comparison, not a day count.** A sunset 23 hours away has
     zero whole days remaining but has *not* expired; reporting it as ``EXPIRED``
     tells the desk the migration window is gone while it still has a final day.

Scope limits:

  - Header presence is a **hint**, not a guarantee. RFC 8594 Section 3 is explicit:
    "Clients SHOULD treat Sunset timestamps as hints: it is not guaranteed that the
    resource will, in fact, be available until that time and will not be available
    after that time." Broker adoption of these headers is inconsistent, so an empty
    registry is not evidence that nothing is being retired.
  - Changelog parsing is a keyword-and-date heuristic over free text, not a
    specification-driven parse. It is a triage aid that routes a human to the entry;
    it is not authoritative.
  - Locale-ambiguous numeric dates (``11/12/2026``) are deliberately **not** parsed:
    day-first and month-first conventions are indistinguishable without knowing the
    publisher, and guessing wrong moves a deadline by up to eleven months.
  - The registry is in-memory and per-process. It does not survive a restart and is
    not shared across replicas.

References:
  - RFC 8594, "The Sunset HTTP Header Field" (May 2019).
  - RFC 9745, "The Deprecation HTTP Response Header Field" (Standards Track,
    March 2025).
  - RFC 8288, "Web Linking" (October 2017), which obsoletes RFC 5988.
"""
import datetime
import email.utils
import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc

#: Escalation thresholds in whole days remaining. These are operational conventions
#: for a migration lead time, **not** anything RFC 8594 or RFC 9745 mandate — neither
#: specification says anything about how far ahead a consumer should escalate. They
#: are constructor-overridable so a desk can align them with its own release cadence.
DEFAULT_CRITICAL_DAYS = 7
DEFAULT_WARNING_DAYS = 30


class DeprecationUrgency(Enum):
    """Escalation tier for a deprecation notice."""

    NONE = "NONE"
    NOTICE = "NOTICE"
    WARNING_30_DAYS = "WARNING_30_DAYS"
    CRITICAL_SUNSET_IMMINENT = "CRITICAL_SUNSET_IMMINENT"
    EXPIRED = "EXPIRED"


#: Severity order. Because the clock only moves forward, a notice with a fixed sunset
#: date can only ever move *up* this ranking, which is what makes escalation-only
#: alerting safe: nothing is suppressed that a desk still needs to see.
_URGENCY_RANK: Dict[DeprecationUrgency, int] = {
    DeprecationUrgency.NONE: 0,
    DeprecationUrgency.NOTICE: 1,
    DeprecationUrgency.WARNING_30_DAYS: 2,
    DeprecationUrgency.CRITICAL_SUNSET_IMMINENT: 3,
    DeprecationUrgency.EXPIRED: 4,
}

_MONTHS: Dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(_MONTHS)

#: ISO calendar date, e.g. "2026-11-20". Digit lookaround rather than \b, because \b
#: does not match between "25" and the "T" of a full ISO timestamp.
_ISO_DATE_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
#: "20 November 2026" / "20 Nov. 2026".
_DAY_FIRST_RE = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_ALT})[a-z]*\.?,?\s+(\d{{4}})\b", re.IGNORECASE
)
#: "November 20, 2026" / "Nov 20 2026" / "March 15th, 2022".
_MONTH_FIRST_RE = re.compile(
    rf"\b({_MONTH_ALT})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)

#: Keywords that mark a changelog entry as deprecation-relevant.
_CHANGELOG_KEYWORDS: Tuple[str, ...] = (
    r"\bdeprecat\w*\b",
    r"\bsunset\w*\b",
    r"\bbreaking change\b",
    r"\bend of life\b",
    r"\bend-of-life\b",
    r"\bretir\w*\b",
)


def _is_aware(value: datetime.datetime) -> bool:
    """Python's canonical awareness test.

    Checking ``tzinfo is not None`` alone is not enough: a tzinfo whose ``utcoffset``
    returns ``None`` leaves the datetime effectively naive, and ``astimezone`` then
    interprets it in the *host's* local timezone — silently leaking server-local time
    into a countdown that is supposed to be UTC.
    """
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


@dataclass(frozen=True)
class DeprecationNotice:
    """A single detected deprecation, as of ``evaluated_at_utc``.

    ``days_remaining`` is whole days, rounded down, and is negative once the sunset
    date has passed. It is ``None`` when no sunset date could be determined. It is
    only meaningful together with ``evaluated_at_utc``: both are computed from one
    clock reading, so they cannot disagree.
    """

    broker_name: str
    endpoint: str
    sunset_date_utc: Optional[datetime.datetime]
    days_remaining: Optional[int]
    urgency: DeprecationUrgency
    source: str  # "HTTP_HEADER" or "CHANGELOG_FEED"
    message: str
    reference_link: Optional[str] = None
    #: RFC 9745 deprecation date, when the broker supplied a parseable one. This is
    #: when the resource became/becomes deprecated, which is not when it stops
    #: answering — urgency is always derived from the sunset date.
    deprecation_date_utc: Optional[datetime.datetime] = None
    #: Distinguishes notices that share a broker and endpoint, notably changelog
    #: entries, which all carry the endpoint "GLOBAL_FEED".
    entry_id: Optional[str] = None
    evaluated_at_utc: Optional[datetime.datetime] = None


class BrokerDeprecationMonitor:
    """Scans HTTP response headers and developer changelog entries for API sunsets.

    Thread-safe: the notice registry is guarded by an ``RLock``, and alert callbacks
    are invoked while that lock is held so that a de-duplicated notice cannot be
    dispatched twice by racing threads. Callbacks should therefore be cheap and
    non-blocking — hand off to a queue rather than making a synchronous HTTP call.
    """

    def __init__(
        self,
        now_fn: Optional[Callable[[], datetime.datetime]] = None,
        alert_callback: Optional[Callable[[DeprecationNotice], None]] = None,
        critical_days: int = DEFAULT_CRITICAL_DAYS,
        warning_days: int = DEFAULT_WARNING_DAYS,
    ) -> None:
        """
        :param now_fn: Dependency injection for current time. Defaults to
            ``datetime.now(timezone.utc)``. A naive datetime is interpreted as UTC
            rather than raising, because this code runs on the live request path.
        :param alert_callback: Optional sink for emitting alerts to external systems
            (PagerDuty, Slack). Exceptions raised by it are logged, not propagated.
        :param critical_days: Days remaining at or below which a notice is CRITICAL.
        :param warning_days: Days remaining at or below which a notice is a WARNING.
        :raises ValueError: if the thresholds are negative or inverted.
        """
        if critical_days < 0 or warning_days < 0:
            raise ValueError("escalation thresholds must be non-negative")
        if critical_days > warning_days:
            raise ValueError(
                f"critical_days ({critical_days}) must not exceed "
                f"warning_days ({warning_days})"
            )

        self._now_fn = now_fn or (lambda: datetime.datetime.now(_UTC))
        self._alert_callback = alert_callback
        self._critical_days = critical_days
        self._warning_days = warning_days

        self._lock = threading.RLock()
        self._active_notices: Dict[str, DeprecationNotice] = {}
        self._warned_naive_now = False

    @property
    def active_notices(self) -> List[DeprecationNotice]:
        """Snapshot of the currently tracked notices."""
        with self._lock:
            return list(self._active_notices.values())

    # ------------------------------------------------------------------ time

    def _now_utc(self) -> datetime.datetime:
        """Current time as an aware UTC datetime.

        A naive ``now_fn`` is a common wiring mistake (``datetime.datetime.now``
        instead of ``lambda: datetime.datetime.now(timezone.utc)``). Subtracting it
        from an aware sunset date raises ``TypeError`` on the live request path, so
        it is interpreted as UTC and warned about once instead.
        """
        now = self._now_fn()
        if not _is_aware(now):
            with self._lock:
                if not self._warned_naive_now:
                    self._warned_naive_now = True
                    logger.warning(
                        "now_fn returned a naive datetime; interpreting it as UTC. "
                        "Pass a timezone-aware callable to remove this ambiguity."
                    )
            return now.replace(tzinfo=_UTC)
        return now.astimezone(_UTC)

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def parse_http_date(date_str: str) -> Optional[datetime.datetime]:
        """Parse an HTTP-date or ISO 8601 string into an aware UTC datetime.

        RFC 8594 Section 3 defines ``Sunset = HTTP-date``, and HTTP-date admits three
        formats (IMF-fixdate, the obsolete RFC 850 form, and asctime), all of which
        ``email.utils.parsedate_tz`` accepts. ISO 8601 is also accepted because
        changelog prose and some non-conforming gateways use it.

        Returns ``None`` for anything unparseable — including ``Deprecation: true``,
        which carries no date.
        """
        if not isinstance(date_str, str):
            return None
        date_str = date_str.strip()
        if not date_str:
            return None

        # ISO 8601 first: fromisoformat honours an explicit offset, whereas matching
        # only the leading YYYY-MM-DD would silently relocate "2026-11-25T00:00+05:30"
        # to midnight UTC — 5.5 hours *later* than the instant the broker announced.
        iso_match = re.match(r"\d{4}-\d{2}-\d{2}", date_str)
        if iso_match:
            for attempt in (date_str.replace("Z", "+00:00"), iso_match.group(0)):
                try:
                    dt = datetime.datetime.fromisoformat(attempt)
                except ValueError:
                    continue  # Retry on the bare date prefix, e.g. "2026-11-25 (TBC)".
                return (
                    dt.replace(tzinfo=_UTC) if dt.tzinfo is None else dt.astimezone(_UTC)
                )

        parsed_tuple = email.utils.parsedate_tz(date_str)
        if parsed_tuple:
            try:
                dt = datetime.datetime(*parsed_tuple[:6])
            except ValueError:
                return None  # e.g. "Wed, 32 Nov 2026 00:00:00 GMT"
            tz_offset = parsed_tuple[9] or 0
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(seconds=tz_offset)))
            return dt.astimezone(_UTC)

        return None

    @staticmethod
    def parse_deprecation_header(value: str) -> Optional[datetime.datetime]:
        """Parse an RFC 9745 ``Deprecation`` field value into an aware UTC datetime.

        RFC 9745 Section 2.1: "Deprecation is an Item Structured Header Field; its
        value MUST be a Date as per Section 3.3.7 of [RFC9651]" — a leading ``@``
        followed by seconds since the Unix epoch, e.g. ``Deprecation: @1688169599``.
        The value may be in the past (already deprecated) or the future.

        Returns ``None`` for the legacy boolean form ``Deprecation: true`` (permitted
        by the pre-RFC drafts and still emitted by deployed gateways), which asserts
        deprecation without dating it. Callers must treat ``None`` as "deprecated,
        date unknown" rather than "not deprecated".
        """
        if not isinstance(value, str):
            return None
        value = value.strip()
        match = re.fullmatch(r"@(-?\d+)", value)
        if not match:
            return None
        try:
            return datetime.datetime.fromtimestamp(int(match.group(1)), tz=_UTC)
        except (OverflowError, OSError, ValueError):
            logger.warning("Deprecation header carried an out-of-range date: %r", value)
            return None

    @staticmethod
    def _extract_link(link_header: str, rel: str) -> Optional[str]:
        """Extract the first link target carrying ``rel`` from an RFC 8288 Link header.

        Splitting the header on "," loses targets whose URI contains a comma, so the
        ``<target>`` forms are matched directly and each one's parameters are read up
        to the next target. ``rel`` is matched as a whole token, since RFC 8288 allows
        multiple space-separated relation types in one ``rel`` parameter.
        """
        if not isinstance(link_header, str):
            return None
        for match in re.finditer(r"<([^>]*)>([^<]*)", link_header):
            target, params = match.group(1).strip(), match.group(2)
            rel_match = re.search(
                r"""rel\s*=\s*(?:"([^"]*)"|'([^']*)'|([^;,\s]+))""", params, re.IGNORECASE
            )
            if not rel_match:
                continue
            rel_value = next(g for g in rel_match.groups() if g is not None)
            if rel.lower() in rel_value.lower().split():
                return target or None
        return None

    @classmethod
    def extract_sunset_link(cls, link_header: str) -> Optional[str]:
        """Extract the ``rel="sunset"`` target (RFC 8594 Section 6) from a Link header."""
        return cls._extract_link(link_header, "sunset")

    @staticmethod
    def extract_candidate_dates(text: str) -> List[datetime.datetime]:
        """Return every unambiguous calendar date in ``text``, ascending, as UTC midnight.

        Recognises ISO ``YYYY-MM-DD`` plus the two long forms broker notices actually
        use ("March 15, 2022", "15 March 2022"). Purely numeric forms such as
        ``11/12/2026`` are excluded: day-first and month-first are indistinguishable
        without knowing the publisher's locale.
        """
        found: Set[Tuple[int, int, int]] = set()

        for year, month, day in _ISO_DATE_RE.findall(text):
            found.add((int(year), int(month), int(day)))
        for day, month, year in _DAY_FIRST_RE.findall(text):
            found.add((int(year), _MONTHS[month[:3].lower()], int(day)))
        for month, day, year in _MONTH_FIRST_RE.findall(text):
            found.add((int(year), _MONTHS[month[:3].lower()], int(day)))

        dates: List[datetime.datetime] = []
        for year, month, day in found:
            try:
                dates.append(datetime.datetime(year, month, day, tzinfo=_UTC))
            except ValueError:
                continue  # e.g. "2026-13-45" or "February 30, 2026".
        return sorted(dates)

    # ------------------------------------------------------------------ evaluation

    def _evaluate(
        self, sunset_date: Optional[datetime.datetime], now: datetime.datetime
    ) -> Tuple[Optional[int], DeprecationUrgency]:
        """Days remaining and urgency, both derived from one clock reading."""
        if sunset_date is None:
            return None, DeprecationUrgency.NOTICE

        delta = sunset_date - now
        # Floor division so the count is conservative on both signs: a sunset 7 days
        # and 23 hours away reports 7 (CRITICAL), never 8.
        days_remaining = int(delta.total_seconds() // 86400)

        # Expiry is decided by the instant, not the floored day count. A sunset 23
        # hours away has zero whole days left but has not expired, and the desk still
        # has a final day to migrate.
        if delta.total_seconds() <= 0:
            return days_remaining, DeprecationUrgency.EXPIRED
        if days_remaining <= self._critical_days:
            return days_remaining, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT
        if days_remaining <= self._warning_days:
            return days_remaining, DeprecationUrgency.WARNING_30_DAYS
        return days_remaining, DeprecationUrgency.NOTICE

    def _register_notice(self, notice: DeprecationNotice) -> None:
        """Store a notice and alert on it if it is new, escalating, or re-dated.

        A pure de-escalation is stored but not re-alerted, so routine re-inspection of
        an unchanged header does not page anyone. Because urgency only rises as the
        clock advances, the only way to de-escalate is for the broker to move the
        sunset date — and that is caught by the date comparison regardless.
        """
        key = f"{notice.broker_name}:{notice.endpoint}:{notice.entry_id or ''}"
        with self._lock:
            existing = self._active_notices.get(key)
            self._active_notices[key] = notice

            if existing is None:
                should_alert = True
            else:
                escalated = (
                    _URGENCY_RANK[notice.urgency] > _URGENCY_RANK[existing.urgency]
                )
                re_dated = existing.sunset_date_utc != notice.sunset_date_utc
                should_alert = escalated or re_dated

            if should_alert and self._alert_callback:
                try:
                    self._alert_callback(notice)
                except Exception:
                    logger.exception(
                        "Alert callback failed for %s %s; the notice is still "
                        "registered and will re-alert if it escalates.",
                        notice.broker_name,
                        notice.endpoint,
                    )

    # ------------------------------------------------------------------ entry points

    def inspect_http_headers(
        self,
        broker_name: str,
        endpoint: str,
        response_headers: Optional[Mapping[str, object]],
    ) -> Optional[DeprecationNotice]:
        """Inspect one API response's headers for deprecation signals.

        Checks ``Sunset`` (RFC 8594), ``Deprecation`` (RFC 9745), the ``rel="sunset"``
        and ``rel="deprecation"`` Link targets (RFC 8288), and the non-standard
        ``X-API-Deprecation-Warning``. Returns ``None`` when nothing is signalled.

        This is intended to run inside an HTTP client response hook, on the same
        thread as live order and market-data calls, so it never propagates an
        exception to the caller: unexpected failures are logged with a traceback and
        reported as ``None``. A monitoring bug must not take the trading path down.
        """
        try:
            return self._inspect_http_headers(broker_name, endpoint, response_headers)
        except Exception:
            logger.exception(
                "Deprecation monitor failed while inspecting headers for %s %s. "
                "The API response itself is unaffected.",
                broker_name,
                endpoint,
            )
            return None

    def _inspect_http_headers(
        self,
        broker_name: str,
        endpoint: str,
        response_headers: Optional[Mapping[str, object]],
    ) -> Optional[DeprecationNotice]:
        if not isinstance(response_headers, Mapping):
            if response_headers is not None:
                logger.warning(
                    "Expected a header mapping for %s %s, got %s.",
                    broker_name,
                    endpoint,
                    type(response_headers).__name__,
                )
            return None

        # Only genuine string values count. A None or absent value coerced with str()
        # becomes the truthy literal "None" and manufactures a deprecation that the
        # broker never signalled.
        headers_lower = {
            str(k).lower(): v.strip()
            for k, v in response_headers.items()
            if isinstance(v, str) and v.strip()
        }

        sunset_raw = headers_lower.get("sunset")
        deprecation_raw = headers_lower.get("deprecation")
        warning_raw = headers_lower.get("x-api-deprecation-warning") or headers_lower.get(
            "x-deprecation-warning"
        )
        link_raw = headers_lower.get("link")

        sunset_link = self._extract_link(link_raw, "sunset") if link_raw else None
        deprecation_link = self._extract_link(link_raw, "deprecation") if link_raw else None

        if not (sunset_raw or deprecation_raw or warning_raw or sunset_link or deprecation_link):
            return None

        sunset_date = self.parse_http_date(sunset_raw) if sunset_raw else None
        deprecation_date = (
            self.parse_deprecation_header(deprecation_raw) if deprecation_raw else None
        )

        # RFC 9745 Section 4: "The timestamp given in the Sunset HTTP header field
        # MUST NOT be earlier than the one given in the Deprecation header field."
        # A violation means the broker's own lifecycle metadata is inconsistent, so
        # neither date should be trusted for migration planning without confirmation.
        if sunset_date and deprecation_date and sunset_date < deprecation_date:
            logger.warning(
                "%s %s returned Sunset (%s) earlier than Deprecation (%s), which "
                "violates RFC 9745 Section 4. Confirm the dates with the broker.",
                broker_name,
                endpoint,
                sunset_date.isoformat(),
                deprecation_date.isoformat(),
            )

        now = self._now_utc()
        days_remaining, urgency = self._evaluate(sunset_date, now)

        if sunset_date is None:
            timeline = "(sunset date not supplied)"
        elif days_remaining is not None and days_remaining < 0:
            timeline = f"({abs(days_remaining)} days past sunset)"
        else:
            timeline = f"({days_remaining} days remaining)"

        msg = (
            f"DEPRECATION NOTICE [{broker_name} {endpoint}]: "
            f"Sunset Date: {sunset_date.isoformat() if sunset_date else 'TBD'} {timeline}. "
            f"Warning: {warning_raw or 'Endpoint scheduled for retirement.'}"
        )

        if urgency in (
            DeprecationUrgency.CRITICAL_SUNSET_IMMINENT,
            DeprecationUrgency.EXPIRED,
        ):
            logger.critical(msg)
        else:
            logger.warning(msg)

        notice = DeprecationNotice(
            broker_name=broker_name,
            endpoint=endpoint,
            sunset_date_utc=sunset_date,
            days_remaining=days_remaining,
            urgency=urgency,
            source="HTTP_HEADER",
            message=msg,
            reference_link=sunset_link or deprecation_link,
            deprecation_date_utc=deprecation_date,
            evaluated_at_utc=now,
        )
        self._register_notice(notice)
        return notice

    def parse_changelog_entry(
        self,
        broker_name: str,
        title: str,
        content: str,
        publish_date: datetime.datetime,
        link: Optional[str] = None,
    ) -> Optional[DeprecationNotice]:
        """Triage one changelog/RSS entry for deprecation keywords and a sunset date.

        Returns ``None`` when no deprecation keyword matches. Like
        ``inspect_http_headers`` this never raises: it typically runs in a background
        poll loop, and an exception there would silently stop all future polling.

        The extracted date is a heuristic. Candidates earlier than ``publish_date``
        are discarded (an entry cannot announce a retirement that already happened
        before it was written — those matches are publication dates and release
        history), and the earliest surviving candidate is chosen, because the soonest
        deadline is the one the desk must act on. When several candidates survive,
        the message says so; a human should confirm against the linked entry.
        """
        try:
            return self._parse_changelog_entry(
                broker_name, title, content, publish_date, link
            )
        except Exception:
            logger.exception(
                "Deprecation monitor failed while parsing a changelog entry for %s.",
                broker_name,
            )
            return None

    def _parse_changelog_entry(
        self,
        broker_name: str,
        title: str,
        content: str,
        publish_date: datetime.datetime,
        link: Optional[str],
    ) -> Optional[DeprecationNotice]:
        title = title or ""
        content = content or ""
        text = f"{title} {content}".lower()

        if not any(re.search(kw, text) for kw in _CHANGELOG_KEYWORDS):
            return None

        if not isinstance(publish_date, datetime.datetime):
            raise TypeError(
                f"publish_date must be a datetime, got {type(publish_date).__name__}"
            )
        published_utc = (
            publish_date.astimezone(_UTC)
            if _is_aware(publish_date)
            else publish_date.replace(tzinfo=_UTC)
        )

        candidates = self.extract_candidate_dates(text)
        sunset_date, candidate_count = self._select_sunset_date(candidates, published_utc)

        now = self._now_utc()
        days_remaining, urgency = self._evaluate(sunset_date, now)

        if sunset_date is None:
            date_note = "no sunset date found in entry"
        elif candidate_count > 1:
            date_note = (
                f"earliest of {candidate_count} candidate dates: "
                f"{sunset_date.date().isoformat()} — confirm against the entry"
            )
        else:
            date_note = f"sunset {sunset_date.date().isoformat()}"

        msg = (
            f"CHANGELOG DEPRECATION ALERT [{broker_name}]: '{title}' — "
            f"Published {published_utc.date().isoformat()} ({date_note})"
        )

        if urgency in (
            DeprecationUrgency.CRITICAL_SUNSET_IMMINENT,
            DeprecationUrgency.EXPIRED,
        ):
            logger.critical(msg)
        else:
            logger.warning(msg)

        notice = DeprecationNotice(
            broker_name=broker_name,
            endpoint="GLOBAL_FEED",
            sunset_date_utc=sunset_date,
            days_remaining=days_remaining,
            urgency=urgency,
            source="CHANGELOG_FEED",
            message=msg,
            reference_link=link,
            entry_id=self._changelog_entry_id(broker_name, title, published_utc, link),
            evaluated_at_utc=now,
        )
        self._register_notice(notice)
        return notice

    @staticmethod
    def _select_sunset_date(
        candidates: Sequence[datetime.datetime], published_utc: datetime.datetime
    ) -> Tuple[Optional[datetime.datetime], int]:
        """Pick the earliest candidate that plausibly names a future retirement.

        Two filters, in order:

        1. Dates *strictly after* the publication day are preferred. An entry cannot
           announce a retirement that already happened before it was written, and the
           publication date itself is nearly always restated in the body ("Posted
           2026-01-05, ..."), where taking it as the sunset date would report a live
           endpoint as already retired.
        2. If nothing survives, a date *on* the publication day is accepted, so an
           entry that announces a same-day retirement is not silently dropped.

        Among survivors the earliest wins: the soonest deadline is the one the desk
        has to act on, and a later date in the same entry is usually the support
        window for the replacement API.
        """
        floor = published_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        after_publication = [d for d in candidates if d > floor]
        if after_publication:
            return after_publication[0], len(after_publication)
        on_publication_day = [d for d in candidates if d == floor]
        if on_publication_day:
            return on_publication_day[0], len(on_publication_day)
        return None, 0

    @staticmethod
    def _changelog_entry_id(
        broker_name: str,
        title: str,
        published_utc: datetime.datetime,
        link: Optional[str],
    ) -> str:
        """Stable identity for one changelog entry.

        Without this every entry from a broker collapses onto the same registry key
        and each new announcement silently evicts the previous one — the monitor
        would report a single outstanding deprecation no matter how many the broker
        published. The permalink is the identifier when the feed supplies one.
        """
        if link:
            return link
        seed = f"{broker_name}|{title}|{published_utc.date().isoformat()}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
