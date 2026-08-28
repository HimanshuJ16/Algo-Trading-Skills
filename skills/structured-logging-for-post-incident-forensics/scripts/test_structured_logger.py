"""Unit tests for structured-logging-for-post-incident-forensics.

Every expectation here is derived independently of the implementation: UTC instants
computed by hand, OpenTelemetry SeverityNumber values taken from the specification, and
JSON validity checked with a parser configured to reject the non-standard ``NaN`` token
that a permissive parser would quietly accept.

The classes named ``*Regression`` pin behaviour that v1.0.0 of this module got wrong.
Each of those tests raises an exception or produces an out-of-order record against the
old implementation and passes against the current one.
"""

import json
import logging
import sys
import threading
import unittest

from structured_logger import (
    DEFAULT_BUFFER_CAPACITY,
    DEFAULT_REDACT_KEYS,
    DEFAULT_SINK_LOGGER_NAME,
    MAX_METADATA_DEPTH,
    MAX_REPR_LENGTH,
    REDACTED_PLACEHOLDER,
    SCHEMA_VERSION,
    SEVERITY_OTEL_NUMBER,
    SEVERITY_PYTHON_LEVEL,
    EventType,
    ForensicLogger,
    Severity,
    StructuredLogEvent,
    new_correlation_id,
    sanitize_metadata,
)


def _strict_loads(payload):
    """Parse JSON, rejecting the non-standard NaN/Infinity tokens Python emits.

    ``json.loads`` accepts them by default; a strict downstream consumer (Elasticsearch,
    most JSON libraries in other languages) rejects the entire line. Testing with the
    permissive default would let a poisoned record pass.
    """
    def _reject(token):
        raise AssertionError(f"record contains non-standard JSON token {token!r}")

    return json.loads(payload, parse_constant=_reject)


class _CapturingHandler(logging.Handler):
    """Collects the formatted message of every record routed to a sink."""

    def __init__(self):
        super().__init__()
        self.messages = []
        self.levels = []

    def emit(self, record):
        self.messages.append(record.getMessage())
        self.levels.append(record.levelno)


class _ExplodingHandler(logging.Handler):
    """A sink that fails on every write, standing in for a full disk or wedged socket."""

    def emit(self, record):
        raise OSError("sink unavailable")


def make_sink(name, handler=None):
    """Return an isolated sink logger with a capturing handler attached."""
    sink = logging.getLogger(name)
    sink.handlers = []
    sink.propagate = False
    sink.setLevel(logging.DEBUG)
    handler = handler or _CapturingHandler()
    sink.addHandler(handler)
    return sink, handler


class TestRecordSchema(unittest.TestCase):
    """The wire format, checked field by field against hand-derived values."""

    def test_iso_timestamp_matches_hand_computed_utc_instant(self):
        # 1_700_000_000 seconds after the Unix epoch is 2023-11-14T22:13:20Z (computed
        # from the epoch, not from the implementation). The nanosecond remainder must
        # survive intact: this is the digit range a float epoch-seconds field loses.
        event = StructuredLogEvent(
            sequence_number=1, instance_id="i-1",
            timestamp_ns=1_700_000_000_123_456_789, monotonic_ns=0,
            event_type="ORDER_PLACED", correlation_id="c", component="bot",
            message="m", severity="INFO", severity_number=9,
        )
        self.assertEqual(event.timestamp_iso, "2023-11-14T22:13:20.123456789Z")

    def test_iso_timestamp_pads_sub_second_zeros(self):
        # 1e9 seconds after the epoch is 2001-09-09T01:46:40Z exactly.
        event = StructuredLogEvent(
            sequence_number=1, instance_id="i-1",
            timestamp_ns=1_000_000_000_000_000_000, monotonic_ns=0,
            event_type="X", correlation_id="c", component="bot",
            message="m", severity="INFO", severity_number=9,
        )
        self.assertEqual(event.timestamp_iso, "2001-09-09T01:46:40.000000000Z")

    def test_every_required_field_is_present_on_the_wire(self):
        flogger = ForensicLogger(component="test-bot", sink=make_sink("t.schema")[0])
        event = flogger.emit(
            EventType.ORDER_PLACED, "Buy 100 AAPL @ market",
            metadata={"symbol": "AAPL", "qty": 100},
        )
        parsed = _strict_loads(event.to_json())
        self.assertEqual(
            set(parsed),
            {"schema_version", "seq", "instance_id", "ts_ns", "ts_iso", "mono_ns",
             "event_type", "correlation_id", "component", "severity",
             "severity_number", "message", "metadata"},
        )
        self.assertEqual(parsed["schema_version"], SCHEMA_VERSION)
        self.assertEqual(parsed["event_type"], "ORDER_PLACED")
        self.assertEqual(parsed["component"], "test-bot")
        self.assertEqual(parsed["seq"], 1)
        self.assertEqual(parsed["metadata"]["symbol"], "AAPL")
        self.assertIsInstance(parsed["ts_ns"], int)
        self.assertIsInstance(parsed["mono_ns"], int)

    def test_severity_numbers_match_the_opentelemetry_specification(self):
        # OpenTelemetry Logs Data Model SeverityNumber ranges: DEBUG 5-8, INFO 9-12,
        # WARN 13-16, ERROR 17-20, FATAL 21-24. The base of each range is used.
        self.assertEqual(SEVERITY_OTEL_NUMBER[Severity.DEBUG], 5)
        self.assertEqual(SEVERITY_OTEL_NUMBER[Severity.INFO], 9)
        self.assertEqual(SEVERITY_OTEL_NUMBER[Severity.WARNING], 13)
        self.assertEqual(SEVERITY_OTEL_NUMBER[Severity.ERROR], 17)
        self.assertEqual(SEVERITY_OTEL_NUMBER[Severity.CRITICAL], 21)

    def test_record_is_immutable(self):
        flogger = ForensicLogger(sink=make_sink("t.frozen")[0])
        event = flogger.emit(EventType.FILL_RECEIVED, "Filled 100 @ 150.00")
        with self.assertRaises(Exception):
            event.message = "Filled 0 @ 0.00"


class TestSeverityHandlingRegression(unittest.TestCase):
    """v1.0.0 resolved the level with ``getattr(logging, severity, logging.INFO)``."""

    def test_lowercase_severity_does_not_raise(self):
        # v1.0.0: getattr(logging, "warning") returned the *function*, and Logger.log
        # raised TypeError("level must be an integer") from inside the emit path --
        # taking down whichever except-block was trying to record the incident.
        sink, handler = make_sink("t.sev.lower")
        flogger = ForensicLogger(sink=sink)
        event = flogger.emit(EventType.SYSTEM_ERROR, "broker timeout", severity="warning")
        self.assertEqual(event.severity, "WARNING")
        self.assertEqual(handler.levels, [logging.WARNING])

    def test_arbitrary_logging_module_attribute_is_not_used_as_a_level(self):
        # v1.0.0: getattr(logging, "raiseExceptions") is True, and True == 1, so the
        # record was emitted at level 1 -- below DEBUG, invisible to every handler.
        sink, handler = make_sink("t.sev.attr")
        flogger = ForensicLogger(sink=sink)
        event = flogger.emit(EventType.RISK_BREACH, "limit", severity="raiseExceptions")
        self.assertEqual(event.severity, "ERROR")
        self.assertEqual(event.metadata["_invalid_severity"], "raiseExceptions")
        self.assertEqual(handler.levels, [logging.ERROR])

    def test_unknown_severity_is_recorded_not_dropped(self):
        flogger = ForensicLogger(sink=make_sink("t.sev.unknown")[0])
        event = flogger.emit(EventType.SYSTEM_ERROR, "x", severity="NOTICE")
        self.assertEqual(event.severity, "ERROR")
        self.assertEqual(event.metadata["_invalid_severity"], "NOTICE")

    def test_aliases_and_enum_are_accepted(self):
        flogger = ForensicLogger(sink=make_sink("t.sev.alias")[0])
        self.assertEqual(flogger.emit(EventType.SYSTEM_ERROR, "a", severity="WARN").severity, "WARNING")
        self.assertEqual(flogger.emit(EventType.SYSTEM_ERROR, "b", severity="FATAL").severity, "CRITICAL")
        self.assertEqual(
            flogger.emit(EventType.SYSTEM_ERROR, "c", severity=Severity.CRITICAL).severity,
            "CRITICAL",
        )
        for event in flogger._snapshot():
            self.assertNotIn("_invalid_severity", event.metadata)

    def test_python_level_table_covers_every_severity(self):
        self.assertEqual(set(SEVERITY_PYTHON_LEVEL), set(Severity))
        self.assertEqual(set(SEVERITY_OTEL_NUMBER), set(Severity))


class TestMetadataSanitizationRegression(unittest.TestCase):
    """v1.0.0 serialised with ``json.dumps(..., default=str)``, which is not enough."""

    def test_non_string_dict_key_does_not_raise(self):
        # v1.0.0: TypeError("keys must be str, int, float, bool or None, not tuple").
        # ``default=`` covers values only, never keys.
        flogger = ForensicLogger(sink=make_sink("t.meta.key")[0])
        event = flogger.emit(EventType.POSITION_UPDATE, "m", metadata={(1, 2): "leg"})
        self.assertEqual(_strict_loads(event.to_json())["metadata"]["(1, 2)"], "leg")

    def test_circular_reference_does_not_raise(self):
        # v1.0.0: ValueError("Circular reference detected"). A position object holding a
        # back-reference to its portfolio is enough to trigger it.
        portfolio = {"name": "book-a"}
        portfolio["self"] = portfolio
        flogger = ForensicLogger(sink=make_sink("t.meta.circ")[0])
        event = flogger.emit(EventType.POSITION_UPDATE, "m", metadata={"p": portfolio})
        self.assertEqual(event.metadata["p"]["self"], "<circular-reference>")
        _strict_loads(event.to_json())

    def test_non_finite_floats_never_reach_the_wire(self):
        # v1.0.0 emitted bare NaN / Infinity, which are not valid JSON: a strict parser
        # rejects the whole line, so one unpriceable Greek destroyed the record.
        flogger = ForensicLogger(sink=make_sink("t.meta.nan")[0])
        event = flogger.emit(
            EventType.POSITION_UPDATE, "greeks",
            metadata={"delta": float("nan"), "vega": float("inf"), "gamma": 0.5},
        )
        payload = event.to_json()
        self.assertNotIn("NaN", payload)
        self.assertNotIn("Infinity", payload)
        parsed = _strict_loads(payload)
        self.assertEqual(parsed["metadata"]["gamma"], 0.5)
        self.assertIsInstance(parsed["metadata"]["delta"], str)

    def test_unserialisable_object_is_reduced_to_a_bounded_repr(self):
        flogger = ForensicLogger(sink=make_sink("t.meta.obj")[0])
        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={"conn": object()})
        self.assertIsInstance(event.metadata["conn"], str)
        self.assertLessEqual(len(event.metadata["conn"]), MAX_REPR_LENGTH + 32)

    def test_oversized_repr_is_truncated(self):
        class Big:
            def __repr__(self):
                return "x" * (MAX_REPR_LENGTH * 3)

        flogger = ForensicLogger(sink=make_sink("t.meta.big")[0])
        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={"b": Big()})
        self.assertTrue(event.metadata["b"].endswith("<truncated>"))
        self.assertLess(len(event.metadata["b"]), MAX_REPR_LENGTH * 2)

    def test_deep_nesting_is_bounded(self):
        node = {"leaf": 1}
        for _ in range(MAX_METADATA_DEPTH + 5):
            node = {"child": node}
        result = sanitize_metadata(node)
        flattened = json.dumps(result)
        self.assertIn("<max-depth-exceeded>", flattened)

    def test_secret_keys_are_redacted_including_nested(self):
        flogger = ForensicLogger(sink=make_sink("t.meta.secret")[0])
        event = flogger.emit(
            EventType.CONNECTIVITY_RESTORED, "reauth",
            metadata={"api_key": "sk_live_x", "auth": {"Authorization": "Bearer y"},
                      "legs": [{"private_key": "z"}]},
        )
        self.assertEqual(event.metadata["api_key"], REDACTED_PLACEHOLDER)
        self.assertEqual(event.metadata["auth"]["Authorization"], REDACTED_PLACEHOLDER)
        self.assertEqual(event.metadata["legs"][0]["private_key"], REDACTED_PLACEHOLDER)
        self.assertNotIn("sk_live_x", event.to_json())

    def test_redaction_matches_the_whole_key_not_a_substring(self):
        # "token_bucket_size" is a rate-limiter setting, not a credential. Substring
        # matching would redact it and hide the cause of a throttling incident.
        flogger = ForensicLogger(sink=make_sink("t.meta.substr")[0])
        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={"token_bucket_size": 40})
        self.assertEqual(event.metadata["token_bucket_size"], 40)

    def test_redaction_set_is_configurable(self):
        flogger = ForensicLogger(
            sink=make_sink("t.meta.custom")[0], redact_keys={"client_code"},
        )
        event = flogger.emit(EventType.ORDER_PLACED, "m",
                             metadata={"client_code": "ABC123", "api_key": "still-visible"})
        self.assertEqual(event.metadata["client_code"], REDACTED_PLACEHOLDER)
        self.assertEqual(event.metadata["api_key"], "still-visible")

    def test_metadata_is_snapshotted_at_emit_time(self):
        # v1.0.0 stored the caller's dict by reference, so a later mutation silently
        # rewrote history: the recorded event reported a quantity that was never sent.
        flogger = ForensicLogger(sink=make_sink("t.meta.snap")[0])
        payload = {"qty": 100}
        event = flogger.emit(EventType.ORDER_PLACED, "Buy 100", metadata=payload)
        payload["qty"] = 0
        self.assertEqual(event.metadata["qty"], 100)
        self.assertEqual(flogger.reconstruct_timeline(event.correlation_id)[0]["metadata"]["qty"], 100)

    def test_default_redact_keys_are_lowercase(self):
        self.assertTrue(all(k == k.lower() for k in DEFAULT_REDACT_KEYS))


class TestCorrelationIdEntropyRegression(unittest.TestCase):
    """v1.0.0 used ``str(uuid.uuid4())[:12]`` -- 11 hex digits, roughly 44 bits."""

    def test_correlation_id_is_32_lowercase_hex_characters(self):
        # The W3C Trace Context trace-id shape: 16 random bytes as 32 lowercase hex
        # characters. A truncated ID collides silently and merges two unrelated order
        # lifecycles into one timeline that looks complete and is wrong.
        cid = new_correlation_id()
        self.assertEqual(len(cid), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in cid))
        self.assertNotEqual(cid, "0" * 32)

    def test_generated_ids_do_not_repeat_over_a_large_draw(self):
        ids = {new_correlation_id() for _ in range(50_000)}
        self.assertEqual(len(ids), 50_000)

    def test_method_and_module_function_agree_on_format(self):
        flogger = ForensicLogger(sink=make_sink("t.cid.method")[0])
        self.assertEqual(len(flogger.new_correlation_id()), 32)

    def test_explicit_correlation_id_is_preserved(self):
        flogger = ForensicLogger(sink=make_sink("t.cid.explicit")[0])
        event = flogger.emit(EventType.ORDER_PLACED, "m", correlation_id="ORD-7788")
        self.assertEqual(event.correlation_id, "ORD-7788")

    def test_events_without_a_correlation_id_do_not_share_one(self):
        flogger = ForensicLogger(sink=make_sink("t.cid.distinct")[0])
        a = flogger.emit(EventType.ORDER_PLACED, "a")
        b = flogger.emit(EventType.ORDER_PLACED, "b")
        self.assertNotEqual(a.correlation_id, b.correlation_id)


class TestSequencingAndConcurrencyRegression(unittest.TestCase):
    """v1.0.0 incremented the counter and appended to the buffer outside any lock."""

    def test_sequence_numbers_are_strictly_monotonic(self):
        flogger = ForensicLogger(sink=make_sink("t.seq.basic")[0])
        e1 = flogger.emit(EventType.ORDER_PLACED, "Order 1")
        e2 = flogger.emit(EventType.FILL_RECEIVED, "Fill 1")
        e3 = flogger.emit(EventType.RISK_BREACH, "Breach!")
        self.assertEqual([e1.sequence_number, e2.sequence_number, e3.sequence_number], [1, 2, 3])

    def test_buffer_stays_in_sequence_order_under_concurrent_emitters(self):
        # v1.0.0: with eight threads emitting 3,000 events each, roughly 30% of adjacent
        # buffer entries were out of sequence order, so the exported JSONL -- the thing
        # a responder reads top to bottom -- was not the order events happened in.
        sink, _ = make_sink("t.seq.threads")
        flogger = ForensicLogger(sink=sink)
        threads_count, per_thread = 8, 500
        switch_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            def work():
                for _ in range(per_thread):
                    flogger.emit(EventType.ORDER_PLACED, "concurrent")

            threads = [threading.Thread(target=work) for _ in range(threads_count)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            sys.setswitchinterval(switch_interval)

        seqs = [e.sequence_number for e in flogger._snapshot()]
        self.assertEqual(len(seqs), threads_count * per_thread)
        self.assertEqual(len(set(seqs)), len(seqs), "duplicate sequence numbers assigned")
        self.assertEqual(seqs, sorted(seqs), "buffer is not held in sequence order")
        self.assertEqual(seqs, list(range(1, threads_count * per_thread + 1)))

    def test_monotonic_clock_is_non_decreasing_in_sequence_order(self):
        # The monotonic reading is taken under the same lock that assigns the sequence,
        # which is what makes an elapsed time computed from it trustworthy even if the
        # wall clock is stepped by NTP mid-incident.
        flogger = ForensicLogger(sink=make_sink("t.seq.mono")[0])
        for i in range(200):
            flogger.emit(EventType.STRATEGY_SIGNAL, str(i))
        monos = [e.monotonic_ns for e in flogger._snapshot()]
        self.assertEqual(monos, sorted(monos))

    def test_two_instances_restart_sequences_and_are_distinguished_by_instance_id(self):
        # Sequence numbers restart at 1 in every instance and every process, so a
        # sequence number alone is ambiguous the moment logs are merged or a bot
        # restarts. instance_id is what disambiguates them.
        a = ForensicLogger(component="bot", sink=make_sink("t.seq.a")[0])
        b = ForensicLogger(component="bot", sink=make_sink("t.seq.b")[0])
        self.assertNotEqual(a.instance_id, b.instance_id)
        self.assertEqual(a.emit(EventType.ORDER_PLACED, "x").sequence_number, 1)
        self.assertEqual(b.emit(EventType.ORDER_PLACED, "y").sequence_number, 1)


class TestBoundedBuffer(unittest.TestCase):
    """The in-memory buffer is bounded, and its truncation is reported, not hidden."""

    def test_buffer_is_bounded_and_keeps_the_most_recent_events(self):
        # v1.0.0 appended to an unbounded list: a long-running bot emitting per-order
        # events grew the buffer without limit until the process was killed.
        flogger = ForensicLogger(sink=make_sink("t.buf.cap")[0], buffer_capacity=10)
        for i in range(25):
            flogger.emit(EventType.FILL_RECEIVED, f"fill-{i}")
        status = flogger.buffer_status()
        self.assertEqual(status["capacity"], 10)
        self.assertEqual(status["emitted"], 25)
        self.assertEqual(status["retained"], 10)
        self.assertEqual(status["evicted"], 15)
        self.assertEqual(status["first_retained_seq"], 16)
        self.assertFalse(status["complete"])

    def test_untruncated_buffer_reports_complete(self):
        flogger = ForensicLogger(sink=make_sink("t.buf.ok")[0], buffer_capacity=10)
        for i in range(10):
            flogger.emit(EventType.FILL_RECEIVED, f"fill-{i}")
        status = flogger.buffer_status()
        self.assertEqual(status["evicted"], 0)
        self.assertTrue(status["complete"])

    def test_truncated_buffer_yields_a_partial_timeline_that_is_flagged(self):
        # The forensic false negative this guards against: a reconstruction that returns
        # fewer events than happened, with nothing in the return value saying so.
        flogger = ForensicLogger(sink=make_sink("t.buf.partial")[0], buffer_capacity=5)
        cid = flogger.new_correlation_id()
        for i in range(12):
            flogger.emit(EventType.PARTIAL_FILL_RECEIVED, f"fill-{i}", correlation_id=cid)
        with self.assertLogs("structured_logger", level="WARNING") as captured:
            timeline = flogger.reconstruct_timeline(cid)
        self.assertEqual(len(timeline), 5)
        self.assertIn("incomplete", " ".join(captured.output))
        self.assertFalse(flogger.buffer_status()["complete"])

    def test_zero_capacity_is_rejected(self):
        with self.assertRaises(ValueError):
            ForensicLogger(buffer_capacity=0)
        with self.assertRaises(ValueError):
            ForensicLogger(buffer_capacity=-1)

    def test_non_integer_capacity_is_rejected_at_construction(self):
        # deque(maxlen=1.5) raises TypeError from deep inside the constructor; catching
        # it here gives the operator a message naming the argument they got wrong.
        for bad in (1.5, "10", True, None):
            with self.subTest(capacity=bad):
                with self.assertRaises(TypeError):
                    ForensicLogger(buffer_capacity=bad)

    def test_default_capacity_is_finite(self):
        self.assertIsInstance(DEFAULT_BUFFER_CAPACITY, int)
        self.assertGreater(DEFAULT_BUFFER_CAPACITY, 0)


class TestTimelineReconstruction(unittest.TestCase):

    def test_events_sharing_a_correlation_id_reconstruct_in_order(self):
        flogger = ForensicLogger(sink=make_sink("t.tl.order")[0])
        cid = flogger.new_correlation_id()
        other = flogger.new_correlation_id()
        flogger.emit(EventType.STRATEGY_SIGNAL, "Long signal", correlation_id=cid)
        flogger.emit(EventType.ORDER_PLACED, "Buy 100 AAPL", correlation_id=other)
        flogger.emit(EventType.ORDER_PLACED, "Buy 100 AAPL", correlation_id=cid)
        flogger.emit(EventType.FILL_RECEIVED, "Filled 100 @ 150.00", correlation_id=cid)

        timeline = flogger.reconstruct_timeline(cid)
        self.assertEqual(
            [entry["event_type"] for entry in timeline],
            ["STRATEGY_SIGNAL", "ORDER_PLACED", "FILL_RECEIVED"],
        )
        self.assertEqual([entry["seq"] for entry in timeline], [1, 3, 4])

    def test_timeline_entries_carry_timestamps(self):
        # v1.0.0's timeline dropped every timestamp, leaving a "timeline" with no times
        # -- unable to answer how long the gap between order and fill was.
        flogger = ForensicLogger(sink=make_sink("t.tl.ts")[0])
        cid = flogger.new_correlation_id()
        flogger.emit(EventType.ORDER_PLACED, "sent", correlation_id=cid)
        flogger.emit(EventType.FILL_RECEIVED, "filled", correlation_id=cid)
        first, second = flogger.reconstruct_timeline(cid)
        for entry in (first, second):
            self.assertIn("ts_ns", entry)
            self.assertIn("ts_iso", entry)
            self.assertEqual(entry["correlation_id"], cid)
        self.assertEqual(first["elapsed_ms"], 0.0)
        self.assertGreaterEqual(second["elapsed_ms"], 0.0)

    def test_elapsed_is_withheld_when_a_timeline_spans_instances(self):
        # Two processes' monotonic clocks share no epoch, so differencing them would
        # produce a confident, meaningless duration.
        flogger = ForensicLogger(sink=make_sink("t.tl.multi")[0])
        cid = "ORD-CROSS"
        local = flogger.emit(EventType.ORDER_PLACED, "sent", correlation_id=cid)
        foreign = StructuredLogEvent(
            sequence_number=1, instance_id="other-process",
            timestamp_ns=local.timestamp_ns + 1, monotonic_ns=10 ** 18,
            event_type="FILL_RECEIVED", correlation_id=cid, component="oms",
            message="filled", severity="INFO", severity_number=9,
        )
        flogger._events.append(foreign)  # merged buffer, as a log shipper would produce
        timeline = flogger.reconstruct_timeline(cid)
        self.assertEqual(len(timeline), 2)
        self.assertTrue(all(entry["elapsed_ms"] is None for entry in timeline))

    def test_unknown_correlation_id_returns_an_empty_timeline(self):
        flogger = ForensicLogger(sink=make_sink("t.tl.empty")[0])
        flogger.emit(EventType.ORDER_PLACED, "m")
        self.assertEqual(flogger.reconstruct_timeline("no-such-id"), [])


class TestQueries(unittest.TestCase):

    def test_query_by_event_type_accepts_enum_and_string(self):
        flogger = ForensicLogger(sink=make_sink("t.q.type")[0])
        flogger.emit(EventType.ORDER_PLACED, "Order 1")
        flogger.emit(EventType.FILL_RECEIVED, "Fill 1")
        flogger.emit(EventType.ORDER_PLACED, "Order 2")
        self.assertEqual(len(flogger.query_by_event_type(EventType.ORDER_PLACED)), 2)
        self.assertEqual(len(flogger.query_by_event_type("ORDER_PLACED")), 2)

    def test_queries_return_events_in_sequence_order(self):
        flogger = ForensicLogger(sink=make_sink("t.q.order")[0])
        cid = flogger.new_correlation_id()
        for i in range(20):
            flogger.emit(EventType.POSITION_UPDATE, str(i), correlation_id=cid)
        seqs = [e.sequence_number for e in flogger.query_by_correlation_id(cid)]
        self.assertEqual(seqs, sorted(seqs))

    def test_exported_json_lines_are_in_sequence_order_and_parse(self):
        flogger = ForensicLogger(sink=make_sink("t.q.export")[0])
        for i in range(5):
            flogger.emit(EventType.ORDER_ACKNOWLEDGED, str(i))
        lines = flogger.get_all_events_json()
        seqs = [_strict_loads(line)["seq"] for line in lines]
        self.assertEqual(seqs, [1, 2, 3, 4, 5])


class TestSinkBehaviour(unittest.TestCase):

    def test_one_json_line_per_event_at_the_mapped_level(self):
        sink, handler = make_sink("t.sink.level")
        flogger = ForensicLogger(sink=sink)
        flogger.emit(EventType.ORDER_PLACED, "a", severity=Severity.DEBUG)
        flogger.emit(EventType.RISK_BREACH, "b", severity=Severity.CRITICAL)
        self.assertEqual(handler.levels, [logging.DEBUG, logging.CRITICAL])
        self.assertEqual([_strict_loads(m)["severity"] for m in handler.messages],
                         ["DEBUG", "CRITICAL"])

    def test_message_newlines_cannot_forge_an_extra_record(self):
        # A venue reject reason, an exception string, or hostile input reaching a log
        # line unescaped would otherwise inject a fabricated event into a JSONL sink.
        sink, handler = make_sink("t.sink.forge")
        flogger = ForensicLogger(sink=sink)
        flogger.emit(EventType.ORDER_REJECTED, 'bad\n{"seq":999,"forged":true}\n')
        self.assertEqual(len(handler.messages), 1)
        self.assertEqual(len(handler.messages[0].splitlines()), 1)
        self.assertEqual(_strict_loads(handler.messages[0])["seq"], 1)

    def test_a_failing_handler_does_not_reach_the_caller(self):
        # ``logging.Handler.handle`` does not catch exceptions raised by ``emit``; only
        # the stdlib handlers do, inside their own bodies. A full disk behind a custom
        # handler would therefore propagate straight into the kill-switch path.
        sink, _ = make_sink("t.sink.fail", handler=_ExplodingHandler())
        flogger = ForensicLogger(sink=sink)
        with self.assertLogs("structured_logger", level="WARNING"):
            event = flogger.emit(EventType.KILL_SWITCH_ACTIVATED, "halt all trading")
        self.assertEqual(event.sequence_number, 1)
        self.assertEqual(len(flogger._snapshot()), 1)
        self.assertEqual(flogger.buffer_status()["sink_failures"], 1)

    def test_a_sink_that_raises_on_log_is_absorbed(self):
        class _RaisingLogger:
            def log(self, level, msg):
                raise RuntimeError("aggregator unreachable")

        flogger = ForensicLogger(sink=_RaisingLogger())
        with self.assertLogs("structured_logger", level="WARNING"):
            event = flogger.emit(EventType.CONNECTIVITY_LOSS, "gateway down")
        self.assertEqual(event.sequence_number, 1)
        status = flogger.buffer_status()
        self.assertEqual(status["sink_failures"], 1)
        self.assertFalse(status["complete"])

    def test_default_sink_is_separate_from_the_module_diagnostic_logger(self):
        # Module warnings are prose; the forensic stream is JSONL. Interleaving them
        # breaks any line-by-line consumer, so they must not share a logger.
        self.assertEqual(DEFAULT_SINK_LOGGER_NAME, "forensic")
        self.assertNotEqual(DEFAULT_SINK_LOGGER_NAME, "structured_logger")
        sink, handler = make_sink(DEFAULT_SINK_LOGGER_NAME)
        try:
            ForensicLogger().emit(EventType.DEPLOYMENT_EVENT, "v2 rolled out")
            self.assertEqual(len(handler.messages), 1)
            self.assertEqual(_strict_loads(handler.messages[0])["event_type"], "DEPLOYMENT_EVENT")
        finally:
            sink.handlers = []
            sink.propagate = True


class TestEventTaxonomy(unittest.TestCase):

    def test_member_names_and_values_agree(self):
        for member in EventType:
            self.assertEqual(member.name, member.value)

    def test_request_and_confirmation_events_are_distinct(self):
        # Being unable to separate "we asked the venue to cancel" from "the venue
        # confirmed the cancel" is what makes an order's live/not-live state at a given
        # instant unrecoverable after the fact.
        for requested, confirmed in [
            (EventType.ORDER_CANCEL_REQUESTED, EventType.ORDER_CANCELLED),
            (EventType.ORDER_MODIFY_REQUESTED, EventType.ORDER_MODIFIED),
            (EventType.ORDER_PLACED, EventType.ORDER_ACKNOWLEDGED),
        ]:
            self.assertNotEqual(requested.value, confirmed.value)

    def test_unknown_event_type_is_recorded_and_flagged(self):
        # v1.0.0 called ``event_type.value`` unconditionally, so passing a plain string
        # raised AttributeError and the event was lost.
        flogger = ForensicLogger(sink=make_sink("t.tax.unknown")[0])
        event = flogger.emit("VENUE_SPECIFIC_EVENT", "something happened")
        self.assertEqual(event.event_type, "VENUE_SPECIFIC_EVENT")
        self.assertTrue(event.metadata["_unknown_event_type"])

    def test_known_event_type_is_not_flagged(self):
        flogger = ForensicLogger(sink=make_sink("t.tax.known")[0])
        event = flogger.emit("FILL_RECEIVED", "filled")
        self.assertEqual(event.event_type, "FILL_RECEIVED")
        self.assertNotIn("_unknown_event_type", event.metadata)


class TestEmitIsTotal(unittest.TestCase):
    """The contract that matters most: emit never raises, whatever it is handed."""

    def test_hostile_inputs_all_produce_records(self):
        class Hostile:
            def __str__(self):
                raise RuntimeError("no")

            def __repr__(self):
                raise RuntimeError("still no")

        flogger = ForensicLogger(sink=make_sink("t.total")[0])
        cases = [
            dict(event_type=None, message="none type"),
            dict(event_type=EventType.SYSTEM_ERROR, message=Hostile()),
            dict(event_type=EventType.SYSTEM_ERROR, message="m", metadata={"h": Hostile()}),
            dict(event_type=EventType.SYSTEM_ERROR, message="m", metadata=["not", "a", "dict"]),
            dict(event_type=EventType.SYSTEM_ERROR, message="m", severity=object()),
            dict(event_type=EventType.SYSTEM_ERROR, message="m", correlation_id=12345),
        ]
        for case in cases:
            with self.subTest(case=sorted(case)):
                event = flogger.emit(**case)
                self.assertIsInstance(event, StructuredLogEvent)
                _strict_loads(event.to_json())
        self.assertEqual(len(flogger._snapshot()), len(cases))

    def test_containers_whose_own_dunder_methods_raise_are_absorbed(self):
        # A partially-initialised ORM row, or a proxy whose backing connection has
        # dropped, raises from __str__/__bool__/items()/__iter__. That happens inside
        # the except block trying to record the outage, so none of it may escape.
        class BadKey:
            def __str__(self):
                raise RuntimeError("no str")

            def __hash__(self):
                return 7

        class BadBool:
            def __bool__(self):
                raise RuntimeError("no bool")

            def __str__(self):
                raise RuntimeError("no str")

        class BadItems(dict):
            def items(self):
                raise RuntimeError("no items")

        class BadIter(list):
            def __iter__(self):
                raise RuntimeError("no iter")

        flogger = ForensicLogger(sink=make_sink("t.total.dunder")[0])

        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={BadKey(): 1})
        self.assertEqual(event.metadata["<unrepresentable-key>"], 1)

        event = flogger.emit(EventType.SYSTEM_ERROR, "m", correlation_id=BadBool())
        self.assertEqual(event.correlation_id, "<unrepresentable-correlation-id>")

        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={"x": BadItems()})
        self.assertIsInstance(event.metadata["x"], str)

        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata={"y": BadIter([1, 2])})
        self.assertIsInstance(event.metadata["y"], str)

        for record in flogger._snapshot():
            _strict_loads(record.to_json())

    def test_non_mapping_metadata_is_kept_not_discarded(self):
        flogger = ForensicLogger(sink=make_sink("t.total.meta")[0])
        event = flogger.emit(EventType.SYSTEM_ERROR, "m", metadata=["leg-a", "leg-b"])
        self.assertEqual(event.metadata["_metadata"], ["leg-a", "leg-b"])

    def test_numeric_correlation_id_is_stringified_and_queryable(self):
        flogger = ForensicLogger(sink=make_sink("t.total.cid")[0])
        flogger.emit(EventType.ORDER_PLACED, "m", correlation_id=12345)
        self.assertEqual(len(flogger.query_by_correlation_id("12345")), 1)
        self.assertEqual(len(flogger.query_by_correlation_id(12345)), 1)


if __name__ == "__main__":
    unittest.main()
