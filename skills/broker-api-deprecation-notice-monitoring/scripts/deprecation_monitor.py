"""
broker-api-deprecation-notice-monitoring: RFC 8594 Sunset header scanner and developer
changelog monitor for impending broker API endpoint retirements.
Designed for quantitative algorithmic trading systems requiring robust lifecycle management.
"""
import datetime
import logging
import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import email.utils

logger = logging.getLogger(__name__)

class DeprecationUrgency(Enum):
    NONE = "NONE"
    NOTICE = "NOTICE"
    WARNING_30_DAYS = "WARNING_30_DAYS"
    CRITICAL_SUNSET_IMMINENT = "CRITICAL_SUNSET_IMMINENT"
    EXPIRED = "EXPIRED"

@dataclass(frozen=True)
class DeprecationNotice:
    broker_name: str
    endpoint: str
    sunset_date_utc: Optional[datetime.datetime]
    days_remaining: Optional[int]
    urgency: DeprecationUrgency
    source: str  # "HTTP_HEADER" or "CHANGELOG_FEED"
    message: str
    reference_link: Optional[str] = None

class BrokerDeprecationMonitor:
    """
    Scans HTTP response headers for RFC 8594 Sunset headers and parses developer
    changelog feeds to issue sunset countdown alerts. Thread-safe for multi-threaded bot execution.
    """

    def __init__(self, 
                 now_fn: Optional[Callable[[], datetime.datetime]] = None,
                 alert_callback: Optional[Callable[[DeprecationNotice], None]] = None):
        """
        :param now_fn: Dependency injection for current time (defaults to datetime.now(timezone.utc)).
        :param alert_callback: Optional callback for emitting alerts to external systems (e.g. PagerDuty, Slack).
        """
        self._now_fn = now_fn or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self._alert_callback = alert_callback
        
        self._lock = threading.RLock()
        self._active_notices: Dict[str, DeprecationNotice] = {} # Keyed by broker:endpoint

    @property
    def active_notices(self) -> List[DeprecationNotice]:
        with self._lock:
            return list(self._active_notices.values())

    @staticmethod
    def parse_http_date(date_str: str) -> Optional[datetime.datetime]:
        """Parses RFC 1123 / RFC 852 / ISO date strings into a timezone-aware UTC datetime."""
        date_str = date_str.strip()
        
        # Try ISO 8601 subset format (YYYY-MM-DD)
        iso_match = re.match(r"^(\d{4}-\d{2}-\d{2})", date_str)
        if iso_match:
            try:
                dt = datetime.datetime.strptime(iso_match.group(1), "%Y-%m-%d")
                return dt.replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                pass

        # Try RFC 2822 / 1123 which handles 'Wed, 11 Nov 2026 00:00:00 GMT' natively in python
        parsed_tuple = email.utils.parsedate_tz(date_str)
        if parsed_tuple:
            dt = datetime.datetime(*parsed_tuple[:6])
            tz_offset = parsed_tuple[9] or 0
            dt = dt.replace(tzinfo=datetime.timezone(datetime.timedelta(seconds=tz_offset)))
            # Convert to UTC
            return dt.astimezone(datetime.timezone.utc)

        return None
        
    @staticmethod
    def extract_sunset_link(link_header: str) -> Optional[str]:
        """Extracts the URL from an RFC 5988 Link header containing rel="sunset"."""
        # e.g. <https://api.example.com/sunset>; rel="sunset"; type="text/html"
        links = link_header.split(',')
        for link in links:
            match = re.search(r'<(.*?)>.*rel=["\']?sunset["\']?', link, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _calculate_urgency(self, sunset_date: Optional[datetime.datetime]) -> DeprecationUrgency:
        if not sunset_date:
            return DeprecationUrgency.NOTICE
            
        now_utc = self._now_fn()
        days_remaining = (sunset_date - now_utc).days
        
        if days_remaining <= 0:
            return DeprecationUrgency.EXPIRED
        elif days_remaining <= 7:
            return DeprecationUrgency.CRITICAL_SUNSET_IMMINENT
        elif days_remaining <= 30:
            return DeprecationUrgency.WARNING_30_DAYS
        return DeprecationUrgency.NOTICE

    def _register_notice(self, notice: DeprecationNotice) -> None:
        key = f"{notice.broker_name}:{notice.endpoint}"
        with self._lock:
            existing = self._active_notices.get(key)
            if not existing or existing.urgency.value != notice.urgency.value or existing.sunset_date_utc != notice.sunset_date_utc:
                self._active_notices[key] = notice
                if self._alert_callback:
                    try:
                        self._alert_callback(notice)
                    except Exception as e:
                        logger.error(f"Failed to execute alert callback: {e}")

    def inspect_http_headers(
        self,
        broker_name: str,
        endpoint: str,
        response_headers: Dict[str, str],
    ) -> Optional[DeprecationNotice]:
        """
        Inspects response headers for `Sunset`, `Deprecation`, `X-API-Deprecation-Warning` and `Link`.
        Thread-safe analysis and registration.
        """
        headers_lower = {k.lower(): str(v).strip() for k, v in response_headers.items()}

        sunset_raw = headers_lower.get("sunset")
        deprecation_raw = headers_lower.get("deprecation")
        warning_raw = headers_lower.get("x-api-deprecation-warning") or headers_lower.get("x-deprecation-warning")
        link_raw = headers_lower.get("link")

        if not (sunset_raw or deprecation_raw or warning_raw or link_raw):
            return None
            
        sunset_link = self.extract_sunset_link(link_raw) if link_raw else None
        
        if not (sunset_raw or deprecation_raw or warning_raw or sunset_link):
            return None # False positive on Link header

        sunset_date = self.parse_http_date(sunset_raw) if sunset_raw else None
        urgency = self._calculate_urgency(sunset_date)

        days_remaining = (sunset_date - self._now_fn()).days if sunset_date else None
        days_str = f"({days_remaining} days remaining)" if sunset_date else "(Unknown sunset timeline)"
        msg = (
            f"DEPRECATION NOTICE [{broker_name} {endpoint}]: "
            f"Sunset Date: {sunset_date or 'TBD'} {days_str}. "
            f"Warning: {warning_raw or 'Endpoint scheduled for retirement.'}"
        )

        if urgency in (DeprecationUrgency.CRITICAL_SUNSET_IMMINENT, DeprecationUrgency.EXPIRED):
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
            reference_link=sunset_link
        )
        self._register_notice(notice)
        return notice

    def parse_changelog_entry(
        self,
        broker_name: str,
        title: str,
        content: str,
        publish_date: datetime.datetime,
        link: Optional[str] = None
    ) -> Optional[DeprecationNotice]:
        """Scans changelog RSS/JSON entries for breaking change or deprecation keywords using heuristics."""
        text = f"{title} {content}".lower()
        keywords = [r"\bdeprecated\b", r"\bsunset\b", r"\bbreaking change\b", r"\bend of life\b", r"\bretired\b"]

        if not any(re.search(kw, text) for kw in keywords):
            return None

        # Look for ISO date patterns in content
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        sunset_date = self.parse_http_date(match.group(1)) if match else None

        urgency = self._calculate_urgency(sunset_date)
        days_remaining = (sunset_date - self._now_fn()).days if sunset_date else None

        msg = f"CHANGELOG DEPRECATION ALERT [{broker_name}]: '{title}' — Published {publish_date.date()}"

        notice = DeprecationNotice(
            broker_name=broker_name,
            endpoint="GLOBAL_FEED",
            sunset_date_utc=sunset_date,
            days_remaining=days_remaining,
            urgency=urgency,
            source="CHANGELOG_FEED",
            message=msg,
            reference_link=link
        )
        self._register_notice(notice)
        return notice
