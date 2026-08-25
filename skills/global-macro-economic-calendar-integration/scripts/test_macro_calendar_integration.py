import logging
import unittest
from datetime import datetime, timezone

from macro_calendar_integration import (
    BLOCKING_SEVERITIES,
    HIGH_IMPACT,
    LOW_IMPACT,
    MEDIUM_IMPACT,
    STATUS_BLACKOUT,
    STATUS_CALENDAR_STALE,
    STATUS_CALENDAR_UNAVAILABLE,
    STATUS_PERMITTED,
    GlobalMacroCalendarEngine,
    MacroCalendarAuditReport,
    MacroEconomicEvent,
    normalize_impact_severity,
    parse_release_timestamp,
    release_timestamp_from_local,
)

# Keep the engine's fail-closed ERROR lines out of the test output.
logging.getLogger("macro_calendar_integration").setLevel(logging.CRITICAL)

UTC = timezone.utc


class TestGlobalMacroCalendarEngine(unittest.TestCase):

    def setUp(self):
        self.engine = GlobalMacroCalendarEngine(pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0) # 15 min buffers
        # Scheduled Event: FOMC Rate Decision at T = 10,000
        self.fomc = MacroEconomicEvent(
            event_id="EVT_FOMC_01", event_name="FOMC_RATE_DECISION", currency="USD",
            release_timestamp_utc=10000.0, impact_severity="HIGH_IMPACT",
            consensus_forecast=5.25, forecast_std_dev=0.10, actual_release=5.50
        )
        self.engine.add_event(self.fomc)

    def test_blackout_active_during_pre_event_window(self):
        # Current time T = 9,400 (10 mins prior to release T=10,000) -> BLACKOUT ACTIVE!
        report = self.engine.audit_macro_trading_status(current_time_utc=9400.0)

        self.assertTrue(report.is_blackout_active)
        self.assertFalse(report.is_trading_permitted)
        self.assertTrue(report.should_cancel_open_limit_orders)
        self.assertEqual(report.status, "MACRO_BLACKOUT_ACTIVE")

    def test_trading_permitted_outside_blackout_window(self):
        # Current time T = 12,000 (33 mins after release T=10,000) -> CLEAR!
        report = self.engine.audit_macro_trading_status(current_time_utc=12000.0)

        self.assertFalse(report.is_blackout_active)
        self.assertTrue(report.is_trading_permitted)
        self.assertFalse(report.should_cancel_open_limit_orders)
        self.assertEqual(report.status, "MACRO_TRADING_PERMITTED")

    def test_surprise_index_calculation(self):
        # Actual 5.50 vs Forecast 5.25 (StdDev 0.10) -> (5.50 - 5.25) / 0.10 = +2.50
        surprise = self.engine.calculate_surprise_index(self.fomc)
        self.assertEqual(surprise, 2.5)

    # --- Window boundaries -----------------------------------------------------

    def test_blackout_bounds_are_inclusive_and_exclusive_one_second_outside(self):
        """Both window bounds are inclusive; one second beyond either is clear."""
        # Window is [10000 - 900, 10000 + 900] = [9100, 10900].
        self.assertTrue(self.engine.audit_macro_trading_status(9100.0).is_blackout_active)
        self.assertTrue(self.engine.audit_macro_trading_status(10900.0).is_blackout_active)
        self.assertTrue(self.engine.audit_macro_trading_status(9099.0).is_trading_permitted)
        self.assertTrue(self.engine.audit_macro_trading_status(10901.0).is_trading_permitted)

    def test_report_exposes_window_bounds_so_caller_knows_when_to_resume(self):
        report = self.engine.audit_macro_trading_status(9400.0)
        self.assertEqual(report.blackout_started_at_utc, 9100.0)
        self.assertEqual(report.blackout_ends_at_utc, 10900.0)
        self.assertEqual([e.event_id for e in report.active_blackout_events], ["EVT_FOMC_01"])

    def test_permitted_report_names_the_next_blocking_event(self):
        report = self.engine.audit_macro_trading_status(1000.0)
        self.assertIsNotNone(report.next_event)
        self.assertEqual(report.next_event.event_id, "EVT_FOMC_01")
        self.assertEqual(report.seconds_to_next_event, 9000.0)

    # --- Fail-closed behaviour -------------------------------------------------

    def test_empty_calendar_blocks_trading_instead_of_reporting_clear(self):
        """Regression: an unloaded calendar must not be indistinguishable from a clear one."""
        engine = GlobalMacroCalendarEngine()
        report = engine.audit_macro_trading_status(current_time_utc=10000.0)

        self.assertFalse(report.is_trading_permitted)
        self.assertEqual(report.status, STATUS_CALENDAR_UNAVAILABLE)
        self.assertTrue(report.should_cancel_open_limit_orders)
        # Not a blackout -- no event is known to be running. The two flags differ,
        # and a caller gating on is_blackout_active alone would trade through this.
        self.assertFalse(report.is_blackout_active)

    def test_empty_calendar_can_be_permitted_only_when_explicitly_opted_out(self):
        engine = GlobalMacroCalendarEngine(require_non_empty_calendar=False)
        self.assertTrue(engine.audit_macro_trading_status(10000.0).is_trading_permitted)

    def test_stale_calendar_blocks_trading(self):
        engine = GlobalMacroCalendarEngine(max_calendar_age_sec=3600.0)
        engine.replace_events([self.fomc], as_of_utc=0.0)

        # 3,600s old exactly -> still inside tolerance.
        self.assertTrue(engine.audit_macro_trading_status(3600.0).is_trading_permitted)
        # 3,601s old -> stale, blocked.
        stale = engine.audit_macro_trading_status(3601.0)
        self.assertFalse(stale.is_trading_permitted)
        self.assertEqual(stale.status, STATUS_CALENDAR_STALE)
        self.assertTrue(stale.should_cancel_open_limit_orders)

    def test_staleness_check_configured_without_an_as_of_timestamp_blocks(self):
        engine = GlobalMacroCalendarEngine(max_calendar_age_sec=3600.0)
        engine.add_event(self.fomc)
        report = engine.audit_macro_trading_status(3000.0)
        self.assertFalse(report.is_trading_permitted)
        self.assertEqual(report.status, STATUS_CALENDAR_STALE)

    def test_calendar_as_of_is_reported_back(self):
        engine = GlobalMacroCalendarEngine(calendar_as_of_utc=500.0)
        engine.add_event(self.fomc)
        self.assertEqual(engine.audit_macro_trading_status(1000.0).calendar_as_of_utc, 500.0)

    # --- Severity handling -----------------------------------------------------

    def test_unrecognised_severity_is_rejected_rather_than_silently_skipped(self):
        """Regression: a vendor severity string outside the vocabulary used to fail open."""
        for bad in ("HIGH", "high_impact ", "3", "CRITICAL", ""):
            with self.subTest(severity=bad):
                with self.assertRaises(ValueError):
                    self.engine.add_event(MacroEconomicEvent(
                        event_id=f"EVT_{bad or 'BLANK'}", event_name="US_CPI_YOY",
                        currency="USD", release_timestamp_utc=50000.0, impact_severity=bad,
                    ))

    def test_low_impact_events_never_open_a_blackout(self):
        engine = GlobalMacroCalendarEngine()
        engine.add_event(MacroEconomicEvent(
            event_id="EVT_LOW", event_name="US_WHOLESALE_INVENTORIES", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=LOW_IMPACT,
        ))
        self.assertTrue(engine.audit_macro_trading_status(10000.0).is_trading_permitted)
        self.assertNotIn(LOW_IMPACT, BLOCKING_SEVERITIES)

    def test_medium_impact_buffers_can_be_differentiated_from_high(self):
        engine = GlobalMacroCalendarEngine(
            pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0,
            medium_pre_event_buffer_sec=120.0, medium_post_event_buffer_sec=120.0,
        )
        medium = MacroEconomicEvent(
            event_id="EVT_MED", event_name="US_RETAIL_SALES", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=MEDIUM_IMPACT,
        )
        engine.add_event(medium)
        self.assertEqual(engine.blackout_window_for(medium), (9880.0, 10120.0))
        self.assertTrue(engine.audit_macro_trading_status(9880.0).is_blackout_active)
        self.assertTrue(engine.audit_macro_trading_status(9700.0).is_trading_permitted)

    def test_medium_impact_defaults_to_the_high_impact_buffers(self):
        engine = GlobalMacroCalendarEngine(pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0)
        medium = MacroEconomicEvent(
            event_id="EVT_MED", event_name="US_RETAIL_SALES", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=MEDIUM_IMPACT,
        )
        engine.add_event(medium)
        self.assertEqual(engine.blackout_window_for(medium), (9100.0, 10900.0))

    def test_normalize_impact_severity_maps_vendor_codes(self):
        # Trading Economics encodes Importance as 1 = low, 2 = medium, 3 = high.
        self.assertEqual(normalize_impact_severity(3), HIGH_IMPACT)
        self.assertEqual(normalize_impact_severity("3"), HIGH_IMPACT)
        self.assertEqual(normalize_impact_severity(2), MEDIUM_IMPACT)
        self.assertEqual(normalize_impact_severity(1), LOW_IMPACT)
        self.assertEqual(normalize_impact_severity("  high "), HIGH_IMPACT)
        self.assertEqual(normalize_impact_severity("High Impact"), HIGH_IMPACT)
        self.assertEqual(normalize_impact_severity("medium-impact"), MEDIUM_IMPACT)

    def test_normalize_impact_severity_rejects_unknown_codes(self):
        for bad in ("CRITICAL", 0, 4, "", None, True, 2.5):
            with self.subTest(code=bad):
                with self.assertRaises(ValueError):
                    normalize_impact_severity(bad)

    # --- Overlapping events ----------------------------------------------------

    def test_overlapping_blackouts_report_the_latest_close(self):
        """Regression: the engine used to stop at the earliest overlapping window.

        The previous implementation returned the first matching event and no
        window end, so a caller that derived its resume time from the reported
        event resumed at 10,900 while the RETAIL window was still open to
        11,500. The engine itself still blocked at 10,900 -- the defect was the
        misreported governing event and the absent end timestamp, not a
        fail-open.
        """
        engine = GlobalMacroCalendarEngine(pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0)
        engine.add_event(MacroEconomicEvent(
            event_id="EVT_CPI", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
        ))
        engine.add_event(MacroEconomicEvent(
            event_id="EVT_RETAIL", event_name="US_RETAIL_SALES", currency="USD",
            release_timestamp_utc=10600.0, impact_severity=HIGH_IMPACT,
        ))
        report = engine.audit_macro_trading_status(10050.0)

        self.assertTrue(report.is_blackout_active)
        self.assertEqual(len(report.active_blackout_events), 2)
        # 10,600 + 900 = 11,500, not the earlier event's 10,900.
        self.assertEqual(report.blackout_ends_at_utc, 11500.0)
        self.assertEqual(report.active_blackout_event.event_id, "EVT_RETAIL")
        # The governing window is reported, so a caller resuming from
        # blackout_ends_at_utc does not restart inside the RETAIL window.
        self.assertFalse(engine.audit_macro_trading_status(10901.0).is_trading_permitted)
        self.assertTrue(engine.audit_macro_trading_status(11501.0).is_trading_permitted)

    def test_fomc_press_conference_covered_by_a_post_event_override(self):
        """The statement lands at 2:00 p.m. ET; the press conference starts at 2:30 p.m. ET."""
        statement = release_timestamp_from_local("2026-01-28T14:00:00", "America/New_York")
        press_conf_start = statement + 1800.0
        engine = GlobalMacroCalendarEngine(pre_event_buffer_sec=900.0, post_event_buffer_sec=900.0)

        default_event = MacroEconomicEvent(
            event_id="EVT_FOMC_DEFAULT", event_name="FOMC_RATE_DECISION", currency="USD",
            release_timestamp_utc=statement, impact_severity=HIGH_IMPACT,
        )
        engine.add_event(default_event)
        # A 15-minute post buffer reopens trading 15 minutes before the press conference.
        self.assertTrue(engine.audit_macro_trading_status(press_conf_start).is_trading_permitted)

        engine.remove_event("EVT_FOMC_DEFAULT")
        engine.add_event(MacroEconomicEvent(
            event_id="EVT_FOMC_COVERED", event_name="FOMC_RATE_DECISION", currency="USD",
            release_timestamp_utc=statement, impact_severity=HIGH_IMPACT,
            post_event_buffer_override_sec=4500.0,  # through 3:15 p.m. ET
        ))
        self.assertFalse(engine.audit_macro_trading_status(press_conf_start).is_trading_permitted)
        self.assertFalse(
            engine.audit_macro_trading_status(press_conf_start + 1800.0).is_trading_permitted)

    # --- Surprise index --------------------------------------------------------

    def test_missing_std_dev_yields_no_standardised_index(self):
        """Regression: a missing StdDev used to be replaced by 1.0.

        That returned the raw unit difference labelled as a z-score, so a
        downstream ``abs(S) > 2`` threshold meant something different for every
        indicator.
        """
        cpi = MacroEconomicEvent(
            event_id="EVT_CPI", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
            consensus_forecast=3.2, forecast_std_dev=None, actual_release=3.4,
        )
        self.assertIsNone(self.engine.calculate_surprise_index(cpi))
        # The raw difference is still available -- correctly labelled and unrounded.
        self.assertAlmostEqual(self.engine.raw_surprise(cpi), 0.2, places=10)

    def test_standardised_index_uses_the_supplied_scale(self):
        # (3.4 - 3.2) / 0.15 = 1.3333...
        cpi = MacroEconomicEvent(
            event_id="EVT_CPI", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
            consensus_forecast=3.2, forecast_std_dev=0.15, actual_release=3.4,
        )
        self.assertEqual(self.engine.calculate_surprise_index(cpi), 1.3333)

    def test_inverted_indicator_flips_the_surprise_sign(self):
        """Unemployment printing above consensus is a negative surprise, not a positive one."""
        unemployment = MacroEconomicEvent(
            event_id="EVT_UNRATE", event_name="US_UNEMPLOYMENT_RATE", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
            consensus_forecast=4.2, forecast_std_dev=0.10, actual_release=4.4,
            higher_actual_is_positive_surprise=False,
        )
        # (4.4 - 4.2) / 0.10 = +2.0 before the sign convention is applied.
        self.assertEqual(self.engine.calculate_surprise_index(unemployment), -2.0)
        self.assertAlmostEqual(self.engine.raw_surprise(unemployment), -0.2, places=10)

    def test_surprise_is_not_visible_before_the_release(self):
        """Regression: the actual print used to be readable at any evaluation time."""
        # The calendar row already carries the actual; a backtest replaying it at
        # 09:00 must not see the 10:00 print.
        self.assertIsNone(self.engine.calculate_surprise_index(self.fomc, as_of_utc=9999.0))
        self.assertIsNone(self.engine.raw_surprise(self.fomc, as_of_utc=9999.0))
        self.assertEqual(self.engine.calculate_surprise_index(self.fomc, as_of_utc=10000.0), 2.5)

    def test_audit_withholds_the_surprise_during_the_pre_event_blackout(self):
        pre = self.engine.audit_macro_trading_status(9400.0)
        self.assertTrue(pre.is_blackout_active)
        self.assertIsNone(pre.macro_surprise_index)
        self.assertIsNone(pre.macro_surprise_raw)

        post = self.engine.audit_macro_trading_status(10500.0)
        self.assertTrue(post.is_blackout_active)
        self.assertEqual(post.macro_surprise_index, 2.5)

    def test_permitted_audit_reports_the_most_recent_released_surprise(self):
        """SKILL.md Verification: 60 minutes after the release, still S = +2.50."""
        report = self.engine.audit_macro_trading_status(13600.0)  # release + 3,600s
        self.assertEqual(report.status, STATUS_PERMITTED)
        self.assertEqual(report.macro_surprise_index, 2.5)
        self.assertEqual(report.surprise_source_event.event_id, "EVT_FOMC_01")

    def test_surprise_lookback_bounds_how_stale_a_reported_surprise_can_be(self):
        engine = GlobalMacroCalendarEngine(surprise_lookback_sec=3600.0)
        engine.add_event(self.fomc)
        self.assertEqual(engine.audit_macro_trading_status(13600.0).macro_surprise_index, 2.5)
        self.assertIsNone(engine.audit_macro_trading_status(13601.0).macro_surprise_index)

    def test_surprise_is_none_when_the_actual_has_not_printed(self):
        pending = MacroEconomicEvent(
            event_id="EVT_NFP", event_name="NFP_JOBS", currency="USD",
            release_timestamp_utc=90000.0, impact_severity=HIGH_IMPACT,
            consensus_forecast=180.0, forecast_std_dev=45.0,
        )
        self.assertIsNone(self.engine.calculate_surprise_index(pending))
        self.assertIsNone(self.engine.raw_surprise(pending))

    def test_non_finite_release_values_do_not_propagate(self):
        event = MacroEconomicEvent(
            event_id="EVT_NAN", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
            consensus_forecast=3.2, forecast_std_dev=0.15, actual_release=3.4,
        )
        event.actual_release = float("nan")  # e.g. a vendor field parsed after validation
        self.assertIsNone(self.engine.raw_surprise(event))
        self.assertIsNone(self.engine.calculate_surprise_index(event))

    def test_non_positive_std_dev_is_rejected_at_validation(self):
        for bad in (0.0, -0.1, float("nan"), float("inf")):
            with self.subTest(std_dev=bad):
                with self.assertRaises(ValueError):
                    self.engine.add_event(MacroEconomicEvent(
                        event_id="EVT_BAD_STD", event_name="US_CPI_YOY", currency="USD",
                        release_timestamp_utc=50000.0, impact_severity=HIGH_IMPACT,
                        consensus_forecast=3.2, forecast_std_dev=bad, actual_release=3.4,
                    ))

    # --- Currency scoping ------------------------------------------------------

    def test_currency_scope_restricts_which_events_can_block(self):
        engine = GlobalMacroCalendarEngine()
        engine.add_event(MacroEconomicEvent(
            event_id="EVT_BOJ", event_name="BOJ_RATE_DECISION", currency="JPY",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
        ))
        self.assertFalse(engine.audit_macro_trading_status(10000.0).is_trading_permitted)
        self.assertTrue(
            engine.audit_macro_trading_status(10000.0, relevant_currencies=["USD"]).is_trading_permitted)
        self.assertFalse(
            engine.audit_macro_trading_status(10000.0, relevant_currencies=["jpy"]).is_trading_permitted)

    def test_currency_scope_rejects_degenerate_inputs(self):
        with self.assertRaises(ValueError):
            self.engine.audit_macro_trading_status(10000.0, relevant_currencies=[])
        with self.assertRaises(ValueError):
            self.engine.audit_macro_trading_status(10000.0, relevant_currencies=["", "  "])
        # A bare string would iterate character by character and match nothing.
        with self.assertRaises(ValueError):
            self.engine.audit_macro_trading_status(10000.0, relevant_currencies="USD")

    # --- Calendar management ---------------------------------------------------

    def test_duplicate_event_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_event(MacroEconomicEvent(
                event_id="EVT_FOMC_01", event_name="FOMC_RATE_DECISION", currency="USD",
                release_timestamp_utc=99999.0, impact_severity=HIGH_IMPACT,
            ))

    def test_remove_event_drops_a_cancelled_release(self):
        self.assertTrue(self.engine.remove_event("EVT_FOMC_01"))
        self.assertFalse(self.engine.remove_event("EVT_FOMC_01"))
        self.assertEqual(self.engine.scheduled_events, [])

    def test_replace_events_leaves_the_calendar_untouched_when_validation_fails(self):
        bad = MacroEconomicEvent(
            event_id="EVT_BAD", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=float("nan"), impact_severity=HIGH_IMPACT,
        )
        with self.assertRaises(ValueError):
            self.engine.replace_events([bad], as_of_utc=1.0)
        self.assertEqual([e.event_id for e in self.engine.scheduled_events], ["EVT_FOMC_01"])
        self.assertIsNone(self.engine.calendar_as_of_utc)

    def test_replace_events_rejects_duplicate_ids(self):
        event = MacroEconomicEvent(
            event_id="EVT_DUP", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=10000.0, impact_severity=HIGH_IMPACT,
        )
        with self.assertRaises(ValueError):
            self.engine.replace_events([event, event])

    def test_events_are_kept_sorted_by_release_time(self):
        self.engine.add_event(MacroEconomicEvent(
            event_id="EVT_EARLY", event_name="US_CPI_YOY", currency="USD",
            release_timestamp_utc=500.0, impact_severity=HIGH_IMPACT,
        ))
        self.assertEqual(
            [e.event_id for e in self.engine.scheduled_events], ["EVT_EARLY", "EVT_FOMC_01"])

    # --- Input validation ------------------------------------------------------

    def test_constructor_rejects_unusable_buffers(self):
        for kwargs in (
            {"pre_event_buffer_sec": -1.0},
            {"post_event_buffer_sec": float("nan")},
            {"medium_pre_event_buffer_sec": -900.0},
            {"max_calendar_age_sec": 0.0},
            {"max_calendar_age_sec": -60.0},
            {"surprise_lookback_sec": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    GlobalMacroCalendarEngine(**kwargs)

    def test_non_finite_release_timestamp_is_rejected(self):
        """NaN compares False against both bounds, which reads as 'never in blackout'."""
        for bad in (float("nan"), float("inf")):
            with self.subTest(ts=bad):
                with self.assertRaises(ValueError):
                    self.engine.add_event(MacroEconomicEvent(
                        event_id=f"EVT_{bad}", event_name="US_CPI_YOY", currency="USD",
                        release_timestamp_utc=bad, impact_severity=HIGH_IMPACT,
                    ))

    def test_blank_identifiers_are_rejected(self):
        for kwargs in (
            {"event_id": "  "},
            {"event_name": ""},
            {"currency": ""},
        ):
            with self.subTest(**kwargs):
                base = {
                    "event_id": "EVT_X", "event_name": "US_CPI_YOY", "currency": "USD",
                    "release_timestamp_utc": 10000.0, "impact_severity": HIGH_IMPACT,
                }
                base.update(kwargs)
                with self.assertRaises(ValueError):
                    self.engine.add_event(MacroEconomicEvent(**base))

    def test_audit_rejects_a_non_finite_clock(self):
        for bad in (float("nan"), float("inf"), None, "10000"):
            with self.subTest(clock=bad):
                with self.assertRaises(ValueError):
                    self.engine.audit_macro_trading_status(current_time_utc=bad)

    def test_negative_buffer_override_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_event(MacroEconomicEvent(
                event_id="EVT_NEG", event_name="US_CPI_YOY", currency="USD",
                release_timestamp_utc=50000.0, impact_severity=HIGH_IMPACT,
                post_event_buffer_override_sec=-60.0,
            ))

    def test_add_event_rejects_a_non_event(self):
        with self.assertRaises(ValueError):
            self.engine.add_event({"event_id": "EVT_DICT"})


class TestTimestampHelpers(unittest.TestCase):

    def test_parse_release_timestamp_requires_an_explicit_offset(self):
        """Trading Economics documents Date as UTC but serialises it without a designator."""
        with self.assertRaises(ValueError):
            parse_release_timestamp("2023-03-30T00:00:00")
        # A naive value resolved against the host zone would shift the window silently.
        self.assertEqual(
            parse_release_timestamp("2023-03-30T00:00:00Z"),
            datetime(2023, 3, 30, 0, 0, tzinfo=UTC).timestamp(),
        )

    def test_parse_release_timestamp_accepts_equivalent_offsets(self):
        self.assertEqual(
            parse_release_timestamp("2026-01-28T19:00:00Z"),
            parse_release_timestamp("2026-01-28T14:00:00-05:00"),
        )

    def test_parse_release_timestamp_rejects_junk(self):
        for bad in ("", "   ", "not-a-date", None, 12345):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    parse_release_timestamp(bad)

    def test_local_release_time_tracks_daylight_saving(self):
        """2:00 p.m. ET is 19:00 UTC in January and 18:00 UTC in July.

        The FOMC publishes "For release at 2:00 p.m. EDT" (EST in winter) -- a
        fixed wall clock, not a fixed UTC instant. Expected values here are built
        directly from the UTC instants, not from the function under test.
        """
        january = release_timestamp_from_local("2026-01-28T14:00:00", "America/New_York")
        july = release_timestamp_from_local("2026-07-29T14:00:00", "America/New_York")
        self.assertEqual(january, datetime(2026, 1, 28, 19, 0, tzinfo=UTC).timestamp())
        self.assertEqual(july, datetime(2026, 7, 29, 18, 0, tzinfo=UTC).timestamp())
        # Hard-coding one UTC offset for both would be an hour wrong for one of them.
        self.assertNotEqual(january, datetime(2026, 1, 28, 18, 0, tzinfo=UTC).timestamp())

    def test_bls_0830_eastern_releases_track_daylight_saving_too(self):
        self.assertEqual(
            release_timestamp_from_local("2026-01-13T08:30:00", "America/New_York"),
            datetime(2026, 1, 13, 13, 30, tzinfo=UTC).timestamp(),
        )
        self.assertEqual(
            release_timestamp_from_local("2026-07-14T08:30:00", "America/New_York"),
            datetime(2026, 7, 14, 12, 30, tzinfo=UTC).timestamp(),
        )

    def test_ambiguous_local_time_is_rejected(self):
        # US DST ends on the first Sunday of November (15 U.S.C. 260a): 1 Nov 2026,
        # so 01:30 local occurs twice.
        with self.assertRaises(ValueError):
            release_timestamp_from_local("2026-11-01T01:30:00", "America/New_York")

    def test_non_existent_local_time_is_rejected(self):
        # US DST begins on the second Sunday of March: 8 Mar 2026, so 02:30 local
        # never happens.
        with self.assertRaises(ValueError):
            release_timestamp_from_local("2026-03-08T02:30:00", "America/New_York")

    def test_local_helper_rejects_an_offset_bearing_string_and_bad_zone(self):
        with self.assertRaises(ValueError):
            release_timestamp_from_local("2026-01-28T14:00:00-05:00", "America/New_York")
        with self.assertRaises(ValueError):
            release_timestamp_from_local("2026-01-28T14:00:00", "Mars/Olympus_Mons")
        with self.assertRaises(ValueError):
            release_timestamp_from_local("", "America/New_York")

    def test_us_and_eu_transition_dates_diverge(self):
        """For a few weeks a year the ET-to-CET gap is 5 hours, not the usual 6.

        The US switches on the second Sunday of March (15 U.S.C. 260a); the EU
        switches on the last Sunday of March (Directive 2000/84/EC). A calendar
        that hard-codes the offset between an ET and a CET release is wrong inside
        that gap.
        """
        def offset_hours(date_iso):
            ny = release_timestamp_from_local(f"{date_iso}T12:00:00", "America/New_York")
            berlin = release_timestamp_from_local(f"{date_iso}T12:00:00", "Europe/Berlin")
            return (ny - berlin) / 3600.0

        self.assertEqual(offset_hours("2026-03-01"), 6.0)   # both on standard time
        self.assertEqual(offset_hours("2026-03-20"), 5.0)   # US on DST, EU not yet
        self.assertEqual(offset_hours("2026-04-15"), 6.0)   # both on DST


class TestReportContract(unittest.TestCase):

    def test_report_is_constructible_from_the_pre_2_0_0_positional_fields(self):
        """The appended fields all default, so existing construction keeps working."""
        report = MacroCalendarAuditReport(
            0.0, True, False, None, False, None, STATUS_PERMITTED, "note")
        self.assertEqual(report.active_blackout_events, [])
        self.assertIsNone(report.blackout_ends_at_utc)

    def test_each_report_gets_its_own_event_list(self):
        first = MacroCalendarAuditReport(0.0, True, False, None, False, None, STATUS_PERMITTED, "a")
        second = MacroCalendarAuditReport(0.0, True, False, None, False, None, STATUS_PERMITTED, "b")
        first.active_blackout_events.append("x")
        self.assertEqual(second.active_blackout_events, [])

    def test_blackout_status_constants_are_distinct(self):
        self.assertEqual(
            len({STATUS_PERMITTED, STATUS_BLACKOUT, STATUS_CALENDAR_STALE,
                 STATUS_CALENDAR_UNAVAILABLE}), 4)


if __name__ == '__main__':
    unittest.main()
