"""
Unit tests for log-aggregation-and-centralized-observability.

Covers:
1. Credential redaction: exact keys, prefixed/suffixed variants, case and separator
   insensitivity, nested dicts, dicts nested inside lists, and the false-positive
   boundary (`token_symbol` must survive).
2. JSON validity: Decimal/datetime/NaN/Infinity metadata must still produce a line a
   strict parser accepts, instead of raising and discarding the batch.
3. Severity normalisation: Python/log4j/syslog dialects mapped to OTel SeverityNumbers,
   FATAL and WARNING counted correctly, unknown levels surfaced not silently dropped.
4. Error-spike detection at the exact threshold boundary, and config validation.
5. Deterministic diagnostic sampling, and the guarantee that INFO+ is never sampled.
6. Timestamp precision, observed-timestamp/ingest-lag, and bad-timestamp fallback.
7. Batch-level input validation and malformed-record repair.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import unittest

from centralized_log_aggregator import (
    MAX_REDACTION_DEPTH,
    REDACTION_PLACEHOLDER,
    TRUNCATION_PLACEHOLDER,
    CentralizedLogAggregatorEngine,
    LogAggregationError,
    ObservabilityReport,
    RawLogRecord,
)

# 1700000000.0 epoch seconds == 2023-11-14T22:13:20Z (UTC), independent of this module.
FIXED_EPOCH = 1700000000.0
INGEST_EPOCH = FIXED_EPOCH + 30.0


def _record(level="INFO", metadata=None, epoch=FIXED_EPOCH, **kwargs):
    return RawLogRecord(
        subsystem=kwargs.pop("subsystem", "ORDER_ROUTER"),
        level=level,
        message=kwargs.pop("message", "Order submitted to CME"),
        correlation_id=kwargs.pop("correlation_id", "trace-101"),
        metadata={} if metadata is None else metadata,
        timestamp_epoch=epoch,
    )


class TestCentralizedLogAggregatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CentralizedLogAggregatorEngine(
            error_spike_threshold_count=5, clock_fn=lambda: INGEST_EPOCH
        )
        # Keep the module's own CRITICAL/WARNING emissions out of the test output.
        logging.getLogger("centralized_log_aggregator").addHandler(logging.NullHandler())
        logging.getLogger("centralized_log_aggregator").propagate = False

    # ------------------------------------------------------------------ redaction

    def test_redacts_exact_and_affixed_credential_keys(self):
        """Regression: an exact-match blocklist leaks `broker_api_key` / `access_token`."""
        metadata = {
            "api_key": "K0",
            "broker_api_key": "K1",
            "access_token": "T1",
            "Authorization": "Bearer X",
            "X-Api-Key": "Z",
            "wallet": {"private_key": "0xdead"},
            "order_id": "ORD-999",
        }
        sanitized, count = self.engine.redact_sensitive_metadata(metadata)

        self.assertEqual(count, 6)
        for key in ("api_key", "broker_api_key", "access_token", "Authorization", "X-Api-Key"):
            self.assertEqual(sanitized[key], REDACTION_PLACEHOLDER, f"{key} leaked")
        self.assertEqual(sanitized["wallet"]["private_key"], REDACTION_PLACEHOLDER)
        self.assertEqual(sanitized["order_id"], "ORD-999")

    def test_redacts_credentials_nested_inside_lists(self):
        """Regression: dict-only recursion leaks secrets held in a list of headers."""
        metadata = {"attempts": [{"authorization": "Bearer Y", "status": 401}, {"status": 200}]}
        sanitized, count = self.engine.redact_sensitive_metadata(metadata)

        self.assertEqual(count, 1)
        self.assertEqual(sanitized["attempts"][0]["authorization"], REDACTION_PLACEHOLDER)
        self.assertEqual(sanitized["attempts"][0]["status"], 401)
        self.assertEqual(sanitized["attempts"][1], {"status": 200})

    def test_non_credential_token_fields_are_preserved(self):
        """A bare `token` substring rule would destroy DEX metadata; it must not fire."""
        metadata = {"token_symbol": "USDC", "token_address": "0xabc", "author": "desk-a"}
        sanitized, count = self.engine.redact_sensitive_metadata(metadata)

        self.assertEqual(count, 0)
        self.assertEqual(sanitized, metadata)

    def test_extra_sensitive_key_substrings_are_honoured(self):
        engine = CentralizedLogAggregatorEngine(extra_sensitive_key_substrings=["vendorpin"])
        sanitized, count = engine.redact_sensitive_metadata({"vendor_pin": "4242", "qty": 5})

        self.assertEqual(count, 1)
        self.assertEqual(sanitized["vendor_pin"], REDACTION_PLACEHOLDER)
        self.assertEqual(sanitized["qty"], 5)

    def test_self_referential_metadata_is_truncated_not_fatal(self):
        """A cyclic structure must bound out, not raise RecursionError and lose the batch."""
        cyclic = {"depth": 0}
        cyclic["self"] = cyclic

        report = self.engine.process_and_aggregate_logs([_record(metadata=cyclic)])
        payload = json.loads(report.formatted_json_payloads[0])

        # The root mapping sits at depth 0, so mappings survive down to depth
        # MAX_REDACTION_DEPTH and the level below it is replaced by the truncation marker.
        node = payload["metadata"]
        for _ in range(MAX_REDACTION_DEPTH + 1):
            self.assertIsInstance(node, dict)
            node = node["self"]
        self.assertEqual(node, TRUNCATION_PLACEHOLDER)

    def test_redact_sensitive_metadata_rejects_non_mapping(self):
        with self.assertRaises(LogAggregationError):
            self.engine.redact_sensitive_metadata(["api_key", "leak"])

    # ------------------------------------------------------------- JSON validity

    def test_non_json_native_metadata_does_not_abort_the_batch(self):
        """Regression: a Decimal price used to raise TypeError and discard every log."""
        metadata = {
            "limit_price": Decimal("101.25"),
            "submitted_at": datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc),
            "fill_ratio": float("nan"),
            "spread": float("inf"),
            7: "int-key",
        }
        report = self.engine.process_and_aggregate_logs([_record(metadata=metadata)])

        payload = json.loads(report.formatted_json_payloads[0])  # must be strict-JSON parseable
        self.assertNotIn("NaN", report.formatted_json_payloads[0])
        self.assertEqual(payload["metadata"]["limit_price"], "101.25")
        self.assertEqual(payload["metadata"]["7"], "int-key")
        self.assertEqual(report.coerced_values_count, 5)
        self.assertEqual(report.total_logs_processed, 1)

    def test_object_with_a_raising_str_does_not_abort_the_batch(self):
        class Unprintable:
            def __str__(self):
                raise RuntimeError("boom")
            __repr__ = __str__

        report = self.engine.process_and_aggregate_logs(
            [_record(metadata={"broken": Unprintable()}), _record(metadata={"ok": 1})]
        )
        payloads = [json.loads(p) for p in report.formatted_json_payloads]

        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["metadata"]["broken"], "[UNPRINTABLE: Unprintable]")
        self.assertEqual(payloads[1]["metadata"], {"ok": 1})

    def test_payload_carries_correlation_id_and_severity_number(self):
        report = self.engine.process_and_aggregate_logs(
            [_record(metadata={"order_id": "ORD-999", "api_key": "SUPER_SECRET_KEY_123"})]
        )
        payload = json.loads(report.formatted_json_payloads[0])

        self.assertEqual(report.status, "LOGS_AGGREGATED_NORMAL")
        self.assertEqual(report.redacted_keys_count, 1)
        self.assertEqual(payload["metadata"]["api_key"], REDACTION_PLACEHOLDER)
        self.assertEqual(payload["correlation_id"], "trace-101")
        self.assertEqual(payload["severity_number"], 9)
        self.assertEqual(payload["subsystem"], "ORDER_ROUTER")

    # ------------------------------------------------------- severity normalisation

    def test_severity_dialects_map_to_opentelemetry_numbers(self):
        levels = {
            "TRACE": 1, "DEBUG": 5, "info": 9, "NOTICE": 10, "WARNING": 13, "WARN": 13,
            "ERROR": 17, "err": 17, "ALERT": 19, "CRITICAL": 21, "FATAL": 21, " panic ": 21,
        }
        for level, expected in levels.items():
            with self.subTest(level=level):
                report = self.engine.process_and_aggregate_logs([_record(level=level)])
                payload = json.loads(report.formatted_json_payloads[0])
                self.assertEqual(payload["severity_number"], expected)

    def test_fatal_and_warning_dialects_are_counted_not_dropped(self):
        """Regression: FATAL/WARNING used to hit no counter, so a spike went unalerted."""
        records = [_record(level="WARNING") for _ in range(3)]
        records += [_record(level="FATAL") for _ in range(6)]

        report = self.engine.process_and_aggregate_logs(records)

        self.assertEqual(report.warn_logs_count, 3)
        self.assertEqual(report.error_logs_count, 6)
        self.assertEqual(report.info_logs_count, 0)
        self.assertTrue(report.has_error_spike_alert)
        self.assertEqual(report.status, "OBSERVABILITY_ERROR_SPIKE_ALERT")

    def test_info_and_warn_are_counted_separately(self):
        report = self.engine.process_and_aggregate_logs(
            [_record(level="INFO"), _record(level="INFO"), _record(level="WARN")]
        )
        self.assertEqual(report.info_logs_count, 2)
        self.assertEqual(report.warn_logs_count, 1)

    def test_unknown_level_is_surfaced_and_never_sampled(self):
        engine = CentralizedLogAggregatorEngine(diagnostic_sample_rate=2, clock_fn=lambda: INGEST_EPOCH)
        report = engine.process_and_aggregate_logs(
            [_record(level="LOUD") for _ in range(4)]
        )
        payload = json.loads(report.formatted_json_payloads[0])

        self.assertEqual(report.unknown_level_count, 4)
        self.assertEqual(report.error_logs_count, 0)
        self.assertEqual(report.sampled_out_count, 0)
        self.assertEqual(len(report.formatted_json_payloads), 4)
        self.assertEqual(payload["severity_number"], 0)

    # -------------------------------------------------------------- spike detection

    def test_alert_fires_exactly_at_threshold_not_one_late(self):
        below = [_record(level="ERROR") for _ in range(4)]
        at = [_record(level="ERROR") for _ in range(5)]

        self.assertFalse(self.engine.process_and_aggregate_logs(below).has_error_spike_alert)
        self.assertEqual(
            self.engine.process_and_aggregate_logs(below).status, "LOGS_AGGREGATED_NORMAL"
        )
        self.assertTrue(self.engine.process_and_aggregate_logs(at).has_error_spike_alert)
        self.assertEqual(
            self.engine.process_and_aggregate_logs(at).status, "OBSERVABILITY_ERROR_SPIKE_ALERT"
        )

    def test_rejects_thresholds_that_would_alert_on_clean_batches(self):
        for bad in (0, -1, True, 1.5):
            with self.subTest(threshold=bad):
                with self.assertRaises(LogAggregationError):
                    CentralizedLogAggregatorEngine(error_spike_threshold_count=bad)

    def test_rejects_invalid_sample_rate(self):
        for bad in (0, -3, 2.5):
            with self.subTest(rate=bad):
                with self.assertRaises(LogAggregationError):
                    CentralizedLogAggregatorEngine(diagnostic_sample_rate=bad)

    # ------------------------------------------------------------------- sampling

    def test_diagnostic_sampling_is_deterministic_and_spares_higher_levels(self):
        engine = CentralizedLogAggregatorEngine(
            error_spike_threshold_count=5, diagnostic_sample_rate=10, clock_fn=lambda: INGEST_EPOCH
        )
        records = [_record(level="DEBUG") for _ in range(100)]
        records += [_record(level="INFO") for _ in range(3)]
        records += [_record(level="ERROR") for _ in range(2)]

        report = engine.process_and_aggregate_logs(records)
        emitted_levels = [json.loads(p)["level"] for p in report.formatted_json_payloads]

        # 1 kept per 10 DEBUG records => 10 kept, 90 dropped; INFO/ERROR untouched.
        self.assertEqual(report.sampled_out_count, 90)
        self.assertEqual(emitted_levels.count("DEBUG"), 10)
        self.assertEqual(emitted_levels.count("INFO"), 3)
        self.assertEqual(emitted_levels.count("ERROR"), 2)
        # Counters report what was *observed*, not what survived sampling.
        self.assertEqual(report.debug_logs_count, 100)
        self.assertEqual(report.total_logs_processed, 105)
        self.assertEqual(json.loads(report.formatted_json_payloads[0])["sample_rate"], 10)

    def test_default_engine_does_not_sample(self):
        report = self.engine.process_and_aggregate_logs([_record(level="DEBUG") for _ in range(20)])

        self.assertEqual(report.sampled_out_count, 0)
        self.assertEqual(len(report.formatted_json_payloads), 20)
        self.assertNotIn("sample_rate", json.loads(report.formatted_json_payloads[0]))

    # ----------------------------------------------------------------- timestamps

    def test_sub_second_precision_is_preserved(self):
        """Whole-second formatting made events inside one second unorderable."""
        first = _record(epoch=FIXED_EPOCH + 0.123456)
        second = _record(epoch=FIXED_EPOCH + 0.923456)

        report = self.engine.process_and_aggregate_logs([first, second])
        stamps = [json.loads(p)["timestamp_iso"] for p in report.formatted_json_payloads]

        self.assertEqual(stamps[0], "2023-11-14T22:13:20.123456Z")
        self.assertEqual(stamps[1], "2023-11-14T22:13:20.923456Z")
        self.assertLess(stamps[0], stamps[1])

    def test_observed_timestamp_and_ingest_lag_are_reported(self):
        report = self.engine.process_and_aggregate_logs([_record(epoch=FIXED_EPOCH)])
        payload = json.loads(report.formatted_json_payloads[0])

        self.assertEqual(payload["observed_timestamp_iso"], "2023-11-14T22:13:50.000000Z")
        self.assertAlmostEqual(report.max_ingest_lag_seconds, 30.0, places=6)

    def test_future_dated_records_report_negative_lag(self):
        """Clock skew ahead of the aggregator must stay visible, not be clamped to zero."""
        report = self.engine.process_and_aggregate_logs([_record(epoch=INGEST_EPOCH + 45.0)])

        self.assertAlmostEqual(report.max_ingest_lag_seconds, -45.0, places=6)

    def test_unusable_timestamp_falls_back_to_ingest_time(self):
        report = self.engine.process_and_aggregate_logs(
            [_record(epoch=float("nan")), _record(epoch=None)]
        )
        stamps = [json.loads(p)["timestamp_iso"] for p in report.formatted_json_payloads]

        self.assertEqual(report.malformed_record_count, 2)
        self.assertEqual(stamps, ["2023-11-14T22:13:50.000000Z"] * 2)

    # ------------------------------------------------------------ batch validation

    def test_empty_or_non_list_batch_is_rejected(self):
        for bad in ([], None, "not-a-batch"):
            with self.subTest(batch=bad):
                with self.assertRaises(LogAggregationError):
                    self.engine.process_and_aggregate_logs(bad)
        # LogAggregationError must stay catchable as ValueError for existing callers.
        with self.assertRaises(ValueError):
            self.engine.process_and_aggregate_logs([])

    def test_malformed_metadata_is_repaired_not_dropped(self):
        records = [_record(metadata="oops"), _record(metadata=None), _record(metadata={"ok": 1})]
        report = self.engine.process_and_aggregate_logs(records)
        payloads = [json.loads(p) for p in report.formatted_json_payloads]

        self.assertEqual(len(payloads), 3)
        self.assertEqual(report.malformed_record_count, 1)
        self.assertEqual(payloads[0]["metadata"], {"_invalid_metadata": "oops"})
        self.assertEqual(payloads[1]["metadata"], {})
        self.assertEqual(payloads[2]["metadata"], {"ok": 1})

    def test_report_is_an_observability_report(self):
        report = self.engine.process_and_aggregate_logs([_record()])
        self.assertIsInstance(report, ObservabilityReport)
        self.assertIn("LOGS AGGREGATED NORMAL", report.audit_notes)


if __name__ == "__main__":
    unittest.main()
