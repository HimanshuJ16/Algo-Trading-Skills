"""Structured JSON log events for post-incident forensics in trading systems.

This module produces one machine-parseable JSON object per event so that, after an
incident, an order's full lifecycle can be reconstructed by filtering rather than by
reading prose. Four design decisions drive everything below.

**Emitting a log record must never raise.** ``ForensicLogger.emit`` is called from
``except`` blocks, from partial-fill handlers, and from the path that fires the kill
switch — exactly the moments when the record matters most and when the inputs are least
trustworthy. A logger that raises there converts a recoverable incident into an
unexplainable one. Every input this module accepts is therefore coerced, never rejected:
an unknown severity, an unserializable ``metadata`` value, a circular reference, a NaN,
or a non-``EventType`` event name all produce a *recorded, flagged* event rather than an
exception. A prior revision resolved ``severity`` with ``getattr(logging, severity)``,
so ``severity="warning"`` (lowercase) resolved to the *function* ``logging.warning`` and
``Logger.log`` raised ``TypeError: level must be an integer`` from inside the emit path.

**Ordering comes from the sequence number, not from the wall clock and not from the
order lines land in the sink.** Wall-clock timestamps step backwards under NTP
correction and diverge between hosts; under MiFID II RTS 25 an HFT firm's business clock
may legitimately sit up to 100 microseconds from UTC. Sequence assignment and buffer
insertion happen together under one lock, so ``(instance_id, seq)`` is a total order
within a process. Sink *line* order is deliberately not guaranteed — the lock is
released before the I/O so logging never serialises the order path — which is why every
record carries its sequence number and why a forensic consumer must sort by
``(instance_id, seq)`` rather than trusting file order. A prior revision incremented the
counter and appended outside any lock; under eight threads emitting concurrently, ~30%
of adjacent buffer entries were out of sequence order.

**Correlation IDs must not collide.** A collision silently merges two unrelated order
lifecycles into one "timeline", which is worse than having no timeline: the
reconstruction looks complete and is wrong. A prior revision used
``str(uuid.uuid4())[:12]`` — 11 hex digits, ~44 bits, a 50% birthday collision at about
4.2 million IDs, well inside one year of a busy order flow. IDs here are 32 lowercase
hex characters, the shape and randomness the W3C Trace Context ``trace-id`` requires.

**Secrets written into an audit log cannot be taken back.** Where these records are
retained under an immutable regime — SEC Rule 17a-4(f) WORM or its audit-trail
alternative, which by design preserves every version of a record — an API secret that
reaches ``metadata`` is unremovable for the whole retention period. ``metadata`` keys are
therefore matched against a redaction set before serialisation.

The record schema, its field-by-field justification, and the recordkeeping regimes it is
shaped for are documented in ``references/standards.md``.

Requires Python 3.7+ (``time.time_ns``, ``time.monotonic_ns``, insertion-ordered dicts).
"""

import json
import logging
import math
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, FrozenSet, Iterable, List, Optional, Tuple, Union

# Diagnostics *about* the logger (redaction notices, sink failures). Deliberately not the
# stream the JSON records go to: mixing prose warnings into the forensic stream breaks
# any JSONL consumer reading it line by line.
logger = logging.getLogger(__name__)

# Bumped whenever a field is added, removed, or changes meaning. Stamped on every record
# because these logs outlive the parser that reads them: RTS 6 Art. 28 keeps HFT order
# records for five years, FINRA Rule 4511(c) six.
SCHEMA_VERSION = "2.0.0"

# Default sink logger name. Callers routing per component pass their own ``sink``.
DEFAULT_SINK_LOGGER_NAME = "forensic"

# In-memory retention. The buffer is a live-debugging and unit-testing aid, NOT the
# record of truth — that is whatever durable sink the handlers write to. Unbounded
# growth in a long-running bot emitting per-order events is an OOM with no upper bound,
# so the buffer is a ring and evictions are counted (see ``buffer_status``).
DEFAULT_BUFFER_CAPACITY = 100_000

REDACTED_PLACEHOLDER = "[REDACTED]"

# Matched against the lowercased metadata key, exactly — not as a substring, so
# ``token_bucket_size`` is not mistaken for a credential. Extend per deployment via the
# ``redact_keys`` constructor argument.
DEFAULT_REDACT_KEYS: FrozenSet[str] = frozenset({
    "api_key", "api_secret", "apikey", "apisecret", "access_token", "refresh_token",
    "auth_token", "authorization", "client_secret", "credentials", "otp", "passphrase",
    "password", "private_key", "secret", "secret_key", "session_token", "totp", "token",
})

# Bounds on the metadata walk. Depth stops runaway nesting; the repr cap stops one
# oversized object from dominating log volume (and cost) on a per-order event.
MAX_METADATA_DEPTH = 8
MAX_REPR_LENGTH = 512

_TRUNCATION_MARKER = "...<truncated>"
_MAX_DEPTH_MARKER = "<max-depth-exceeded>"
_CIRCULAR_MARKER = "<circular-reference>"

NS_PER_SECOND = 1_000_000_000


class EventType(Enum):
    """Standardised trading-system event taxonomy.

    The request/confirmation split (``*_REQUESTED`` vs. the past-tense member) is the
    point of the taxonomy, not verbosity: "we asked the venue to cancel" and "the venue
    confirmed the cancel" are different facts, and an incident timeline that cannot
    separate them cannot answer whether an order was live at a given instant.
    """

    STRATEGY_SIGNAL = "STRATEGY_SIGNAL"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_ACKNOWLEDGED = "ORDER_ACKNOWLEDGED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_MODIFY_REQUESTED = "ORDER_MODIFY_REQUESTED"
    ORDER_MODIFIED = "ORDER_MODIFIED"
    ORDER_CANCEL_REQUESTED = "ORDER_CANCEL_REQUESTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_EXPIRED = "ORDER_EXPIRED"
    PARTIAL_FILL_RECEIVED = "PARTIAL_FILL_RECEIVED"
    FILL_RECEIVED = "FILL_RECEIVED"
    POSITION_UPDATE = "POSITION_UPDATE"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    RISK_BREACH = "RISK_BREACH"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"
    CONNECTIVITY_LOSS = "CONNECTIVITY_LOSS"
    CONNECTIVITY_RESTORED = "CONNECTIVITY_RESTORED"
    MARKET_DATA_GAP = "MARKET_DATA_GAP"
    CONFIG_CHANGED = "CONFIG_CHANGED"
    DEPLOYMENT_EVENT = "DEPLOYMENT_EVENT"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class Severity(Enum):
    """Severity levels, mapped to both a Python logging level and an OTel SeverityNumber.

    Keeping the set closed is what makes ``severity`` safe to use as a filter across a
    five-year archive; an open string field degrades into ``WARN``/``WARNING``/``warn``
    variants that no query catches all of.
    """

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Explicit table — never ``getattr(logging, name)``, which resolves arbitrary module
# attributes (``logging.shutdown``, ``logging.raiseExceptions``) into the level argument.
SEVERITY_PYTHON_LEVEL: Dict[Severity, int] = {
    Severity.DEBUG: logging.DEBUG,
    Severity.INFO: logging.INFO,
    Severity.WARNING: logging.WARNING,
    Severity.ERROR: logging.ERROR,
    Severity.CRITICAL: logging.CRITICAL,
}

# OpenTelemetry Logs Data Model SeverityNumber: TRACE 1-4, DEBUG 5-8, INFO 9-12,
# WARN 13-16, ERROR 17-20, FATAL 21-24. The base of each range is used so records
# survive a move into an OTel-shaped pipeline without a lossy re-derivation.
SEVERITY_OTEL_NUMBER: Dict[Severity, int] = {
    Severity.DEBUG: 5,
    Severity.INFO: 9,
    Severity.WARNING: 13,
    Severity.ERROR: 17,
    Severity.CRITICAL: 21,
}

# Aliases accepted from callers wired to another vocabulary. Anything outside this map
# and the enum is still recorded (at ERROR) with the raw value preserved.
_SEVERITY_ALIASES: Dict[str, Severity] = {
    "WARN": Severity.WARNING,
    "ERR": Severity.ERROR,
    "FATAL": Severity.CRITICAL,
    "CRIT": Severity.CRITICAL,
}


def new_correlation_id() -> str:
    """Return a fresh 32-lowercase-hex correlation ID.

    The shape and entropy of a W3C Trace Context ``trace-id``: 16 random bytes, which
    the specification says SHOULD be globally unique and SHOULD be randomly generated.
    Full width matters because a truncated ID collides silently — two unrelated order
    lifecycles merge into one timeline that looks complete and is wrong.
    """
    return uuid.uuid4().hex


def _new_instance_id() -> str:
    """Return an identifier unique to this ``ForensicLogger`` instance.

    Sequence numbers restart at 1 in every new instance and every new process, so a
    sequence number alone is ambiguous the moment two processes' logs are merged or a
    bot restarts mid-incident. The PID makes the record cross-referenceable against OS
    and supervisor logs; the random suffix disambiguates a recycled PID.
    """
    return f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _coerce_severity(value: Union[Severity, str, None]) -> Tuple[Severity, Optional[str]]:
    """Return ``(severity, invalid_raw_value)``; never raises.

    An unrecognised severity is recorded at ERROR rather than dropped or raised: the
    caller clearly intended to log *something*, and losing the event is a worse outcome
    than filing it one level too loud. The raw value is returned so the event can carry
    evidence of the mis-call instead of hiding it.
    """
    if isinstance(value, Severity):
        return value, None
    if value is None:
        return Severity.INFO, None
    raw = _safe_str(value, "<unrepresentable-severity>")
    name = raw.strip().upper()
    if name in Severity.__members__:
        return Severity[name], None
    if name in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[name], None
    return Severity.ERROR, raw


def _coerce_event_type(value: Union[EventType, str, None]) -> Tuple[str, bool]:
    """Return ``(event_type_string, is_known)``; never raises.

    An unknown event type is recorded verbatim rather than rejected. Refusing to log an
    event because its type is not in the enum would mean the taxonomy silently censors
    the incident it was meant to describe.
    """
    if isinstance(value, EventType):
        return value.value, True
    name = _safe_str(value, "UNKNOWN_EVENT_TYPE")
    return name, name in EventType.__members__


def _safe_str(value: Any, fallback: str) -> str:
    """``str(value)`` that cannot raise.

    Every conversion this module performs on caller-supplied data goes through here.
    A ``__str__`` that raises is not hypothetical: partially-initialised ORM rows and
    proxy objects whose backing connection has dropped both do it, and the moment they
    do it is inside the ``except`` block trying to record the outage.
    """
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - the caller's __str__ is not ours to trust
        return fallback


def _truncate_repr(value: Any) -> str:
    """Return a bounded ``repr`` of an object the JSON encoder cannot represent."""
    try:
        text = repr(value)
    except Exception:  # pragma: no cover - hostile __repr__
        return f"<unreprable {type(value).__name__}>"
    if len(text) > MAX_REPR_LENGTH:
        return text[:MAX_REPR_LENGTH] + _TRUNCATION_MARKER
    return text


def sanitize_metadata(
    value: Any,
    redact_keys: Iterable[str] = DEFAULT_REDACT_KEYS,
    _depth: int = 0,
    _seen: Optional[FrozenSet[int]] = None,
) -> Any:
    """Return a JSON-serialisable, redacted copy of ``value``; never raises.

    Four hazards are handled here rather than at the encoder, because the encoder's
    failure mode is an exception thrown from inside the emit path:

    * **Unserialisable objects and non-string dict keys.** ``json.dumps`` raises
      ``TypeError`` on a ``dict`` keyed by a tuple regardless of any ``default=`` hook,
      because ``default`` covers values only.
    * **Circular references.** ``json.dumps`` raises ``ValueError`` on a self-referencing
      structure; a position object holding a back-reference to its portfolio is enough.
    * **Non-finite floats.** ``json.dumps`` emits bare ``NaN`` / ``Infinity``, which are
      not valid JSON — a strict downstream parser rejects the whole line, so an
      unpriceable Greek silently destroys the record it appears in.
    * **Secrets.** Keys whose lowercased form is in ``redact_keys`` are replaced before
      the value is ever serialised.

    Returning a *copy* also snapshots the metadata at emit time: a caller that later
    mutates the dict it passed in cannot retroactively rewrite the recorded event.
    """
    redact = {str(k).lower() for k in redact_keys}
    return _sanitize(value, redact, _depth, _seen or frozenset())


def _sanitize(value: Any, redact: set, depth: int, seen: FrozenSet[int]) -> Any:
    if depth > MAX_METADATA_DEPTH:
        return _MAX_DEPTH_MARKER

    if value is None or isinstance(value, (str, bool, int)):
        # bool before int is irrelevant here (both are JSON-native); str is bounded by
        # the caller, not by us — truncating a message would lose forensic content.
        return value

    if isinstance(value, float):
        # NaN/Inf are not representable in JSON; keep the fact, lose the invalid token.
        return value if math.isfinite(value) else repr(value)

    if isinstance(value, (dict, list, tuple, set, frozenset)):
        marker = id(value)
        if marker in seen:
            return _CIRCULAR_MARKER
        nested_seen = seen | {marker}
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            try:
                items = list(value.items())
            except Exception:  # noqa: BLE001 - a dict subclass may override items()
                return _truncate_repr(value)
            for raw_key, raw_value in items:
                key = _safe_str(raw_key, "<unrepresentable-key>")
                if key.lower() in redact:
                    out[key] = REDACTED_PLACEHOLDER
                else:
                    out[key] = _sanitize(raw_value, redact, depth + 1, nested_seen)
            return out
        try:
            items = list(value)
        except Exception:  # noqa: BLE001 - a sequence subclass may override __iter__
            return _truncate_repr(value)
        return [_sanitize(item, redact, depth + 1, nested_seen) for item in items]

    return _truncate_repr(value)


@dataclass(frozen=True)
class StructuredLogEvent:
    """One immutable forensic record.

    Frozen because an audit record that can be edited after the fact is not evidence.
    ``metadata`` holds a sanitised copy taken at emit time, not the caller's dict.
    """

    sequence_number: int
    instance_id: str
    timestamp_ns: int
    monotonic_ns: int
    event_type: str
    correlation_id: str
    component: str
    message: str
    severity: str
    severity_number: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def timestamp_iso(self) -> str:
        """RFC 3339 UTC timestamp with full nanosecond precision.

        Rendered from the integer nanoseconds rather than a float, so no digit is lost
        to binary rounding. This is the field a human and most log stores read; the
        authoritative value is ``timestamp_ns``.
        """
        seconds, nanos = divmod(self.timestamp_ns, NS_PER_SECOND)
        base = datetime.fromtimestamp(seconds, tz=timezone.utc)
        return f"{base.strftime('%Y-%m-%dT%H:%M:%S')}.{nanos:09d}Z"

    def to_dict(self) -> Dict[str, Any]:
        """Return the wire form of the record, in stable field order."""
        return {
            "schema_version": SCHEMA_VERSION,
            "seq": self.sequence_number,
            "instance_id": self.instance_id,
            "ts_ns": self.timestamp_ns,
            "ts_iso": self.timestamp_iso,
            "mono_ns": self.monotonic_ns,
            "event_type": self.event_type,
            "correlation_id": self.correlation_id,
            "component": self.component,
            "severity": self.severity,
            "severity_number": self.severity_number,
            "message": self.message,
            "metadata": self.metadata,
        }

    def to_json(self) -> str:
        """Serialise to a single-line JSON object.

        ``allow_nan=False`` makes invalid JSON an error rather than a silently poisoned
        line; ``sanitize_metadata`` has already removed every value that could trigger
        it. Newlines inside ``message`` or ``metadata`` are escaped by the encoder, so a
        hostile or malformed string cannot forge an extra record in a line-delimited
        sink.
        """
        return json.dumps(
            self.to_dict(), separators=(",", ":"), allow_nan=False, default=_truncate_repr
        )


class ForensicLogger:
    """Thread-safe structured event logger for post-incident timeline reconstruction.

    Emits one JSON object per event to ``sink`` and retains a bounded ring buffer of the
    most recent events for in-process querying.

    Guarantees:

    * ``emit`` never raises, whatever it is handed.
    * ``(instance_id, sequence_number)`` is a strict total order over the events this
      instance emitted, and the retained buffer is held in that order.
    * ``monotonic_ns`` is non-decreasing in ``sequence_number`` — it is read under the
      same lock that assigns the sequence — so an elapsed time computed from it is
      immune to the wall-clock steps that ``timestamp_ns`` is exposed to.

    Explicitly not guaranteed:

    * **Sink line order.** The lock is released before the I/O so that logging never
      serialises the order path. Sort by ``(instance_id, seq)`` when reading back.
    * **Durability.** The ring buffer is a debugging aid. Retention, immutability, and
      the WORM or audit-trail properties a recordkeeping regime requires belong to the
      handler and storage layer, not here.
    """

    def __init__(
        self,
        component: str = "trading-bot",
        *,
        buffer_capacity: int = DEFAULT_BUFFER_CAPACITY,
        redact_keys: Optional[Iterable[str]] = None,
        sink: Optional[logging.Logger] = None,
        instance_id: Optional[str] = None,
    ) -> None:
        if isinstance(buffer_capacity, bool) or not isinstance(buffer_capacity, int):
            raise TypeError("buffer_capacity must be an int.")
        if buffer_capacity < 1:
            raise ValueError("buffer_capacity must be at least 1.")
        self.component = str(component)
        self.instance_id = str(instance_id) if instance_id else _new_instance_id()
        self.redact_keys: FrozenSet[str] = frozenset(
            str(k).lower() for k in (redact_keys if redact_keys is not None else DEFAULT_REDACT_KEYS)
        )
        self._sink = sink if sink is not None else logging.getLogger(DEFAULT_SINK_LOGGER_NAME)
        self._lock = threading.Lock()
        self._events: Deque[StructuredLogEvent] = deque(maxlen=buffer_capacity)
        self._sequence = 0
        self._evicted = 0
        self._sink_failures = 0

    # --- emission ---------------------------------------------------------

    def new_correlation_id(self) -> str:
        """Generate a new correlation ID for linking related events."""
        return new_correlation_id()

    def emit(
        self,
        event_type: Union[EventType, str],
        message: str,
        correlation_id: Optional[str] = None,
        severity: Union[Severity, str] = Severity.INFO,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> StructuredLogEvent:
        """Record one event and write it to the sink. Never raises.

        Malformed input is recorded and flagged rather than rejected. Flags land in
        ``metadata`` under reserved underscore-prefixed keys so a query can find every
        mis-instrumented call site after the fact:

        * ``_invalid_severity`` — the raw severity that could not be resolved; the event
          is filed at ERROR.
        * ``_unknown_event_type`` — the event type is not an ``EventType`` member.
        * ``_serialization_error`` — the record could not be encoded and a degraded
          placeholder was written to the sink (the in-buffer event is still intact).
        """
        event_type_str, known_type = _coerce_event_type(event_type)
        resolved_severity, invalid_severity = _coerce_severity(severity)

        message_str = _safe_str(message, "<unrepresentable-message>")

        safe_metadata = sanitize_metadata(metadata or {}, self.redact_keys)
        if not isinstance(safe_metadata, dict):
            # A caller passing a non-mapping keeps its content rather than losing it.
            safe_metadata = {"_metadata": safe_metadata}
        if invalid_severity is not None:
            safe_metadata["_invalid_severity"] = invalid_severity
        if not known_type:
            safe_metadata["_unknown_event_type"] = True

        # Truthiness is evaluated defensively: ``__bool__`` is caller code too.
        try:
            has_cid = bool(correlation_id)
        except Exception:  # noqa: BLE001 - hostile __bool__
            has_cid = True
        cid = _safe_str(correlation_id, "<unrepresentable-correlation-id>") if has_cid else new_correlation_id()

        with self._lock:
            self._sequence += 1
            if self._events.maxlen is not None and len(self._events) == self._events.maxlen:
                self._evicted += 1
            event = StructuredLogEvent(
                sequence_number=self._sequence,
                instance_id=self.instance_id,
                timestamp_ns=time.time_ns(),
                monotonic_ns=time.monotonic_ns(),
                event_type=event_type_str,
                correlation_id=cid,
                component=self.component,
                message=message_str,
                severity=resolved_severity.value,
                severity_number=SEVERITY_OTEL_NUMBER[resolved_severity],
                metadata=safe_metadata,
            )
            self._events.append(event)

        self._write_to_sink(event, SEVERITY_PYTHON_LEVEL[resolved_severity])
        return event

    def _write_to_sink(self, event: StructuredLogEvent, level: int) -> None:
        """Write one record to the sink, absorbing any sink or encoder failure.

        A logging handler that fails — a full disk, a wedged socket to the aggregator —
        must not propagate into the trading path. The failure is counted so
        ``buffer_status`` can report that the durable record is incomplete.
        """
        try:
            payload = event.to_json()
        except (TypeError, ValueError) as exc:
            self._record_sink_failure()
            logger.warning("Forensic record seq=%s failed to serialise: %s", event.sequence_number, exc)
            payload = json.dumps({
                "schema_version": SCHEMA_VERSION,
                "seq": event.sequence_number,
                "instance_id": event.instance_id,
                "ts_ns": event.timestamp_ns,
                "event_type": event.event_type,
                "correlation_id": event.correlation_id,
                "component": event.component,
                "severity": event.severity,
                "message": event.message,
                "metadata": {"_serialization_error": str(exc)},
            }, separators=(",", ":"))
        try:
            self._sink.log(level, payload)
        except Exception as exc:  # noqa: BLE001 - a broken sink must not kill the caller
            self._record_sink_failure()
            logger.warning("Forensic sink rejected seq=%s: %s", event.sequence_number, exc)

    def _record_sink_failure(self) -> None:
        """Count a durable-write failure under the lock so ``buffer_status`` is exact."""
        with self._lock:
            self._sink_failures += 1

    # --- querying ---------------------------------------------------------

    def _snapshot(self) -> List[StructuredLogEvent]:
        """Return a consistent copy of the retained buffer."""
        with self._lock:
            return list(self._events)

    def query_by_correlation_id(self, correlation_id: str) -> List[StructuredLogEvent]:
        """Return retained events for ``correlation_id``, in sequence order."""
        target = str(correlation_id)
        return sorted(
            (e for e in self._snapshot() if e.correlation_id == target),
            key=lambda e: e.sequence_number,
        )

    def query_by_event_type(self, event_type: Union[EventType, str]) -> List[StructuredLogEvent]:
        """Return retained events of one type, in sequence order."""
        target = event_type.value if isinstance(event_type, EventType) else str(event_type)
        return sorted(
            (e for e in self._snapshot() if e.event_type == target),
            key=lambda e: e.sequence_number,
        )

    def reconstruct_timeline(self, correlation_id: str) -> List[Dict[str, Any]]:
        """Reconstruct the ordered timeline for one correlation ID.

        Each entry carries both timestamps and, where every event in the timeline came
        from this same logger instance, ``elapsed_ms`` measured from the first event on
        the **monotonic** clock — the only elapsed figure that survives an NTP step
        mid-incident. When events span instances the field is ``None``, because two
        processes' monotonic clocks share no epoch and differencing them is meaningless.

        A truncated buffer yields a *partial* timeline that looks whole. When events
        have been evicted this logs a warning; check ``buffer_status()["complete"]``
        before drawing a conclusion from a reconstruction, and prefer replaying the
        durable sink for any incident older than the buffer.
        """
        events = self.query_by_correlation_id(correlation_id)
        if self._evicted:
            logger.warning(
                "Timeline for correlation_id=%s reconstructed from a buffer that has "
                "evicted %d event(s); it may be incomplete. Replay the durable sink.",
                correlation_id, self._evicted,
            )
        single_instance = len({e.instance_id for e in events}) <= 1
        base_mono = events[0].monotonic_ns if events else 0
        return [
            {
                "seq": e.sequence_number,
                "instance_id": e.instance_id,
                "ts_ns": e.timestamp_ns,
                "ts_iso": e.timestamp_iso,
                "elapsed_ms": (
                    (e.monotonic_ns - base_mono) / 1_000_000.0 if single_instance else None
                ),
                "event_type": e.event_type,
                "correlation_id": e.correlation_id,
                "component": e.component,
                "severity": e.severity,
                "message": e.message,
                "metadata": e.metadata,
            }
            for e in events
        ]

    def buffer_status(self) -> Dict[str, Any]:
        """Report whether the in-memory buffer still holds the complete event history.

        ``complete`` is False once anything has been evicted or a sink write failed —
        the point at which an in-memory reconstruction stops being authoritative.
        """
        with self._lock:
            retained = len(self._events)
            first_seq = self._events[0].sequence_number if self._events else None
            evicted = self._evicted
            emitted = self._sequence
            sink_failures = self._sink_failures
        return {
            "instance_id": self.instance_id,
            "component": self.component,
            "capacity": self._events.maxlen,
            "emitted": emitted,
            "retained": retained,
            "evicted": evicted,
            "first_retained_seq": first_seq,
            "sink_failures": sink_failures,
            "complete": evicted == 0 and sink_failures == 0,
        }

    def get_all_events_json(self) -> List[str]:
        """Export the retained events as JSON lines, in sequence order."""
        return [e.to_json() for e in self._snapshot()]
