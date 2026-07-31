import json
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

SENSITIVE_KEYS: Set[str] = {
    "api_key", "secret", "api_secret", "private_key", "password", "auth_header", "token"
}

@dataclass
class RawLogRecord:
    subsystem: str                      # e.g. 'ORDER_ROUTER', 'RISK_GATEWAY', 'MARKET_DATA'
    level: str                          # 'DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'
    message: str                        # e.g. 'Order submitted to CME'
    correlation_id: str                 # Trace ID (e.g. 'trace-8849102-abc')
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp_epoch: float = field(default_factory=time.time)

@dataclass
class ObservabilityReport:
    total_logs_processed: int
    debug_logs_count: int
    info_logs_count: int
    error_logs_count: int
    redacted_keys_count: int
    formatted_json_payloads: List[str]
    has_error_spike_alert: bool
    status: str                         # 'LOGS_AGGREGATED_NORMAL', 'OBSERVABILITY_ERROR_SPIKE_ALERT'
    audit_notes: str

class CentralizedLogAggregatorEngine:
    """
    Centralized logging and observability pipeline for distributed trading microservices,
    formatting structured JSON logs, redacting sensitive API keys/secrets, and monitoring error rate spikes for Grafana Loki / OpenTelemetry.
    """
    def __init__(self, error_spike_threshold_count: int = 10):
        self.error_spike_threshold_count = error_spike_threshold_count

    def redact_sensitive_metadata(self, metadata: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
        """
        Recursively redacts sensitive API keys, secrets, and private keys from log metadata dictionary.
        """
        sanitized = {}
        redacted_count = 0

        for k, v in metadata.items():
            key_lower = k.lower()
            if key_lower in SENSITIVE_KEYS or any(sk in key_lower for sk in ["secret", "private_key", "password"]):
                sanitized[k] = "[REDACTED]"
                redacted_count += 1
            elif isinstance(v, dict):
                sub_dict, sub_count = self.redact_sensitive_metadata(v)
                sanitized[k] = sub_dict
                redacted_count += sub_count
            else:
                sanitized[k] = v

        return sanitized, redacted_count

    def process_and_aggregate_logs(
        self,
        records: List[RawLogRecord]
    ) -> ObservabilityReport:
        """
        Sanitizes records, formats structured OpenTelemetry-compliant JSON payloads, and monitors error velocity.
        """
        if not records:
            raise ValueError("Log record batch cannot be empty.")

        json_payloads: List[str] = []
        tot_redacted = 0
        debug_cnt = 0
        info_cnt = 0
        error_cnt = 0

        for r in records:
            level_clean = r.level.strip().upper()
            if level_clean == "DEBUG":
                debug_cnt += 1
            elif level_clean in ("INFO", "WARN"):
                info_cnt += 1
            elif level_clean in ("ERROR", "CRITICAL"):
                error_cnt += 1

            # 1. Redact Sensitive Metadata
            sanitized_meta, red_cnt = self.redact_sensitive_metadata(r.metadata)
            tot_redacted += red_cnt

            # 2. Format Structured JSON Payload (OpenTelemetry / Loki format)
            structured_doc = {
                "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r.timestamp_epoch)),
                "correlation_id": r.correlation_id,
                "subsystem": r.subsystem,
                "level": level_clean,
                "message": r.message,
                "metadata": sanitized_meta
            }
            json_payloads.append(json.dumps(structured_doc))

        # 3. Audit Error Rate Spikes
        has_error_alert = (error_cnt >= self.error_spike_threshold_count)

        if has_error_alert:
            status = "OBSERVABILITY_ERROR_SPIKE_ALERT"
            notes = (
                f"OBSERVABILITY ALERT [{error_cnt} Error Logs]: Error count in batch ({error_cnt}) "
                f"exceeds threshold ({self.error_spike_threshold_count})! Redacted {tot_redacted} sensitive keys."
            )
            logger.critical(notes)
        else:
            status = "LOGS_AGGREGATED_NORMAL"
            notes = (
                f"LOGS AGGREGATED NORMAL: Processed {len(records):,} records "
                f"(Debug = {debug_cnt}, Info = {info_cnt}, Error = {error_cnt}). Redacted {tot_redacted} sensitive keys."
            )
            logger.info(notes)

        return ObservabilityReport(
            total_logs_processed=len(records),
            debug_logs_count=debug_cnt,
            info_logs_count=info_cnt,
            error_logs_count=error_cnt,
            redacted_keys_count=tot_redacted,
            formatted_json_payloads=json_payloads,
            has_error_spike_alert=has_error_alert,
            status=status,
            audit_notes=notes
        )
