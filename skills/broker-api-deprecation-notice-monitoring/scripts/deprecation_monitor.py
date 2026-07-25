"""
broker-api-deprecation-notice-monitoring: RFC 8594 Sunset header scanner and developer
changelog monitor for impending broker API endpoint retirements.
"""
from dataclasses import dataclass, field
import datetime
import logging
import re
import time
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DeprecationUrgency(Enum):
    NONE = "NONE"
    NOTICE = "NOTICE"
    WARNING_30_DAYS = "WARNING_30_DAYS"
    CRITICAL_SUNSET_IMMINENT = "CRITICAL_SUNSET_IMMINENT"
    EXPIRED = "EXPIRED"


@dataclass
class DeprecationNotice:
    broker_name: str
    endpoint: str
    sunset_date_utc: Optional[datetime.date]
    days_remaining: Optional[int]
    urgency: DeprecationUrgency
    source: str  # "HTTP_HEADER" or "CHANGELOG_FEED"
    message: str


class BrokerDeprecationMonitor:
    """
    Scans HTTP response headers for RFC 8594 Sunset headers and parses developer
    changelog feeds to issue sunset countdown alerts.
    """

    def __init__(self, now_fn: Optional[Any] = None):
        self._now_fn = now_fn or (lambda: datetime.date.today())
        self.active_notices: List[DeprecationNotice] = []

    def parse_http_date(self, date_str: str) -> Optional[datetime.date]:
        """Parses RFC 1123 / ISO date strings into a date object."""
        date_str = date_str.strip()
        # Try ISO format (YYYY-MM-DD)
        if re.match(r"^\d{4}-\d{2}-\d{2}", date_str):
            try:
                return datetime.datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            except ValueError:
                pass

        # Try RFC 1123 format (e.g., Wed, 11 Nov 2026 00:00:00 GMT)
        formats = [
            "%a, %d %b %Y %H:%M:%S GMT",
            "%a, %d %b %Y %H:%M:%S %z",
            "%d %b %Y",
        ]
        for fmt in formats:
            try:
                return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        return None

    def inspect_http_headers(
        self,
        broker_name: str,
        endpoint: str,
        response_headers: Dict[str, str],
    ) -> Optional[DeprecationNotice]:
        """
        Inspects response headers for `Sunset`, `Deprecation`, or `X-API-Deprecation-Warning`.
        """
        headers_lower = {k.lower(): str(v) for k, v in response_headers.items()}

        sunset_raw = headers_lower.get("sunset")
        deprecation_raw = headers_lower.get("deprecation")
        warning_raw = headers_lower.get("x-api-deprecation-warning") or headers_lower.get("x-deprecation-warning")

        if not (sunset_raw or deprecation_raw or warning_raw):
            return None

        sunset_date = self.parse_http_date(sunset_raw) if sunset_raw else None
        today = self._now_fn()

        days_remaining = None
        urgency = DeprecationUrgency.NOTICE

        if sunset_date:
            days_remaining = (sunset_date - today).days
            if days_remaining <= 0:
                urgency = DeprecationUrgency.EXPIRED
            elif days_remaining <= 7:
                urgency = DeprecationUrgency.CRITICAL_SUNSET_IMMINENT
            elif days_remaining <= 30:
                urgency = DeprecationUrgency.WARNING_30_DAYS

        msg = (
            f"DEPRECATION NOTICE [{broker_name} {endpoint}]: "
            f"Sunset Date: {sunset_date} ({days_remaining} days remaining). "
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
        )
        self.active_notices.append(notice)
        return notice

    def parse_changelog_entry(
        self,
        broker_name: str,
        title: str,
        content: str,
        publish_date: datetime.date,
    ) -> Optional[DeprecationNotice]:
        """Scans changelog RSS/JSON entries for breaking change or deprecation keywords."""
        text = f"{title} {content}".lower()
        keywords = ["deprecated", "sunset", "breaking change", "end of life", "retired"]

        if not any(kw in text for kw in keywords):
            return None

        # Look for date patterns in content
        match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
        sunset_date = self.parse_http_date(match.group(1)) if match else None

        today = self._now_fn()
        days_remaining = (sunset_date - today).days if sunset_date else None
        urgency = DeprecationUrgency.WARNING_30_DAYS if (days_remaining and days_remaining <= 30) else DeprecationUrgency.NOTICE

        msg = f"CHANGELOG DEPRECATION ALERT [{broker_name}]: '{title}' — Published {publish_date}"

        notice = DeprecationNotice(
            broker_name=broker_name,
            endpoint="GLOBAL_FEED",
            sunset_date_utc=sunset_date,
            days_remaining=days_remaining,
            urgency=urgency,
            source="CHANGELOG_FEED",
            message=msg,
        )
        self.active_notices.append(notice)
        return notice
