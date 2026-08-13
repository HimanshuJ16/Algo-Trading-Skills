"""
Unit tests for best-execution-record-keeping-global.

Regression tests are marked as such: each one fails against the pre-2.1.0 behaviour
(violations overwriting each other, an unassessable record silently passing, a failed
record leaving no audit trail, and a per-record hash that made tampering undetectable).
"""
import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone


def setUpModule():
    """Keep the repo-wide test run readable -- these tests deliberately trigger warnings."""
    logging.getLogger("best_execution_record_keeping_global").setLevel(logging.CRITICAL)

from best_execution_record_keeping_global import (
    BestExAnalysis,
    BestExRecordError,
    BestExecutionRecordKeepingGlobalEngine,
    TimestampPrecision,
    TradeRecord,
)


def iso(offset_seconds: float = 0.0) -> str:
    base = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).isoformat()


class BaseCase(unittest.TestCase):

    def setUp(self):
        self.engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        self.base_timestamps = {
            "creation": iso(0),
            "submission": iso(1),
            "execution": iso(2),
        }

    def record(self, **overrides) -> TradeRecord:
        params = dict(
            order_id="ORD-001",
            symbol="AAPL",
            side="BUY",
            quantity=100,
            price=150.0,
            order_type="LIMIT",
            venue="XNAS",
            algo_id="VWAP_01",
            trader_id="TRD123",
            client_id="CLI456",
            timestamps=dict(self.base_timestamps),
            fills=[{"price": 150.0, "qty": 100, "timestamp": self.base_timestamps["execution"]}],
            benchmark_price=149.5,
            regulatory_tags={"MiFID_II_Capacity": "AOTC"},
        )
        params.update(overrides)
        return TradeRecord(**params)


class TestScreening(BaseCase):

    def test_compliant_trade(self):
        analysis = self.engine.run_best_ex_checks(self.record(benchmark_price=150.0))

        self.assertTrue(analysis.is_compliant)
        self.assertEqual(analysis.violations, [])
        self.assertTrue(analysis.slippage_evaluated)
        self.assertIsNotNone(analysis.record_hash)
        self.assertNotEqual(analysis.record_hash, "")

    def test_compliant_notes_do_not_claim_best_execution(self):
        """A pass must not read as a regulatory determination."""
        analysis = self.engine.run_best_ex_checks(self.record(benchmark_price=150.0))
        self.assertIn("Not a best-execution determination", analysis.notes)

    def test_buy_slippage_sign_and_magnitude(self):
        """Buying at 305 against a 300 benchmark is +1.6667% adverse slippage."""
        analysis = self.engine.run_best_ex_checks(
            self.record(
                side="BUY",
                benchmark_price=300.0,
                fills=[{"price": 305.0, "qty": 100, "timestamp": iso(2)}],
            )
        )
        self.assertAlmostEqual(analysis.slippage, 5.0 / 300.0, places=12)
        self.assertFalse(analysis.is_compliant)

    def test_sell_slippage_sign_is_adverse_cost(self):
        """Selling below the benchmark is adverse, so slippage must be positive."""
        analysis = self.engine.run_best_ex_checks(
            self.record(
                side="SELL",
                benchmark_price=300.0,
                fills=[{"price": 295.0, "qty": 100, "timestamp": iso(2)}],
            )
        )
        self.assertAlmostEqual(analysis.slippage, 5.0 / 300.0, places=12)
        self.assertFalse(analysis.is_compliant)

    def test_price_improvement_is_negative_slippage_and_passes(self):
        analysis = self.engine.run_best_ex_checks(
            self.record(
                side="BUY",
                benchmark_price=300.0,
                fills=[{"price": 297.0, "qty": 100, "timestamp": iso(2)}],
            )
        )
        self.assertAlmostEqual(analysis.slippage, -3.0 / 300.0, places=12)
        self.assertTrue(analysis.is_compliant)

    def test_slippage_uses_volume_weighted_average_not_simple_average(self):
        """VWAP of 90@10 and 110@90 is 108.0, not the simple mean 100.0."""
        analysis = self.engine.run_best_ex_checks(
            self.record(
                side="BUY",
                benchmark_price=100.0,
                fills=[
                    {"price": 90.0, "qty": 10, "timestamp": iso(2)},
                    {"price": 110.0, "qty": 90, "timestamp": iso(3)},
                ],
            )
        )
        self.assertAlmostEqual(analysis.slippage, 0.08, places=12)

    def test_exact_tolerance_boundary_does_not_flag(self):
        """Flagging is strictly greater-than; slippage exactly at tolerance passes."""
        engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.02)
        analysis = engine.run_best_ex_checks(
            self.record(
                side="BUY",
                benchmark_price=100.0,
                fills=[{"price": 102.0, "qty": 100, "timestamp": iso(2)}],
            )
        )
        self.assertAlmostEqual(analysis.slippage, 0.02, places=12)
        self.assertTrue(analysis.is_compliant)

    def test_slippage_note_resolves_below_the_tolerance_it_breaches(self):
        """
        A note reading "Slippage 0.15% exceeded tolerance 0.15%" reads as a
        contradiction. The message must carry enough resolution to show the breach.
        """
        engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.0015)
        analysis = engine.run_best_ex_checks(
            self.record(
                side="BUY",
                benchmark_price=100.0,
                fills=[{"price": 100.1523, "qty": 100, "timestamp": iso(2)}],
            )
        )
        self.assertFalse(analysis.is_compliant)
        note = analysis.violations[0]
        self.assertIn("0.1523%", note)
        self.assertIn("0.1500%", note)

    def test_unrecognised_side_does_not_guess_slippage_direction(self):
        analysis = self.engine.run_best_ex_checks(self.record(side="LONG"))
        self.assertFalse(analysis.slippage_evaluated)
        self.assertNotEqual(analysis.slippage, analysis.slippage)  # nan
        self.assertTrue(any("side" in v for v in analysis.violations))


class TestRegressions(BaseCase):
    """Each of these passed incorrectly before version 2.1.0."""

    def test_all_violations_recorded_not_just_the_last(self):
        """
        REGRESSION: a slippage breach used to be overwritten by the missing-tags note,
        erasing the more serious finding from the compliance record.
        """
        analysis = self.engine.run_best_ex_checks(
            self.record(
                benchmark_price=150.0,
                fills=[{"price": 200.0, "qty": 100, "timestamp": iso(2)}],
                regulatory_tags={},
            )
        )

        self.assertFalse(analysis.is_compliant)
        self.assertGreaterEqual(len(analysis.violations), 2)
        self.assertTrue(any("Slippage" in v for v in analysis.violations))
        self.assertTrue(any("regulatory tags" in v for v in analysis.violations))
        self.assertIn("Slippage", analysis.notes)
        self.assertIn("regulatory tags", analysis.notes)

    def test_missing_benchmark_is_not_a_pass(self):
        """
        REGRESSION: a record with no benchmark used to report 0.0% slippage and
        "Best execution standards met" -- the entire screen skipped, reported as a pass.
        """
        analysis = self.engine.run_best_ex_checks(self.record(benchmark_price=None))

        self.assertFalse(analysis.is_compliant)
        self.assertFalse(analysis.slippage_evaluated)
        self.assertNotEqual(analysis.slippage, analysis.slippage)  # nan, not 0.0
        self.assertTrue(any("benchmark" in v.lower() for v in analysis.violations))

    def test_no_fills_is_not_a_pass(self):
        """REGRESSION: an unfilled order used to pass with 0.0% slippage."""
        analysis = self.engine.run_best_ex_checks(self.record(fills=[]))

        self.assertFalse(analysis.is_compliant)
        self.assertFalse(analysis.slippage_evaluated)
        self.assertTrue(any("No fills" in v for v in analysis.violations))

    def test_failed_record_still_reaches_the_audit_log_with_a_hash(self):
        """
        REGRESSION: a missing execution timestamp used to return early with an empty
        hash and no audit entry -- losing exactly the record an examiner would ask about.
        """
        timestamps = dict(self.base_timestamps)
        del timestamps["execution"]

        analysis = self.engine.run_best_ex_checks(self.record(timestamps=timestamps))

        self.assertFalse(analysis.is_compliant)
        self.assertNotEqual(analysis.record_hash, "")
        self.assertEqual(len(self.engine.audit_log), 1)
        self.assertEqual(self.engine.audit_log[0]["analysis"]["record_hash"], analysis.record_hash)

    def test_tampering_with_a_logged_record_is_detected(self):
        """
        REGRESSION: hashing each record in isolation let an edited record keep a
        'matching' hash. verify_audit_log now recomputes and reports the mismatch.
        """
        self.engine.run_best_ex_checks(self.record(order_id="A", benchmark_price=150.0))
        self.engine.run_best_ex_checks(self.record(order_id="B", benchmark_price=150.0))
        self.assertEqual(self.engine.verify_audit_log(), [])

        self.engine.audit_log[0]["record"]["fills"][0]["price"] = 999.0
        problems = self.engine.verify_audit_log()

        self.assertTrue(problems)
        self.assertTrue(any("modified after screening" in p for p in problems))


class TestAuditChain(BaseCase):

    def test_chain_links_to_genesis_then_forward(self):
        first = self.engine.run_best_ex_checks(self.record(order_id="A", benchmark_price=150.0))
        second = self.engine.run_best_ex_checks(self.record(order_id="B", benchmark_price=150.0))

        log = self.engine.audit_log
        self.assertEqual(log[0]["previous_entry_hash"], "0" * 64)
        self.assertEqual(log[1]["previous_entry_hash"], log[0]["entry_hash"])
        self.assertEqual(self.engine.head_hash, log[1]["entry_hash"])
        self.assertNotEqual(first.record_hash, second.record_hash)

    def test_identical_records_hash_identically(self):
        """Determinism: the same payload must always produce the same record hash."""
        a = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        b = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        self.assertEqual(
            a.run_best_ex_checks(self.record(benchmark_price=150.0)).record_hash,
            b.run_best_ex_checks(self.record(benchmark_price=150.0)).record_hash,
        )

    def test_deleting_an_entry_is_detected(self):
        for oid in ("A", "B", "C"):
            self.engine.run_best_ex_checks(self.record(order_id=oid, benchmark_price=150.0))

        del self.engine.audit_log[1]
        problems = self.engine.verify_audit_log()

        self.assertTrue(problems)
        self.assertTrue(any("sequence" in p or "chain is broken" in p for p in problems))

    def test_reordering_entries_is_detected(self):
        for oid in ("A", "B"):
            self.engine.run_best_ex_checks(self.record(order_id=oid, benchmark_price=150.0))

        self.engine.audit_log.reverse()
        self.assertTrue(self.engine.verify_audit_log())

    def test_editing_an_analysis_verdict_is_detected(self):
        """Flipping a verdict from non-compliant to compliant must not go unnoticed."""
        self.engine.run_best_ex_checks(
            self.record(fills=[{"price": 200.0, "qty": 100, "timestamp": iso(2)}])
        )
        self.engine.audit_log[0]["analysis"]["is_compliant"] = True

        problems = self.engine.verify_audit_log()
        self.assertTrue(any("entry_hash does not match" in p for p in problems))

    def test_empty_log_verifies_and_reports_genesis_head(self):
        self.assertEqual(self.engine.verify_audit_log(), [])
        self.assertEqual(self.engine.head_hash, "0" * 64)


class TestValidation(BaseCase):

    def test_malformed_fill_reports_a_violation_rather_than_raising_keyerror(self):
        analysis = self.engine.run_best_ex_checks(self.record(fills=[{"price": 150.0}]))

        self.assertFalse(analysis.is_compliant)
        self.assertTrue(any("Malformed fill" in v for v in analysis.violations))
        self.assertEqual(len(self.engine.audit_log), 1)

    def test_non_numeric_fill_price_reports_a_violation(self):
        analysis = self.engine.run_best_ex_checks(
            self.record(fills=[{"price": "150.0", "qty": 100, "timestamp": iso(2)}])
        )
        self.assertTrue(any("Malformed fill" in v for v in analysis.violations))

    def test_calculate_average_fill_price_raises_clearly_on_bad_fill(self):
        with self.assertRaises(BestExRecordError) as ctx:
            self.record(fills=[{"price": 1.0}]).calculate_average_fill_price()
        self.assertIn("qty", str(ctx.exception))

    def test_naive_timestamp_is_flagged(self):
        timestamps = {k: v.replace("+00:00", "") for k, v in self.base_timestamps.items()}
        analysis = self.engine.run_best_ex_checks(self.record(timestamps=timestamps))
        self.assertTrue(any("no timezone offset" in v for v in analysis.violations))

    def test_non_utc_timestamp_is_flagged(self):
        timestamps = dict(self.base_timestamps)
        timestamps["execution"] = "2026-08-13T12:00:02+02:00"
        analysis = self.engine.run_best_ex_checks(self.record(timestamps=timestamps))
        self.assertTrue(any("not UTC" in v for v in analysis.violations))

    def test_out_of_order_timestamps_are_flagged(self):
        timestamps = {"creation": iso(5), "submission": iso(1), "execution": iso(2)}
        analysis = self.engine.run_best_ex_checks(self.record(timestamps=timestamps))
        self.assertTrue(any("precedes" in v for v in analysis.violations))

    def test_unparseable_timestamp_is_flagged(self):
        timestamps = dict(self.base_timestamps)
        timestamps["execution"] = "13/08/2026 10:00:02"
        analysis = self.engine.run_best_ex_checks(self.record(timestamps=timestamps))
        self.assertTrue(any("not parseable" in v for v in analysis.violations))

    def test_z_suffix_timestamps_are_accepted(self):
        timestamps = {
            "creation": "2026-08-13T10:00:00.000000Z",
            "submission": "2026-08-13T10:00:01.000000Z",
            "execution": "2026-08-13T10:00:02.000000Z",
        }
        analysis = self.engine.run_best_ex_checks(
            self.record(timestamps=timestamps, benchmark_price=150.0)
        )
        self.assertTrue(analysis.is_compliant, analysis.violations)

    def test_timestamp_precision_check_is_opt_in_and_activity_specific(self):
        """
        RTS 25 sets granularity by activity, so the engine checks nothing by default
        and flags only against the precision the caller declares.
        """
        second_precision = {
            "creation": "2026-08-13T10:00:00+00:00",
            "submission": "2026-08-13T10:00:01+00:00",
            "execution": "2026-08-13T10:00:02+00:00",
        }
        record = self.record(timestamps=second_precision, benchmark_price=150.0)

        default_engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        self.assertTrue(default_engine.run_best_ex_checks(record).is_compliant)

        hft_engine = BestExecutionRecordKeepingGlobalEngine(
            slippage_tolerance=0.01, timestamp_precision=TimestampPrecision.MICROSECOND
        )
        analysis = hft_engine.run_best_ex_checks(record)
        self.assertFalse(analysis.is_compliant)
        self.assertTrue(any("fractional-second digit" in v for v in analysis.violations))

    def test_millisecond_precision_accepted_for_millisecond_requirement(self):
        ms = {
            "creation": "2026-08-13T10:00:00.000+00:00",
            "submission": "2026-08-13T10:00:01.000+00:00",
            "execution": "2026-08-13T10:00:02.000+00:00",
        }
        engine = BestExecutionRecordKeepingGlobalEngine(
            slippage_tolerance=0.01, timestamp_precision=TimestampPrecision.MILLISECOND
        )
        analysis = engine.run_best_ex_checks(self.record(timestamps=ms, benchmark_price=150.0))
        self.assertTrue(analysis.is_compliant, analysis.violations)

    def test_named_required_tags_are_enforced(self):
        engine = BestExecutionRecordKeepingGlobalEngine(
            slippage_tolerance=0.01, required_tags=("LEI", "MiFID_II_Capacity")
        )
        analysis = engine.run_best_ex_checks(
            self.record(regulatory_tags={"MiFID_II_Capacity": "AOTC"}, benchmark_price=150.0)
        )
        self.assertFalse(analysis.is_compliant)
        self.assertTrue(any("LEI" in v for v in analysis.violations))

    def test_empty_string_tag_counts_as_missing(self):
        engine = BestExecutionRecordKeepingGlobalEngine(
            slippage_tolerance=0.01, required_tags=("LEI",)
        )
        analysis = engine.run_best_ex_checks(
            self.record(regulatory_tags={"LEI": ""}, benchmark_price=150.0)
        )
        self.assertFalse(analysis.is_compliant)

    def test_negative_slippage_tolerance_rejected(self):
        with self.assertRaises(BestExRecordError):
            BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=-0.5)

    def test_non_finite_slippage_tolerance_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(tolerance=bad):
                with self.assertRaises(BestExRecordError):
                    BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=bad)

    def test_zero_benchmark_price_does_not_divide_by_zero(self):
        analysis = self.engine.run_best_ex_checks(self.record(benchmark_price=0.0))
        self.assertFalse(analysis.slippage_evaluated)
        self.assertTrue(any("positive" in v for v in analysis.violations))

    def test_negative_fill_quantity_is_flagged(self):
        analysis = self.engine.run_best_ex_checks(
            self.record(
                fills=[
                    {"price": 150.0, "qty": -50, "timestamp": iso(2)},
                    {"price": 150.0, "qty": 100, "timestamp": iso(3)},
                ]
            )
        )
        self.assertTrue(any("negative quantity" in v for v in analysis.violations))

    def test_non_traderecord_input_raises(self):
        with self.assertRaises(BestExRecordError):
            self.engine.run_best_ex_checks({"order_id": "X"})

    def test_whitespace_only_tag_counts_as_missing(self):
        """REGRESSION: '   ' is truthy, so a blank LEI used to satisfy a required tag."""
        engine = BestExecutionRecordKeepingGlobalEngine(
            slippage_tolerance=0.01, required_tags=("LEI",)
        )
        analysis = engine.run_best_ex_checks(
            self.record(regulatory_tags={"LEI": "   "}, benchmark_price=150.0)
        )
        self.assertFalse(analysis.is_compliant)
        self.assertTrue(any("LEI" in v for v in analysis.violations))


class TestMalformedRecordsStillReachTheLog(BaseCase):
    """
    REGRESSION: each of these raised an uncaught AttributeError/TypeError before the
    record was hashed or logged, so the malformed record vanished from the audit trail.
    """

    def assert_flagged_and_logged(self, engine, record, fragment):
        analysis = engine.run_best_ex_checks(record)
        self.assertFalse(analysis.is_compliant)
        self.assertNotEqual(analysis.record_hash, "")
        self.assertEqual(len(engine.audit_log), 1)
        self.assertEqual(engine.verify_audit_log(), [])
        self.assertTrue(
            any(fragment in v for v in analysis.violations),
            f"expected {fragment!r} in {analysis.violations}",
        )

    def test_non_string_side(self):
        self.assert_flagged_and_logged(self.engine, self.record(side=None), "side must be a string")

    def test_timestamps_not_a_dict(self):
        self.assert_flagged_and_logged(
            self.engine, self.record(timestamps=["2026-08-13T10:00:00+00:00"]), "must be a dict"
        )

    def test_timestamps_none(self):
        self.assert_flagged_and_logged(self.engine, self.record(timestamps=None), "must be a dict")

    def test_fills_not_a_list(self):
        self.assert_flagged_and_logged(
            self.engine, self.record(fills={"price": 150.0, "qty": 100}), "must be a list"
        )

    def test_regulatory_tags_none(self):
        self.assert_flagged_and_logged(
            self.engine, self.record(regulatory_tags=None), "Missing regulatory tags"
        )

    def test_non_json_native_value_does_not_prevent_hashing(self):
        """A datetime left in a fill used to raise TypeError inside the hash step."""
        engine = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        record = self.record(
            fills=[{"price": 150.0, "qty": 100, "timestamp": datetime(2026, 8, 13, tzinfo=timezone.utc)}],
            benchmark_price=150.0,
        )

        analysis = engine.run_best_ex_checks(record)

        self.assertNotEqual(analysis.record_hash, "")
        self.assertEqual(len(engine.audit_log), 1)
        # Stringification must be deterministic, or the chain would not verify.
        second = BestExecutionRecordKeepingGlobalEngine(slippage_tolerance=0.01)
        self.assertEqual(second.run_best_ex_checks(record).record_hash, analysis.record_hash)


class TestExport(BaseCase):

    def test_export_is_utf8_and_round_trips_non_ascii(self):
        """
        Without an explicit encoding this raises or mangles on a Windows default
        codepage. The symbol below is a realistic non-ASCII instrument name.
        """
        self.engine.run_best_ex_checks(
            self.record(symbol="NESTLÉ SA", venue="Börse München", benchmark_price=150.0)
        )

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            self.engine.export_audit_log(path)
            with open(path, encoding="utf-8") as fh:
                exported = json.load(fh)
        finally:
            os.unlink(path)

        self.assertEqual(exported[0]["record"]["symbol"], "NESTLÉ SA")
        self.assertEqual(exported[0]["record"]["venue"], "Börse München")
        self.assertEqual(exported[0]["entry_hash"], self.engine.head_hash)

    def test_exported_log_carries_the_chain(self):
        self.engine.run_best_ex_checks(self.record(order_id="A", benchmark_price=150.0))
        self.engine.run_best_ex_checks(self.record(order_id="B", benchmark_price=150.0))

        handle, path = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        try:
            self.engine.export_audit_log(path)
            with open(path, encoding="utf-8") as fh:
                exported = json.load(fh)
        finally:
            os.unlink(path)

        self.assertEqual(exported[1]["previous_entry_hash"], exported[0]["entry_hash"])
        self.assertIsInstance(BestExAnalysis(**{**exported[0]["analysis"]}), BestExAnalysis)


if __name__ == "__main__":
    unittest.main()
