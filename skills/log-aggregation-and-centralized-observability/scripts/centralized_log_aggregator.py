"""
log-aggregation-and-centralized-observability: batch log sanitiser and structured-JSON
formatter for distributed trading microservices -- credential redaction, OpenTelemetry
severity normalisation, diagnostic-level sampling, and error-velocity spike detection.

SCOPE BOUNDARY (read before relying on this engine):

1. REDACTION IS METADATA-KEY SCOPED. `redact_sensitive_metadata` matches *keys* in the
   `metadata` mapping (recursively, including inside lists). It never inspects the
   free-text `message`, and it cannot recognise a secret that was interpolated into a
   string (`f"auth failed for {api_key}"`). Never format a credential into a log message:
   there is no reliable way to detect one afterwards, and the log has already shipped.

2. ALERTING IS BATCH SCOPED AND STATELESS. `process_and_aggregate_logs` counts errors in
   the batch it is handed and nothing else. Alert latency therefore equals your flush
   cadence, and a spike split across two flushes may not trip either batch. This is an
   *infrastructure* alarm; it is not a real-time trading monitor. Firms in scope of
   MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) should note Article
   16(5): "Real-time alerts shall be generated within five seconds after the relevant
   event." A minute-long log-flush cycle cannot carry an obligation with a five-second
   bound -- keep that monitoring on its own path.

3. THE PAYLOAD IS NOT AN OpenTelemetry LOG RECORD. The OTel logs data model names its
   fields Timestamp, ObservedTimestamp, TraceId, SpanId, SeverityText, SeverityNumber,
   Body, Attributes and Resource. This engine emits its own flat schema (`timestamp_iso`,
   `correlation_id`, `subsystem`, `level`, `message`, `metadata`) and adds
   `severity_number` so a Collector/Loki pipeline can map it mechanically. The mapping
   table is in `references/standards.md`; write it into your Collector config explicitly.

Sources consulted are listed in `references/standards.md`.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

REDACTION_PLACEHOLDER = "[REDACTED]"
TRUNCATION_PLACEHOLDER = "[TRUNCATED: MAX_DEPTH]"

# Maximum nesting depth traversed when sanitising metadata. Bounds recursion on hostile,
# malformed, or self-referential structures (a dict that contains itself would otherwise
# raise RecursionError and lose the whole batch).
MAX_REDACTION_DEPTH = 12

# Exact key matches, compared after normalisation (lowercased, separators stripped), so
# "API-Key", "api_key" and "apiKey" all collapse to "apikey".
SENSITIVE_KEYS: Set[str] = {
    "api_key", "secret", "api_secret", "private_key", "password",
    "auth_header", "token", "authorization",
}

# Substring patterns matched against the normalised key. These catch the prefixed and
# suffixed variants an exact-match blocklist misses -- `broker_api_key`, `access_token`,
# `X-Api-Key` -- which is how credentials actually reach a log line.
#
# Deliberately NOT a bare "token" substring: crypto/DEX metadata legitimately carries
# `token_symbol`, `token_address`, `base_token`. Redacting those destroys the fields you
# need for forensics without protecting anything. The compound token patterns below are
# the ones that name a credential.
SENSITIVE_KEY_SUBSTRINGS: Set[str] = {
    "apikey", "secret", "password", "passwd", "passphrase",
    "privatekey", "privkey", "accesskey", "signingkey",
    "authorization", "authheader", "credential",
    "mnemonic", "seedphrase", "bearer", "cookie",
    "accesstoken", "refreshtoken", "idtoken", "bearertoken",
    "authtoken", "sessiontoken", "apitoken",
}

# Severity text -> OpenTelemetry SeverityNumber. The OTel logs data model defines the
# ranges TRACE 1-4, DEBUG 5-8, INFO 9-12, WARN 13-16, ERROR 17-20, FATAL 21-24, and its
# appendix maps Log4j FATAL->21 / ERROR->17 / WARN->13 / INFO->9 / DEBUG->5 / TRACE->1
# and syslog emerg->21, alert->19, err->17, warning->13, notice->10, info->9, debug->5.
#
# Python's CRITICAL is not in that appendix; it is Python's highest level, so it maps to
# FATAL(21) here. (Syslog's *crit* maps to 18 in the appendix -- a different scale using
# the same word. Both land in the ERROR-or-worse bucket, so spike counting is unaffected.)
SEVERITY_NUMBERS: Dict[str, int] = {
    "TRACE": 1, "VERBOSE": 5, "DEBUG": 5, "FINE": 5,
    "INFO": 9, "INFORMATIONAL": 9, "NOTICE": 10,
    "WARN": 13, "WARNING": 13,
    "ERROR": 17, "ERR": 17, "SEVERE": 17,
    "ALERT": 19,
    "CRIT": 21, "CRITICAL": 21, "FATAL": 21, "PANIC": 21,
    "EMERG": 21, "EMERGENCY": 21,
}

# OTel SeverityNumber 0 == UNSPECIFIED. Emitted for a level string this engine cannot map.
SEVERITY_UNSPECIFIED = 0

# Bucket floors, expressed as SeverityNumbers so every dialect above buckets identically.
SEVERITY_DEBUG_FLOOR = 5
SEVERITY_INFO_FLOOR = 9
SEVERITY_WARN_FLOOR = 13
SEVERITY_ERROR_FLOOR = 17

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


class LogAggregationError(ValueError):
    """Raised on invalid engine configuration or an unusable log batch.

    Subclasses ValueError so existing `except ValueError` callers keep working.
    """


def _normalize_key(key: str) -> str:
    """Lowercase a metadata key and strip separators so naming styles collapse."""
    return _NON_ALNUM_RE.sub("", key.lower())


# Snapshotted at import for the per-key hot path. Extend the blocklist through the
# engine's `extra_sensitive_key_substrings` argument -- mutating SENSITIVE_KEYS after
# import will not be picked up here.
_NORMALIZED_SENSITIVE_KEYS: Set[str] = {_normalize_key(k) for k in SENSITIVE_KEYS}


def _safe_str(value: Any) -> str:
    """`str()` that cannot itself abort the batch.

    A metadata object with a raising `__str__`/`__repr__` would otherwise propagate out
    of serialisation and discard every other log line in the flush.
    """
    try:
        return str(value)
    except Exception:  # noqa: BLE001 - deliberately broad: any failure must stay contained
        return f"[UNPRINTABLE: {type(value).__name__}]"


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _NORMALIZED_SENSITIVE_KEYS:
        return True
    return any(pattern in normalized for pattern in SENSITIVE_KEY_SUBSTRINGS)


@dataclass
class RawLogRecord:
    """One log event as emitted by a trading microservice, before sanitisation."""

    subsystem: str                      # e.g. 'ORDER_ROUTER', 'RISK_GATEWAY', 'MARKET_DATA'
    level: str                          # 'DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL', ...
    message: str                        # e.g. 'Order submitted to CME' -- never interpolate secrets
    correlation_id: str                 # Trace ID (e.g. 'trace-8849102-abc')
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp_epoch: float = field(default_factory=time.time)


@dataclass
class ObservabilityReport:
    """Outcome of one `process_and_aggregate_logs` batch."""

    total_logs_processed: int
    debug_logs_count: int
    info_logs_count: int
    error_logs_count: int
    redacted_keys_count: int
    formatted_json_payloads: List[str]
    has_error_spike_alert: bool
    status: str                         # 'LOGS_AGGREGATED_NORMAL', 'OBSERVABILITY_ERROR_SPIKE_ALERT'
    audit_notes: str
    # --- added in 1.1.0; all defaulted so existing construction stays valid ---
    trace_logs_count: int = 0
    warn_logs_count: int = 0            # split out of info_logs_count, which is now INFO-only
    unknown_level_count: int = 0        # level strings with no SeverityNumber mapping
    malformed_record_count: int = 0     # records with unusable metadata or timestamp
    sampled_out_count: int = 0          # TRACE/DEBUG records dropped by diagnostic sampling
    coerced_values_count: int = 0       # values stringified to keep the payload JSON-valid
    max_ingest_lag_seconds: float = 0.0  # max(observed - record timestamp) in this batch


class CentralizedLogAggregatorEngine:
    """
    Centralized logging and observability pipeline for distributed trading microservices:
    redacts credential-bearing metadata keys, emits one structured JSON line per record
    with an OpenTelemetry SeverityNumber, samples high-volume diagnostic levels, and
    flags error-velocity spikes within the submitted batch.

    The engine is stateless across calls: the same batch always produces the same report
    (given an injected `clock_fn`), and sampling never carries a counter between batches.
    """

    def __init__(
        self,
        error_spike_threshold_count: int = 10,
        diagnostic_sample_rate: int = 1,
        extra_sensitive_key_substrings: Optional[Iterable[str]] = None,
        clock_fn: Callable[[], float] = time.time,
    ) -> None:
        """
        Args:
            error_spike_threshold_count: alert when the batch holds this many records at
                ERROR severity or worse (SeverityNumber >= 17). The comparison is `>=`:
                a threshold of 10 fires on the 10th error, not the 11th. Must be >= 1 --
                a threshold of 0 would alert on every batch, including error-free ones.
            diagnostic_sample_rate: keep 1 of every N TRACE/DEBUG records (1 = keep all).
                Sampling is deterministic (first of each group) and applies only to
                SeverityNumber < 9; INFO, WARN, ERROR and FATAL are never sampled.
            extra_sensitive_key_substrings: site-specific credential field names to redact
                in addition to the built-in patterns (matched on the normalised key).
            clock_fn: source of the observed (ingest) timestamp. Inject in tests.
        """
        if isinstance(error_spike_threshold_count, bool) or not isinstance(error_spike_threshold_count, int):
            raise LogAggregationError("error_spike_threshold_count must be an int.")
        if error_spike_threshold_count < 1:
            raise LogAggregationError(
                f"error_spike_threshold_count must be >= 1, got {error_spike_threshold_count}. "
                "A threshold of 0 alerts on batches containing no errors at all."
            )
        if isinstance(diagnostic_sample_rate, bool) or not isinstance(diagnostic_sample_rate, int):
            raise LogAggregationError("diagnostic_sample_rate must be an int.")
        if diagnostic_sample_rate < 1:
            raise LogAggregationError(
                f"diagnostic_sample_rate must be >= 1 (1 keeps every record), "
                f"got {diagnostic_sample_rate}."
            )

        self.error_spike_threshold_count = error_spike_threshold_count
        self.diagnostic_sample_rate = diagnostic_sample_rate
        self.clock_fn = clock_fn
        self._extra_substrings: Set[str] = {
            _normalize_key(s) for s in (extra_sensitive_key_substrings or ()) if _normalize_key(s)
        }

    # ------------------------------------------------------------------ redaction

    def _key_is_sensitive(self, key: str) -> bool:
        if _is_sensitive_key(key):
            return True
        normalized = _normalize_key(key)
        return any(pattern in normalized for pattern in self._extra_substrings)

    def _sanitize(self, value: Any, depth: int, counters: Dict[str, int]) -> Any:
        """
        Recursively redact credential-bearing keys and coerce every leaf into something
        `json.dumps(..., allow_nan=False)` can serialise.

        Returns a JSON-safe copy; `counters` accumulates 'redacted' and 'coerced'.
        """
        if depth > MAX_REDACTION_DEPTH:
            logger.warning(
                "Metadata nesting exceeded MAX_REDACTION_DEPTH=%d; branch truncated. "
                "Deeply nested branches are not scanned for credentials.",
                MAX_REDACTION_DEPTH,
            )
            return TRUNCATION_PLACEHOLDER

        if isinstance(value, dict):
            sanitized: Dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                if isinstance(raw_key, str):
                    key = raw_key
                else:
                    key = _safe_str(raw_key)
                    counters["coerced"] += 1
                if self._key_is_sensitive(key):
                    sanitized[key] = REDACTION_PLACEHOLDER
                    counters["redacted"] += 1
                else:
                    sanitized[key] = self._sanitize(raw_value, depth + 1, counters)
            return sanitized

        if isinstance(value, (list, tuple)):
            # Credentials nested inside a list of dicts (HTTP header captures, retry
            # attempts, multi-leg order payloads) are a real leak path -- traverse them.
            return [self._sanitize(item, depth + 1, counters) for item in value]

        if value is None or isinstance(value, bool) or isinstance(value, (str, int)):
            return value

        if isinstance(value, float):
            if math.isfinite(value):
                return value
            # NaN / Infinity are not valid JSON. json.dumps emits them by default, which
            # produces a line a strict parser (Loki, OTel Collector) rejects.
            counters["coerced"] += 1
            return _safe_str(value)

        # Decimal, datetime, UUID, Enum, numpy scalars, dataclasses, arbitrary objects.
        # Trading metadata routinely carries Decimal prices; raising here would discard
        # the entire batch over one field.
        counters["coerced"] += 1
        return _safe_str(value)

    def redact_sensitive_metadata(self, metadata: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Redact credential-bearing keys from a log metadata mapping.

        Traverses nested dictionaries *and* lists/tuples, matches keys case- and
        separator-insensitively, and returns a JSON-safe copy plus the redaction count.
        Does not inspect message text or values -- see the module docstring.
        """
        if metadata is None:
            return {}, 0
        if not isinstance(metadata, dict):
            raise LogAggregationError(
                f"metadata must be a mapping, got {type(metadata).__name__}."
            )
        counters = {"redacted": 0, "coerced": 0}
        sanitized = self._sanitize(metadata, depth=0, counters=counters)
        return sanitized, counters["redacted"]

    # ------------------------------------------------------------------ formatting

    @staticmethod
    def _format_timestamp(epoch: float) -> str:
        """RFC 3339 UTC timestamp with microsecond precision.

        Whole-second formatting loses the ordering of events inside the same second,
        which is exactly the resolution a post-incident timeline needs.
        """
        dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        return f"{dt.strftime('%Y-%m-%dT%H:%M:%S')}.{dt.microsecond:06d}Z"

    @staticmethod
    def _severity_number(level_clean: str) -> int:
        return SEVERITY_NUMBERS.get(level_clean, SEVERITY_UNSPECIFIED)

    # ------------------------------------------------------------------ pipeline

    def process_and_aggregate_logs(self, records: List[RawLogRecord]) -> ObservabilityReport:
        """
        Sanitise records, emit one structured JSON line each, and audit error velocity.

        Malformed records are repaired and counted, never dropped and never allowed to
        abort the batch: a logging pipeline that raises on one bad field loses every other
        log in the flush, including the ones describing the incident.
        """
        if records is None or not isinstance(records, (list, tuple)):
            raise LogAggregationError("records must be a list of RawLogRecord.")
        if not records:
            raise LogAggregationError("Log record batch cannot be empty.")

        observed_epoch = float(self.clock_fn())
        observed_iso = self._format_timestamp(observed_epoch)

        json_payloads: List[str] = []
        counters = {"redacted": 0, "coerced": 0}
        trace_cnt = debug_cnt = info_cnt = warn_cnt = error_cnt = 0
        unknown_cnt = malformed_cnt = sampled_out_cnt = 0
        diagnostic_seen = 0
        # Deliberately unclamped: a negative maximum means every record in the batch is
        # timestamped in the future relative to this host, i.e. clock skew between the
        # emitting service and the aggregator. Clamping it at zero would hide that.
        max_lag: Optional[float] = None
        unknown_levels: Set[str] = set()

        for index, r in enumerate(records):
            level_clean = _safe_str(getattr(r, "level", "")).strip().upper()
            severity = self._severity_number(level_clean)

            if severity >= SEVERITY_ERROR_FLOOR:
                error_cnt += 1
            elif severity >= SEVERITY_WARN_FLOOR:
                warn_cnt += 1
            elif severity >= SEVERITY_INFO_FLOOR:
                info_cnt += 1
            elif severity >= SEVERITY_DEBUG_FLOOR:
                debug_cnt += 1
            elif severity > SEVERITY_UNSPECIFIED:
                trace_cnt += 1
            else:
                unknown_cnt += 1
                unknown_levels.add(level_clean)

            # Sample only the diagnostic levels, and never an unmapped level -- an
            # unrecognised string could be an error dialect this engine does not know.
            if SEVERITY_UNSPECIFIED < severity < SEVERITY_INFO_FLOOR and self.diagnostic_sample_rate > 1:
                keep = (diagnostic_seen % self.diagnostic_sample_rate) == 0
                diagnostic_seen += 1
                if not keep:
                    sampled_out_cnt += 1
                    continue

            metadata = getattr(r, "metadata", None)
            if metadata is None:
                metadata = {}
            elif not isinstance(metadata, dict):
                malformed_cnt += 1
                logger.warning(
                    "Record %d (%s/%s) carried non-mapping metadata of type %s; "
                    "coerced to a string field.",
                    index, getattr(r, "subsystem", "?"), level_clean, type(metadata).__name__,
                )
                metadata = {"_invalid_metadata": _safe_str(metadata)}

            sanitized_meta = self._sanitize(metadata, depth=0, counters=counters)

            raw_epoch = getattr(r, "timestamp_epoch", None)
            try:
                timestamp_epoch = float(raw_epoch)
                if not math.isfinite(timestamp_epoch):
                    raise ValueError("non-finite timestamp")
                timestamp_iso = self._format_timestamp(timestamp_epoch)
            except (TypeError, ValueError, OSError, OverflowError):
                malformed_cnt += 1
                logger.warning(
                    "Record %d (%s) carried an unusable timestamp_epoch (%r); "
                    "falling back to the observed ingest time.",
                    index, getattr(r, "subsystem", "?"), raw_epoch,
                )
                timestamp_epoch = observed_epoch
                timestamp_iso = observed_iso

            lag = observed_epoch - timestamp_epoch
            max_lag = lag if max_lag is None else max(max_lag, lag)

            structured_doc: Dict[str, Any] = {
                "timestamp_iso": timestamp_iso,
                "observed_timestamp_iso": observed_iso,
                "correlation_id": _safe_str(getattr(r, "correlation_id", "")),
                "subsystem": _safe_str(getattr(r, "subsystem", "")),
                "level": level_clean,
                "severity_number": severity,
                "message": _safe_str(getattr(r, "message", "")),
                "metadata": sanitized_meta,
            }
            if self.diagnostic_sample_rate > 1 and SEVERITY_UNSPECIFIED < severity < SEVERITY_INFO_FLOOR:
                # Downstream counts are wrong unless the consumer can rescale by the rate.
                structured_doc["sample_rate"] = self.diagnostic_sample_rate

            json_payloads.append(json.dumps(structured_doc, allow_nan=False))

        if unknown_levels:
            logger.warning(
                "%d record(s) carried unrecognised level string(s) %s; they were emitted "
                "with severity_number=0 and did NOT count toward the error-spike threshold.",
                unknown_cnt, sorted(unknown_levels),
            )

        has_error_alert = error_cnt >= self.error_spike_threshold_count

        summary = (
            f"Processed {len(records):,} records "
            f"(Trace = {trace_cnt}, Debug = {debug_cnt}, Info = {info_cnt}, "
            f"Warn = {warn_cnt}, Error+ = {error_cnt}, Unknown = {unknown_cnt}). "
            f"Redacted {counters['redacted']} sensitive keys, "
            f"sampled out {sampled_out_cnt}, coerced {counters['coerced']} values. "
            f"Max ingest lag {(max_lag or 0.0):.3f}s."
        )
        if has_error_alert:
            status = "OBSERVABILITY_ERROR_SPIKE_ALERT"
            notes = (
                f"OBSERVABILITY ALERT [{error_cnt} Error Logs]: error count in batch "
                f"({error_cnt}) reached the threshold ({self.error_spike_threshold_count}). "
                + summary
            )
            logger.critical(notes)
        else:
            status = "LOGS_AGGREGATED_NORMAL"
            notes = "LOGS AGGREGATED NORMAL: " + summary
            logger.info(notes)

        return ObservabilityReport(
            total_logs_processed=len(records),
            debug_logs_count=debug_cnt,
            info_logs_count=info_cnt,
            error_logs_count=error_cnt,
            redacted_keys_count=counters["redacted"],
            formatted_json_payloads=json_payloads,
            has_error_spike_alert=has_error_alert,
            status=status,
            audit_notes=notes,
            trace_logs_count=trace_cnt,
            warn_logs_count=warn_cnt,
            unknown_level_count=unknown_cnt,
            malformed_record_count=malformed_cnt,
            sampled_out_count=sampled_out_cnt,
            coerced_values_count=counters["coerced"],
            max_ingest_lag_seconds=(max_lag if max_lag is not None else 0.0),
        )
