"""Unit tests for the instrument universe change detection engine.

Identifiers used here are real and were resolved against the OpenFIGI mapping
API (api.openfigi.com/v3/mapping) rather than invented:

    BBG000MM2P62  META  Meta Platforms Inc-Class A (US composite)
    BBG002GPKKZ7  META  Meta Platforms Inc-Class A (UW / Nasdaq exchange level)
    BBG000B9XRY4  AAPL  Apple Inc (US composite)
    BBG000B9Y5X2  AAPL  Apple Inc (UW / Nasdaq exchange level)
    BBG000N7QR55  PLTR  Palantir Technologies Inc-A (US composite)
    BBG000H6HNW3  TWTR  Twitter Inc (US composite, delisted 2022)
    BBG01VRMNFB1  FB    ProShares S&P Dynamic Buffer ETF - the recycled 'FB'
                        ticker, which is why diffs must never key on tickers.

ISIN check-digit fixtures are taken from the ANNA ISIN Uniform Guidelines 2025
(FTSE 100 GB0001383545, IBEX 35 ES0SI0000005, S&P 500 US78378X1072) and from
OpenFIGI (Apple US0378331005, Meta US30303M1027).
"""

import logging
import unittest
from datetime import date

from universe_change_detection import (
    ACTION_FREEZE_TRADING,
    ACTION_HOLD_FOR_MANUAL_REVIEW,
    ACTION_INITIATE_COVERAGE,
    ACTION_LIQUIDATE,
    ACTION_RESUME_ELIGIBILITY,
    ACTION_REVIEW_STATUS_CHANGE,
    ACTION_UPDATE_ROUTING,
    ACTION_UPDATE_SYMBOL_MAPPER,
    CHANGE_ADDITION,
    CHANGE_DELETION,
    CHANGE_EXCHANGE_MIGRATION,
    CHANGE_STATUS_CHANGE,
    CHANGE_TICKER_RENAME,
    ID_SCHEME_FIGI,
    ID_SCHEME_ISIN,
    REPORT_CHANGES_DETECTED,
    REPORT_NO_CHANGES,
    REPORT_SNAPSHOT_SUSPECT,
    InstrumentRecord,
    UniverseChangeDetectionEngine,
    is_valid_figi,
    is_valid_isin,
)

# Keep expected warning output off the test runner's stderr.
logging.getLogger("universe_change_detection").setLevel(logging.CRITICAL)

META = "BBG000MM2P62"
AAPL = "BBG000B9XRY4"
PLTR = "BBG000N7QR55"
TWTR = "BBG000H6HNW3"


def filler(count):
    """A block of distinct instruments, so deletion ratios stay realistic.

    The identifiers are structurally valid FIGIs (consonants/digits only) but
    are synthetic - they are not allocated identifiers.
    """
    return [
        InstrumentRecord(
            f"BBG000TST{index:02d}0", f"FILL{index:02d}", f"Filler {index}", "NASDAQ"
        )
        for index in range(count)
    ]


class TestIdentifierValidation(unittest.TestCase):
    """FIGI/ISIN validators, against independently sourced identifiers."""

    def test_real_figis_accepted(self):
        for figi in (META, AAPL, PLTR, TWTR, "BBG000B9Y5X2", "BBG01VRMNFB1"):
            self.assertTrue(is_valid_figi(figi), figi)

    def test_tickers_and_malformed_figis_rejected(self):
        for value in ("FB", "META", "AAPL", "BBG000B9XRY", "BBG000B9XRYA",
                      "BAG000B9XRY4", "US0378331005", "", None, 12345):
            self.assertFalse(is_valid_figi(value), repr(value))

    def test_figi_validation_is_structural_only(self):
        # BBG000MM82B1 is well-formed but is not an allocated FIGI (OpenFIGI
        # returns "No identifier found"). Structure is not existence.
        self.assertTrue(is_valid_figi("BBG000MM82B1"))

    def test_real_isins_accepted(self):
        for isin in ("US30303M1027", "US0378331005", "GB0001383545",
                     "ES0SI0000005", "US78378X1072"):
            self.assertTrue(is_valid_isin(isin), isin)

    def test_isin_check_digit_and_shape_enforced(self):
        for value in ("US30303M1028", "US0378331006", "US30303M102",
                      "us30303m1027", "AAPL", "", None, 12345):
            self.assertFalse(is_valid_isin(value), repr(value))


class TestConstructorValidation(unittest.TestCase):

    def test_rejects_unknown_id_scheme(self):
        with self.assertRaises(ValueError):
            UniverseChangeDetectionEngine(id_scheme="CUSIP")

    def test_rejects_out_of_range_deletion_ratio(self):
        for ratio in (-0.01, 1.5):
            with self.assertRaises(ValueError):
                UniverseChangeDetectionEngine(max_deletion_ratio=ratio)

    def test_rejects_non_numeric_deletion_ratio(self):
        for ratio in ("0.1", None, True):
            with self.assertRaises(TypeError):
                UniverseChangeDetectionEngine(max_deletion_ratio=ratio)


class TestCoreDeltaClassification(unittest.TestCase):

    def setUp(self):
        self.engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_FIGI)

    def test_rename_addition_and_deletion_classified_by_permanent_id(self):
        previous = filler(20) + [
            InstrumentRecord(META, "FB", "Facebook Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", "ACTIVE"),
        ]
        current = filler(20) + [
            InstrumentRecord(META, "META", "Meta Platforms Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc", "NASDAQ", "ACTIVE"),
        ]

        report = self.engine.detect_universe_changes(previous, current)

        self.assertEqual(report.status, REPORT_CHANGES_DETECTED)
        self.assertFalse(report.snapshot_is_suspect)
        self.assertEqual(report.additions_count, 1)
        self.assertEqual(report.deletions_count, 1)
        self.assertEqual(report.renames_count, 1)
        self.assertEqual(report.status_changes_count, 0)
        self.assertEqual(report.exchange_migrations_count, 0)
        self.assertEqual(report.total_previous_count, 23)
        self.assertEqual(report.total_current_count, 23)

        by_type = {alert.change_type: alert for alert in report.alerts}
        self.assertEqual(by_type[CHANGE_ADDITION].recommended_action, ACTION_INITIATE_COVERAGE)
        self.assertEqual(by_type[CHANGE_ADDITION].new_ticker, "PLTR")
        self.assertEqual(by_type[CHANGE_DELETION].recommended_action, ACTION_LIQUIDATE)
        self.assertEqual(by_type[CHANGE_DELETION].previous_ticker, "TWTR")

        rename = by_type[CHANGE_TICKER_RENAME]
        self.assertEqual(rename.recommended_action, ACTION_UPDATE_SYMBOL_MAPPER)
        self.assertEqual(rename.permanent_id, META)
        self.assertEqual((rename.previous_ticker, rename.new_ticker), ("FB", "META"))
        # The rename must NOT be double-counted as a delete of FB plus an add of META.
        self.assertEqual(len(report.alerts), 3)

    def test_identical_snapshots_report_no_changes(self):
        snapshot = [InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "ACTIVE")]
        report = self.engine.detect_universe_changes(snapshot, snapshot)

        self.assertEqual(report.status, REPORT_NO_CHANGES)
        self.assertEqual(report.alerts, [])
        self.assertEqual(report.deletion_ratio, 0.0)
        self.assertFalse(report.snapshot_is_suspect)

    def test_ticker_case_and_padding_is_not_a_rename(self):
        previous = [InstrumentRecord(AAPL, "aapl ", "Apple Inc", "nasdaq", "active")]
        current = [InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "ACTIVE")]

        report = self.engine.detect_universe_changes(previous, current)

        self.assertEqual(report.renames_count, 0)
        self.assertEqual(report.status_changes_count, 0)
        self.assertEqual(report.exchange_migrations_count, 0)
        self.assertEqual(report.status, REPORT_NO_CHANGES)

    def test_asset_name_change_alone_raises_no_alert(self):
        # A company renaming itself does not change the FIGI or the tradability
        # of the instrument (FIGI Allocation Rules Sec. 3.2.5).
        previous = [InstrumentRecord(META, "META", "Facebook Inc", "NASDAQ")]
        current = [InstrumentRecord(META, "META", "Meta Platforms Inc", "NASDAQ")]

        report = self.engine.detect_universe_changes(previous, current)

        self.assertEqual(report.alerts, [])
        self.assertEqual(report.status, REPORT_NO_CHANGES)

    def test_bootstrap_against_empty_previous_universe_is_all_additions(self):
        current = [InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ")]

        report = self.engine.detect_universe_changes([], current)

        self.assertEqual(report.additions_count, 1)
        self.assertEqual(report.deletion_ratio, 0.0)
        self.assertFalse(report.snapshot_is_suspect)
        self.assertEqual(report.status, REPORT_CHANGES_DETECTED)

    def test_alerts_are_ordered_risk_reducing_first(self):
        previous = filler(30) + [
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(META, "FB", "Facebook Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", "ACTIVE"),
        ]
        current = filler(30) + [
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "HALTED"),
            InstrumentRecord(META, "META", "Meta Platforms Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc", "NASDAQ", "ACTIVE"),
        ]

        report = self.engine.detect_universe_changes(previous, current)
        ordering = [alert.change_type for alert in report.alerts]

        self.assertEqual(
            ordering,
            [CHANGE_DELETION, CHANGE_STATUS_CHANGE, CHANGE_TICKER_RENAME, CHANGE_ADDITION],
        )


class TestStatusTransitions(unittest.TestCase):
    """Regression tests: every transition used to map to FREEZE_TRADING_ALERTS."""

    def setUp(self):
        self.engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_FIGI)

    def _single_status_alert(self, previous_status, new_status):
        previous = [InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", previous_status)]
        current = [InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", new_status)]
        report = self.engine.detect_universe_changes(previous, current)
        self.assertEqual(report.status_changes_count, 1)
        self.assertEqual(len(report.alerts), 1)
        self.assertEqual(report.alerts[0].change_type, CHANGE_STATUS_CHANGE)
        return report.alerts[0]

    def test_active_to_delisted_requires_liquidation_not_a_freeze(self):
        alert = self._single_status_alert("ACTIVE", "DELISTED")
        self.assertEqual(alert.recommended_action, ACTION_LIQUIDATE)
        self.assertIn("merger completion", alert.audit_notes)

    def test_halted_to_delisted_requires_liquidation(self):
        alert = self._single_status_alert("HALTED", "DELISTED")
        self.assertEqual(alert.recommended_action, ACTION_LIQUIDATE)

    def test_active_to_halted_freezes_trading(self):
        alert = self._single_status_alert("ACTIVE", "HALTED")
        self.assertEqual(alert.recommended_action, ACTION_FREEZE_TRADING)

    def test_active_to_suspended_freezes_trading(self):
        alert = self._single_status_alert("ACTIVE", "SUSPENDED")
        self.assertEqual(alert.recommended_action, ACTION_FREEZE_TRADING)

    def test_halted_to_active_resumes_rather_than_freezing(self):
        alert = self._single_status_alert("HALTED", "ACTIVE")
        self.assertEqual(alert.recommended_action, ACTION_RESUME_ELIGIBILITY)
        self.assertNotEqual(alert.recommended_action, ACTION_FREEZE_TRADING)

    def test_unrecognised_status_is_routed_to_review_not_guessed(self):
        alert = self._single_status_alert("ACTIVE", "PRE_OPEN_ONLY")
        self.assertEqual(alert.recommended_action, ACTION_REVIEW_STATUS_CHANGE)
        self.assertIn("outside the recognised vocabulary", alert.audit_notes)

    def test_status_change_is_case_insensitive(self):
        previous = [InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", "active")]
        current = [InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE", "ACTIVE")]
        report = self.engine.detect_universe_changes(previous, current)
        self.assertEqual(report.status_changes_count, 0)

    def test_addition_in_non_tradable_status_does_not_initiate_coverage(self):
        current = [InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc",
                                    "NASDAQ", "HALTED")]
        report = self.engine.detect_universe_changes([], current)

        self.assertEqual(report.additions_count, 1)
        self.assertEqual(report.alerts[0].recommended_action, ACTION_REVIEW_STATUS_CHANGE)


class TestVenueMigration(unittest.TestCase):

    def test_exchange_change_emits_routing_update(self):
        engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_FIGI)
        previous = [InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc", "NYSE")]
        current = [InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc", "NASDAQ")]

        report = engine.detect_universe_changes(previous, current)

        self.assertEqual(report.exchange_migrations_count, 1)
        self.assertEqual(report.renames_count, 0)
        alert = report.alerts[0]
        self.assertEqual(alert.change_type, CHANGE_EXCHANGE_MIGRATION)
        self.assertEqual(alert.recommended_action, ACTION_UPDATE_ROUTING)


class TestChurnGuard(unittest.TestCase):
    """A truncated vendor file must never be actioned as a mass delisting."""

    def test_empty_current_snapshot_suppresses_every_liquidation(self):
        engine = UniverseChangeDetectionEngine()
        previous = filler(50)

        report = engine.detect_universe_changes(previous, [])

        self.assertTrue(report.snapshot_is_suspect)
        self.assertEqual(report.status, REPORT_SNAPSHOT_SUSPECT)
        self.assertEqual(report.deletions_count, 50)
        self.assertEqual(report.deletion_ratio, 1.0)
        self.assertEqual(len(report.alerts), 50)
        for alert in report.alerts:
            self.assertTrue(alert.requires_manual_review)
            self.assertEqual(alert.recommended_action, ACTION_HOLD_FOR_MANUAL_REVIEW)
            self.assertEqual(alert.suppressed_action, ACTION_LIQUIDATE)
        self.assertNotIn(
            ACTION_LIQUIDATE, [alert.recommended_action for alert in report.alerts]
        )

    def test_empty_current_is_suspect_even_when_ratio_guard_is_disabled(self):
        engine = UniverseChangeDetectionEngine(max_deletion_ratio=1.0)
        report = engine.detect_universe_changes(filler(4), [])

        self.assertTrue(report.snapshot_is_suspect)
        self.assertEqual(report.alerts[0].recommended_action, ACTION_HOLD_FOR_MANUAL_REVIEW)

    def test_deletion_ratio_exactly_at_threshold_is_not_suspect(self):
        engine = UniverseChangeDetectionEngine(max_deletion_ratio=0.10)
        previous = filler(10)
        report = engine.detect_universe_changes(previous, previous[:9])

        self.assertAlmostEqual(report.deletion_ratio, 0.10)
        self.assertFalse(report.snapshot_is_suspect)
        self.assertEqual(report.status, REPORT_CHANGES_DETECTED)
        self.assertEqual(report.alerts[0].recommended_action, ACTION_LIQUIDATE)
        self.assertEqual(report.alerts[0].suppressed_action, "")

    def test_deletion_ratio_just_above_threshold_is_suspect(self):
        engine = UniverseChangeDetectionEngine(max_deletion_ratio=0.10)
        previous = filler(10)
        report = engine.detect_universe_changes(previous, previous[:8])

        self.assertAlmostEqual(report.deletion_ratio, 0.20)
        self.assertTrue(report.snapshot_is_suspect)
        for alert in report.alerts:
            self.assertEqual(alert.recommended_action, ACTION_HOLD_FOR_MANUAL_REVIEW)

    def test_small_universe_trips_the_default_guard(self):
        # A three-name universe losing one name is a 33% deletion ratio: the
        # default threshold is calibrated for a large universe and must be
        # re-tuned for a small one.
        engine = UniverseChangeDetectionEngine()
        previous = [
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ"),
            InstrumentRecord(META, "META", "Meta Platforms Inc", "NASDAQ"),
            InstrumentRecord(TWTR, "TWTR", "Twitter Inc", "NYSE"),
        ]
        report = engine.detect_universe_changes(previous, previous[:2])

        self.assertTrue(report.snapshot_is_suspect)

    def test_suspect_snapshot_is_logged_as_a_warning(self):
        engine = UniverseChangeDetectionEngine(max_deletion_ratio=0.10)
        previous = filler(10)

        with self.assertLogs("universe_change_detection", level="WARNING") as captured:
            engine.detect_universe_changes(previous, previous[:5])

        self.assertTrue(any("SNAPSHOT SUSPECT" in line for line in captured.output))

    def test_additions_are_suppressed_alongside_deletions(self):
        engine = UniverseChangeDetectionEngine(max_deletion_ratio=0.10)
        previous = filler(10)
        current = previous[:5] + [InstrumentRecord(PLTR, "PLTR", "Palantir", "NASDAQ")]

        report = engine.detect_universe_changes(previous, current)

        self.assertTrue(report.snapshot_is_suspect)
        addition = [a for a in report.alerts if a.change_type == CHANGE_ADDITION][0]
        self.assertEqual(addition.recommended_action, ACTION_HOLD_FOR_MANUAL_REVIEW)
        self.assertEqual(addition.suppressed_action, ACTION_INITIATE_COVERAGE)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = UniverseChangeDetectionEngine()

    def test_blank_permanent_id_rejected(self):
        for blank in ("", "   "):
            with self.assertRaises(ValueError):
                InstrumentRecord(blank, "AAPL", "Apple Inc", "NASDAQ")

    def test_non_string_fields_rejected(self):
        with self.assertRaises(TypeError):
            InstrumentRecord(None, "AAPL", "Apple Inc", "NASDAQ")
        with self.assertRaises(TypeError):
            InstrumentRecord(AAPL, 123, "Apple Inc", "NASDAQ")

    def test_blank_status_rejected(self):
        with self.assertRaises(ValueError):
            InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ", "  ")

    def test_duplicate_permanent_ids_rejected(self):
        # One ISIN covers every venue on which a fungible security trades
        # (ANNA ISIN Uniform Guidelines Sec. 1.1), so an ISIN-keyed multi-venue
        # snapshot collides. Silently keeping the last row would hide a listing.
        snapshot = [
            InstrumentRecord("US30303M1027", "META", "Meta Platforms Inc", "NASDAQ"),
            InstrumentRecord("US30303M1027", "FB2A", "Meta Platforms Inc", "XETRA"),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.detect_universe_changes(snapshot, snapshot)
        self.assertIn("duplicate permanent identifiers", str(ctx.exception))

    def test_non_record_elements_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.detect_universe_changes(
                [{"permanent_id": AAPL, "ticker_symbol": "AAPL"}], []
            )

    def test_none_snapshot_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.detect_universe_changes(None, [])

    def test_figi_scheme_rejects_a_ticker_keyed_snapshot(self):
        engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_FIGI)
        snapshot = [InstrumentRecord("AAPL", "AAPL", "Apple Inc", "NASDAQ")]

        with self.assertRaises(ValueError) as ctx:
            engine.detect_universe_changes(snapshot, snapshot)
        self.assertIn("not a structurally valid FIGI", str(ctx.exception))

    def test_isin_scheme_rejects_a_bad_check_digit(self):
        engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_ISIN)
        good = [InstrumentRecord("US30303M1027", "META", "Meta Platforms Inc", "NASDAQ")]
        bad = [InstrumentRecord("US30303M1028", "META", "Meta Platforms Inc", "NASDAQ")]

        self.assertEqual(engine.detect_universe_changes(good, good).status, REPORT_NO_CHANGES)
        with self.assertRaises(ValueError):
            engine.detect_universe_changes(good, bad)

    def test_opaque_scheme_accepts_in_house_identifiers(self):
        snapshot = [InstrumentRecord("INTERNAL-000123", "AAPL", "Apple Inc", "NASDAQ")]
        report = self.engine.detect_universe_changes(snapshot, snapshot)
        self.assertEqual(report.status, REPORT_NO_CHANGES)


class TestSnapshotOrdering(unittest.TestCase):

    def setUp(self):
        self.engine = UniverseChangeDetectionEngine(id_scheme=ID_SCHEME_FIGI)
        self.previous = [InstrumentRecord(AAPL, "AAPL", "Apple Inc", "NASDAQ")]
        self.current = self.previous + [
            InstrumentRecord(PLTR, "PLTR", "Palantir Technologies Inc", "NASDAQ")
        ]

    def test_reversed_as_of_dates_are_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.detect_universe_changes(
                self.previous, self.current, date(2026, 8, 25), date(2026, 8, 24)
            )
        self.assertIn("strictly later", str(ctx.exception))

    def test_equal_as_of_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.detect_universe_changes(
                self.previous, self.current, date(2026, 8, 25), date(2026, 8, 25)
            )

    def test_ordered_as_of_dates_are_recorded_in_the_audit_note(self):
        report = self.engine.detect_universe_changes(
            self.previous, self.current, date(2026, 8, 24), date(2026, 8, 25)
        )
        self.assertIn("2026-08-24 -> 2026-08-25", report.audit_notes)
        self.assertEqual(report.additions_count, 1)

    def test_non_date_as_of_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.detect_universe_changes(
                self.previous, self.current, "2026-08-24", date(2026, 8, 25)
            )


if __name__ == "__main__":
    unittest.main()
