"""
Unit tests for tick-data-schema-versioning.

Expected values are derived independently of the implementation:

  * Nanosecond conversions are computed by hand from the decimal literal
    (1784948000.999999 s is exactly 1784948000999999000 ns), never by
    re-running the module's own conversion.
  * Midpoints are computed by hand ((-40.32 + -37.63) / 2 = -38.975).
  * Several tests are explicit regressions: they name the value the previous
    implementation produced and assert the new one differs.
"""
import logging
import math
import unittest

from schema_versioner import (
    CARRIED_KEY,
    MigrationNote,
    MissingVersionHeaderError,
    NoMigrationPathError,
    NoteKind,
    SchemaConformanceError,
    TickSchema,
    TickSchemaVersioner,
    UnknownSchemaVersionError,
    VERSION_KEY,
)

# Keep the module's once-per-condition warnings off the test console. A
# NullHandler (rather than raising the level) leaves ``assertLogs`` free to
# capture them in ObservabilityTest.
logging.getLogger("schema_versioner").addHandler(logging.NullHandler())

V1_TICK = {
    "schema_version": 1,
    "symbol": "AAPL",
    "timestamp_sec": 1784948000.0,
    "price": 150.50,
    "volume": 100.0,
}

V2_TICK = {
    "schema_version": 2,
    "symbol": "BTCUSDT",
    "timestamp_ns": 1784948000000000000,
    "bid": 60000.0,
    "ask": 60010.0,
    "volume": 1.5,
    "exchange_id": "BINANCE",
}

V3_TICK = {
    "schema_version": 3,
    "symbol": "BTCUSDT",
    "timestamp_ns": 1784948000000000000,
    "bid": 60000.0,
    "ask": 60010.0,
    "volume": 1.5,
    "exchange_id": "BINANCE",
    "bid_size": 2.0,
    "ask_size": 3.0,
}


class VersionHeaderTest(unittest.TestCase):
    """The header is read, never inferred."""

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_read_version_returns_declared_header(self):
        self.assertEqual(self.versioner.read_version(V1_TICK), 1)
        self.assertEqual(self.versioner.read_version(V3_TICK), 3)

    def test_missing_header_raises_rather_than_defaulting_to_v1(self):
        """Regression: the previous implementation defaulted to V1.

        A payload with no header was migrated as though it were V1, producing a
        structurally valid V2 tick with symbol '', bid 0.0, ask 0.0 and
        timestamp 0 -- a fabricated tick indistinguishable from a real one.
        """
        unversioned = {"symbol": "AAPL", "some_vendor_field": 1}
        with self.assertRaises(MissingVersionHeaderError):
            self.versioner.read_version(unversioned)
        with self.assertRaises(MissingVersionHeaderError):
            self.versioner.normalize_to_target_version(unversioned, 2)

    def test_bool_header_is_rejected_despite_being_an_int(self):
        payload = dict(V1_TICK, schema_version=True)
        with self.assertRaises(MissingVersionHeaderError):
            self.versioner.read_version(payload)

    def test_non_integer_header_is_rejected(self):
        for bad in ("2", 2.0, None, [2]):
            with self.subTest(header=bad):
                with self.assertRaises(MissingVersionHeaderError):
                    self.versioner.read_version(dict(V1_TICK, schema_version=bad))


class WrapPayloadTest(unittest.TestCase):
    """Stamping a header is a claim about the body, and the claim is checked."""

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_valid_body_is_stamped_and_source_is_not_mutated(self):
        body = {"symbol": "AAPL", "timestamp_sec": 1.0, "price": 2.0, "volume": 3.0}
        wrapped = self.versioner.wrap_payload(body, 1)
        self.assertEqual(wrapped[VERSION_KEY], 1)
        self.assertNotIn(VERSION_KEY, body)
        self.assertIsNot(wrapped, body)

    def test_stamping_a_v1_body_as_v2_is_refused(self):
        """Regression: the previous implementation stamped any version onto
        any body, producing a payload that lied about its own schema. The
        KeyError then surfaced in the V2 consumer, far from the cause."""
        v1_body = {"symbol": "AAPL", "timestamp_sec": 1.0, "price": 2.0, "volume": 3.0}
        with self.assertRaises(SchemaConformanceError):
            self.versioner.wrap_payload(v1_body, 2)

    def test_relabelling_an_already_versioned_payload_is_refused(self):
        with self.assertRaises(SchemaConformanceError) as ctx:
            self.versioner.wrap_payload(V1_TICK, 2)
        self.assertIn("relabel", str(ctx.exception))

    def test_restamping_the_same_version_is_allowed(self):
        wrapped = self.versioner.wrap_payload(V1_TICK, 1)
        self.assertEqual(wrapped[VERSION_KEY], 1)

    def test_unknown_version_is_refused(self):
        with self.assertRaises(UnknownSchemaVersionError):
            self.versioner.wrap_payload({"symbol": "X"}, 99)


class ConformanceTest(unittest.TestCase):
    """A body must match the schema its own header declares."""

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_missing_required_field_raises(self):
        broken = {k: v for k, v in V1_TICK.items() if k != "price"}
        with self.assertRaises(SchemaConformanceError) as ctx:
            self.versioner.normalize_to_target_version(broken, 2)
        self.assertIn("price", str(ctx.exception))

    def test_non_finite_price_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(price=bad):
                with self.assertRaises(SchemaConformanceError):
                    self.versioner.normalize_to_target_version(
                        dict(V1_TICK, price=bad), 2)

    def test_string_where_number_declared_raises(self):
        """The 'breaking field type mutation' pitfall, enforced."""
        with self.assertRaises(SchemaConformanceError):
            self.versioner.normalize_to_target_version(
                dict(V1_TICK, price="150.50"), 2)

    def test_bool_where_number_declared_raises(self):
        with self.assertRaises(SchemaConformanceError):
            self.versioner.normalize_to_target_version(
                dict(V1_TICK, price=True), 2)

    def test_negative_volume_raises(self):
        with self.assertRaises(SchemaConformanceError):
            self.versioner.normalize_to_target_version(
                dict(V1_TICK, volume=-1.0), 2)

    def test_int_is_accepted_where_float_declared(self):
        result = self.versioner.normalize_to_target_version(
            dict(V1_TICK, price=150, volume=100), 2)
        self.assertEqual(result.payload["bid"], 150)

    def test_malformed_carry_envelope_is_a_conformance_failure(self):
        """The carry envelope arrives over the wire like everything else, so a
        malformed one must not escape as a bare ``ValueError`` from ``dict()``."""
        for bad in (["a", "b"], "oops", 5):
            with self.subTest(carried=bad):
                with self.assertRaises(SchemaConformanceError):
                    self.versioner.normalize_to_target_version(
                        dict(V1_TICK, **{CARRIED_KEY: bad}), 2)

    def test_absent_optional_field_is_accepted(self):
        v2_no_venue = {k: v for k, v in V2_TICK.items() if k != "exchange_id"}
        result = self.versioner.normalize_to_target_version(v2_no_venue, 1)
        self.assertNotIn(CARRIED_KEY, result.payload)


class UpgradeV1ToV2Test(unittest.TestCase):

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_timestamp_conversion_is_exact_not_float_scaled(self):
        """Regression: ``int(ts_sec * 1e9)`` scaled in binary64 and truncated
        toward zero, yielding 1784948000999998976 -- 24 ns early."""
        result = self.versioner.normalize_to_target_version(
            dict(V1_TICK, timestamp_sec=1784948000.999999), 2)
        self.assertEqual(result.payload["timestamp_ns"], 1784948000999999000)
        self.assertNotEqual(result.payload["timestamp_ns"], 1784948000999998976)

    def test_sub_nanosecond_values_round_half_to_even(self):
        """1.5 ns and 2.5 ns both resolve to 2 ns under round-half-even.

        Truncation would give 1 and 2, so this pins the rounding mode rather
        than merely the magnitude.
        """
        for seconds, expected_ns in ((1.5e-9, 2), (2.5e-9, 2), (3.5e-9, 4)):
            with self.subTest(seconds=seconds):
                result = self.versioner.normalize_to_target_version(
                    dict(V1_TICK, timestamp_sec=seconds), 2)
                self.assertEqual(result.payload["timestamp_ns"], expected_ns)

    def test_bid_and_ask_are_flagged_as_synthesized(self):
        """bid == ask means a zero spread that the publisher never quoted."""
        result = self.versioner.normalize_to_target_version(V1_TICK, 2)
        self.assertEqual(result.payload["bid"], 150.50)
        self.assertEqual(result.payload["ask"], 150.50)
        self.assertTrue(result.has_synthesized_values)
        self.assertEqual(
            set(result.fields_noted(NoteKind.SYNTHESIZED_VALUE)), {"bid", "ask"})
        self.assertFalse(result.is_lossless)

    def test_exchange_id_defaults_to_none_not_a_sentinel_string(self):
        """Regression: the previous implementation wrote 'UNKNOWN' (and the V2
        dataclass defaulted to 'US'). A sentinel string joins against a venue
        reference table as though it were a venue."""
        result = self.versioner.normalize_to_target_version(V1_TICK, 2)
        self.assertIsNone(result.payload["exchange_id"])
        self.assertEqual(
            result.fields_noted(NoteKind.DEFAULT_APPLIED), ("exchange_id",))

    def test_precision_note_raised_for_a_modern_epoch(self):
        result = self.versioner.normalize_to_target_version(V1_TICK, 2)
        notes = result.notes_of(NoteKind.PRECISION_REDUCED)
        self.assertEqual([n.field for n in notes], ["timestamp_ns"])
        self.assertIn("238 ns", notes[0].detail)

    def test_no_precision_note_when_the_float_is_exact(self):
        result = self.versioner.normalize_to_target_version(
            dict(V1_TICK, timestamp_sec=0.0), 2)
        self.assertEqual(result.notes_of(NoteKind.PRECISION_REDUCED), ())

    def test_unknown_vendor_field_survives_the_hop(self):
        """Regression: the previous implementation rebuilt a fresh dict and
        dropped every unrecognized key -- the exact pitfall this skill
        documents."""
        result = self.versioner.normalize_to_target_version(
            dict(V1_TICK, venue_seq=7, trade_id="abc"), 2)
        self.assertEqual(result.payload["venue_seq"], 7)
        self.assertEqual(result.payload["trade_id"], "abc")
        self.assertEqual(result.unknown_fields, ("trade_id", "venue_seq"))

    def test_symbol_case_is_preserved(self):
        """Regression: the previous implementation upper-cased the symbol.
        Symbol namespace normalization belongs to feed normalization, not to a
        version adapter, and silently rewriting an identifier breaks joins."""
        result = self.versioner.normalize_to_target_version(
            dict(V1_TICK, symbol="es.fut.z25"), 2)
        self.assertEqual(result.payload["symbol"], "es.fut.z25")

    def test_source_payload_is_not_mutated(self):
        source = dict(V1_TICK)
        snapshot = dict(source)
        self.versioner.normalize_to_target_version(source, 2)
        self.assertEqual(source, snapshot)


class DowngradeV2ToV1Test(unittest.TestCase):

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=1)

    def test_midpoint_of_a_normal_quote(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 1)
        self.assertEqual(result.payload["price"], 60005.0)
        self.assertEqual(result.payload["timestamp_sec"], 1784948000.0)

    def test_midpoint_of_a_negative_quote(self):
        """Regression: ``(bid + ask) / 2 if bid > 0 and ask > 0 else (bid or
        ask)`` returned the bid, -40.32, for the NYMEX WTI front month on
        2020-04-20 (settled -37.63/b, low -40.32/b). The mid is -38.975."""
        crude = dict(V2_TICK, symbol="CLK0", bid=-40.32, ask=-37.63,
                     exchange_id="NYMEX")
        result = self.versioner.normalize_to_target_version(crude, 1)
        self.assertAlmostEqual(result.payload["price"], -38.975, places=10)

    def test_midpoint_when_one_side_is_exactly_zero(self):
        """Regression: ``bid or ask`` treated a zero bid as absent and returned
        the ask, 10.0, instead of the midpoint 5.0."""
        result = self.versioner.normalize_to_target_version(
            dict(V2_TICK, bid=0.0, ask=10.0), 1)
        self.assertEqual(result.payload["price"], 5.0)

    def test_midpoint_always_lies_within_the_quote(self):
        for bid, ask in ((1e300, 1.5e300), (-5.0, 5.0), (99.99, 100.01)):
            with self.subTest(bid=bid, ask=ask):
                result = self.versioner.normalize_to_target_version(
                    dict(V2_TICK, bid=bid, ask=ask), 1)
                price = result.payload["price"]
                self.assertTrue(math.isfinite(price))
                self.assertGreaterEqual(price, bid)
                self.assertLessEqual(price, ask)

    def test_crossed_quote_is_flagged_but_still_migrated(self):
        result = self.versioner.normalize_to_target_version(
            dict(V2_TICK, bid=101.0, ask=99.0), 1)
        self.assertTrue(result.has_suspect_values)
        self.assertEqual(result.fields_noted(NoteKind.SUSPECT_VALUE), ("price",))
        self.assertEqual(result.payload["price"], 100.0)

    def test_uncrossed_quote_raises_no_suspect_note(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 1)
        self.assertFalse(result.has_suspect_values)

    def test_locked_quote_is_not_flagged_as_crossed(self):
        result = self.versioner.normalize_to_target_version(
            dict(V2_TICK, bid=100.0, ask=100.0), 1)
        self.assertFalse(result.has_suspect_values)

    def test_exchange_id_is_parked_not_dropped(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 1)
        self.assertNotIn("exchange_id", result.payload)
        self.assertEqual(result.payload[CARRIED_KEY], {"exchange_id": "BINANCE"})
        self.assertEqual(
            result.fields_noted(NoteKind.FIELD_CARRIED), ("exchange_id",))

    def test_collapsing_a_quote_to_a_midpoint_is_always_noted(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 1)
        self.assertIn("price", result.fields_noted(NoteKind.PRECISION_REDUCED))
        self.assertFalse(result.is_lossless)

    def test_unrepresentable_nanoseconds_are_noted(self):
        """1784948000000000001 ns cannot be held in binary64 seconds; the
        nearest float round-trips to 1784948000000000000 ns."""
        result = self.versioner.normalize_to_target_version(
            dict(V2_TICK, timestamp_ns=1784948000000000001), 1)
        fields = result.fields_noted(NoteKind.PRECISION_REDUCED)
        self.assertIn("timestamp_sec", fields)

    def test_representable_nanoseconds_raise_no_timestamp_note(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 1)
        self.assertNotIn("timestamp_sec",
                         result.fields_noted(NoteKind.PRECISION_REDUCED))


class MultiHopTest(unittest.TestCase):
    """Chained migrations, and the fields that must survive an intermediate hop."""

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=3)

    def test_v1_to_v3_runs_through_v2(self):
        result = self.versioner.normalize_to_target_version(V1_TICK, 3)
        self.assertEqual(result.path, (1, 2, 3))
        self.assertEqual(result.payload[VERSION_KEY], 3)
        self.assertIsNone(result.payload["bid_size"])
        self.assertIsNone(result.payload["ask_size"])
        self.assertIsNone(result.payload["exchange_id"])

    def test_v3_to_v1_runs_through_v2(self):
        result = self.versioner.normalize_to_target_version(V3_TICK, 1)
        self.assertEqual(result.path, (3, 2, 1))

    def test_notes_accumulate_across_hops(self):
        result = self.versioner.normalize_to_target_version(V1_TICK, 3)
        kinds = {n.kind for n in result.notes}
        self.assertIn(NoteKind.SYNTHESIZED_VALUE, kinds)   # from hop 1->2
        self.assertIn(NoteKind.DEFAULT_APPLIED, kinds)     # from hops 1->2, 2->3
        self.assertEqual(
            set(result.fields_noted(NoteKind.DEFAULT_APPLIED)),
            {"exchange_id", "bid_size", "ask_size"})

    def test_round_trip_v3_v1_v3_restores_every_carried_field(self):
        """The headline pitfall: an intermediate hop must not eat a field.

        bid and ask cannot survive -- V1 has one price -- but the venue and the
        sizes never needed to be lost, and a vendor extension must pass through
        both hops untouched.
        """
        source = dict(V3_TICK, trade_id="t-1")
        down = self.versioner.normalize_to_target_version(source, 1)
        self.assertEqual(down.payload[CARRIED_KEY],
                         {"bid_size": 2.0, "ask_size": 3.0,
                          "exchange_id": "BINANCE"})
        self.assertEqual(down.payload["trade_id"], "t-1")

        up = self.versioner.normalize_to_target_version(down.payload, 3)
        self.assertEqual(up.payload["exchange_id"], "BINANCE")
        self.assertEqual(up.payload["bid_size"], 2.0)
        self.assertEqual(up.payload["ask_size"], 3.0)
        self.assertEqual(up.payload["trade_id"], "t-1")
        self.assertNotIn(CARRIED_KEY, up.payload)

    def test_restored_fields_raise_no_default_applied_note(self):
        down = self.versioner.normalize_to_target_version(V3_TICK, 1)
        up = self.versioner.normalize_to_target_version(down.payload, 3)
        self.assertEqual(up.notes_of(NoteKind.DEFAULT_APPLIED), ())

    def test_v2_to_v3_is_lossless(self):
        """Adding optional fields loses nothing: a default applied to a field
        the writer never had is the defined resolution, not a loss."""
        result = self.versioner.normalize_to_target_version(V2_TICK, 3)
        self.assertTrue(result.is_lossless)
        self.assertFalse(result.has_synthesized_values)

    def test_noop_returns_a_copy_with_no_notes(self):
        result = self.versioner.normalize_to_target_version(V2_TICK, 2)
        self.assertEqual(result.path, (2,))
        self.assertEqual(result.notes, ())
        self.assertTrue(result.is_lossless)
        self.assertIsNot(result.payload, V2_TICK)
        self.assertEqual(result.payload, V2_TICK)

    def test_default_target_version_is_used_when_none_given(self):
        versioner = TickSchemaVersioner(target_version=1)
        result = versioner.normalize_to_target_version(V3_TICK)
        self.assertEqual(result.target_version, 1)


class RegistryTest(unittest.TestCase):

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_known_versions(self):
        self.assertEqual(self.versioner.known_versions, (1, 2, 3))

    def test_unknown_source_version_raises(self):
        with self.assertRaises(UnknownSchemaVersionError):
            self.versioner.normalize_to_target_version(
                dict(V1_TICK, schema_version=7), 2)

    def test_unknown_target_version_raises(self):
        """Regression: the previous implementation logged a warning and
        returned the raw, still-old-versioned payload, so the consumer went on
        to read fields that were not there."""
        with self.assertRaises(UnknownSchemaVersionError):
            self.versioner.normalize_to_target_version(V1_TICK, 9)

    def test_unreachable_version_raises_no_path(self):
        self.versioner.register_schema(TickSchema(version=9, fields=()))
        with self.assertRaises(NoMigrationPathError):
            self.versioner.normalize_to_target_version(V1_TICK, 9)

    def test_adapter_for_unregistered_version_is_refused(self):
        with self.assertRaises(UnknownSchemaVersionError):
            self.versioner.register_adapter(2, 42, lambda body, notes: body)

    def test_adapter_must_change_the_version(self):
        with self.assertRaises(ValueError):
            self.versioner.register_adapter(2, 2, lambda body, notes: body)

    def test_constructor_rejects_an_unknown_target(self):
        with self.assertRaises(UnknownSchemaVersionError):
            TickSchemaVersioner(target_version=42)

    def test_a_registered_adapter_extends_the_chain(self):
        self.versioner.register_schema(TickSchema(version=4, fields=()))

        def to_v4(body, notes):
            out = dict(body)
            notes.append(MigrationNote(
                NoteKind.DEFAULT_APPLIED, "v4_marker", "added by test adapter"))
            out["v4_marker"] = True
            return out

        self.versioner.register_adapter(3, 4, to_v4)
        result = self.versioner.normalize_to_target_version(V1_TICK, 4)
        self.assertEqual(result.path, (1, 2, 3, 4))
        self.assertTrue(result.payload["v4_marker"])


class ObservabilityTest(unittest.TestCase):

    def setUp(self):
        self.versioner = TickSchemaVersioner(target_version=2)

    def test_counters_track_hops_notes_and_migrations(self):
        self.versioner.normalize_to_target_version(V1_TICK, 2)
        self.versioner.normalize_to_target_version(V1_TICK, 2)
        stats = self.versioner.stats()
        self.assertEqual(stats["hop:1->2"], 2)
        self.assertEqual(stats["migrate:1->2"], 2)
        self.assertEqual(stats["note:synthesized_value"], 4)

    def test_each_lossy_condition_warns_exactly_once(self):
        """Per-tick logging is not viable at tick rates; the counters carry
        the volume and the log carries the distinct conditions."""
        with self.assertLogs("schema_versioner", level="WARNING") as captured:
            for _ in range(5):
                self.versioner.normalize_to_target_version(V1_TICK, 2)
        bid_warnings = [r for r in captured.records
                        if "'bid'" in r.getMessage()
                        and "synthesized_value" in r.getMessage()]
        self.assertEqual(len(bid_warnings), 1)
        # One warning, but every occurrence is still counted: 5 migrations x
        # 2 synthesized fields (bid, ask).
        self.assertEqual(self.versioner.stats()["note:synthesized_value"], 10)

    def test_default_applied_notes_do_not_warn(self):
        logger = logging.getLogger("schema_versioner")
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture(level=logging.WARNING)
        logger.addHandler(handler)
        try:
            self.versioner.normalize_to_target_version(V2_TICK, 3)
        finally:
            logger.removeHandler(handler)
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
