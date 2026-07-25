"""
structured-logging-for-post-incident-forensics: Structured JSON log event system
with correlation IDs and sequence numbers for post-incident timeline reconstruction.
"""
from dataclasses import dataclass, field
import json
import logging
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventType(Enum):
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    FILL_RECEIVED = "FILL_RECEIVED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    RISK_BREACH = "RISK_BREACH"
    POSITION_UPDATE = "POSITION_UPDATE"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    CONNECTIVITY_LOSS = "CONNECTIVITY_LOSS"
    CONNECTIVITY_RESTORED = "CONNECTIVITY_RESTORED"
    STRATEGY_SIGNAL = "STRATEGY_SIGNAL"
    DEPLOYMENT_EVENT = "DEPLOYMENT_EVENT"


@dataclass
class StructuredLogEvent:
    sequence_number: int
    timestamp_utc: float
    event_type: str
    correlation_id: str
    component: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    severity: str = "INFO"

    def to_json(self) -> str:
        return json.dumps({
            "seq": self.sequence_number,
            "ts": self.timestamp_utc,
            "event_type": self.event_type,
            "correlation_id": self.correlation_id,
            "component": self.component,
            "severity": self.severity,
            "message": self.message,
            "metadata": self.metadata,
        }, default=str)


class ForensicLogger:
    """
    Structured logging system for trading bots that produces JSON log events
    with correlation IDs, monotonic sequence numbers, and standardized event types,
    enabling post-incident timeline reconstruction.
    """

    def __init__(self, component: str = "trading-bot"):
        self.component = component
        self._sequence = 0
        self._events: List[StructuredLogEvent] = []

    def _next_seq(self) -> int:
        self._sequence += 1
        return self._sequence

    def new_correlation_id(self) -> str:
        """Generate a new correlation ID for linking related events."""
        return str(uuid.uuid4())[:12]

    def emit(
        self,
        event_type: EventType,
        message: str,
        correlation_id: Optional[str] = None,
        severity: str = "INFO",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StructuredLogEvent:
        """Emit a structured log event."""
        event = StructuredLogEvent(
            sequence_number=self._next_seq(),
            timestamp_utc=time.time(),
            event_type=event_type.value,
            correlation_id=correlation_id or self.new_correlation_id(),
            component=self.component,
            message=message,
            severity=severity,
            metadata=metadata or {},
        )
        self._events.append(event)
        logger.log(
            getattr(logging, severity, logging.INFO),
            event.to_json(),
        )
        return event

    def query_by_correlation_id(self, correlation_id: str) -> List[StructuredLogEvent]:
        """Retrieve all events linked to a correlation ID for timeline reconstruction."""
        return [e for e in self._events if e.correlation_id == correlation_id]

    def query_by_event_type(self, event_type: EventType) -> List[StructuredLogEvent]:
        """Retrieve all events of a specific type."""
        return [e for e in self._events if e.event_type == event_type.value]

    def reconstruct_timeline(self, correlation_id: str) -> List[Dict]:
        """Reconstruct a chronological timeline for a given correlation ID."""
        events = self.query_by_correlation_id(correlation_id)
        events.sort(key=lambda e: e.sequence_number)
        return [
            {
                "seq": e.sequence_number,
                "event_type": e.event_type,
                "message": e.message,
                "severity": e.severity,
                "metadata": e.metadata,
            }
            for e in events
        ]

    def get_all_events_json(self) -> List[str]:
        """Export all events as JSON lines."""
        return [e.to_json() for e in self._events]
