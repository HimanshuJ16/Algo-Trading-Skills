import datetime
import logging
import unittest

from daylight_saving_time_transition_handling import (
    DstScheduleError,
    DstTransitionAuditReport,
    DstTransitionHandlerEngine,
    ExchangeScheduleSpec,
    UtcSessionWindow,
)

EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def setUpModule():
    """The engine warns by design on every transition date; keep the suite output quiet."""
    logging.getLogger("daylight_saving_time_transition_handling").addHandler(logging.NullHandler())
    logging.getLogger("daylight_saving_time_transition_handling").propagate = False


def _independent_utc_ns(iso_utc: str) -> int:
    """Nanosecond epoch derived by integer arithmetic, independent of the implementation."""
    dt = datetime.datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    return (dt - EPOCH) // datetime.timedelta(microseconds=1) * 1000


class TestDstTransitionHandlerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DstTransitionHandlerEngine()
        # NYSE: New York (09:30 - 16:00)
        self.engine.register_exchange(
            ExchangeScheduleSpec("NYSE", "New York Stock Exchange", "America/New_York", "09:30", "16:00")
        )
        # LSE: London (08:00 - 16:30)
        self.engine.register_exchange(
            ExchangeScheduleSpec("LSE", "London Stock Exchange", "Europe/London", "08:00", "16:30")
        )

    # ------------------------------------------------------------------
    # Core UTC conversion
    # ------------------------------------------------------------------

    def test_march_us_eu_dst_desync_window(self):
        # 2026-03-15: the US moved to EDT on 2026-03-08 (2nd Sunday, 15 U.S.C. 260a) but the
        # EU does not start summer time until 2026-03-29 (last Sunday, Directive 2000/84/EC).
        report = self.engine.audit_global_dst_transitions("2026-03-15")

        self.assertTrue(report.is_us_eu_desync_window)
        self.assertGreater(len(report.warnings), 0)

        nyse_sess = next(s for s in report.sessions if s.exchange_id == "NYSE")
        lse_sess = next(s for s in report.sessions if s.exchange_id == "LSE")

        # NYSE Open in EDT (UTC-4) at 09:30 local -> 13:30Z
        self.assertEqual(nyse_sess.utc_open_iso, "2026-03-15T13:30:00Z")
        self.assertTrue(nyse_sess.is_dst_active)
        # LSE Open in GMT (UTC+0) at 08:00 local -> 08:00Z
        self.assertEqual(lse_sess.utc_open_iso, "2026-03-15T08:00:00Z")
        self.assertFalse(lse_sess.is_dst_active)

        # During the desync window the US sits 4h behind UTC and the EU at UTC.
        self.assertEqual(report.us_eu_offset_delta_hours, -4.0)

    def test_april_aligned_dst_session(self):
        # 2026-04-15: both US (EDT, UTC-4) and EU (BST, UTC+1) are on summer time.
        report = self.engine.audit_global_dst_transitions("2026-04-15")

        self.assertFalse(report.is_us_eu_desync_window)
        nyse_sess = next(s for s in report.sessions if s.exchange_id == "NYSE")
        lse_sess = next(s for s in report.sessions if s.exchange_id == "LSE")

        self.assertTrue(nyse_sess.is_dst_active)
        self.assertTrue(lse_sess.is_dst_active)
        # Aligned periods keep the canonical 5-hour transatlantic gap.
        self.assertEqual(report.us_eu_offset_delta_hours, -5.0)

    def test_utc_nanosecond_epoch_matches_independent_derivation(self):
        window = self.engine.calculate_utc_session("NYSE", "2026-03-15")
        self.assertEqual(window.utc_open_ns, _independent_utc_ns("2026-03-15T13:30:00Z"))
        self.assertEqual(window.utc_close_ns, _independent_utc_ns("2026-03-15T20:00:00Z"))
        self.assertEqual(window.session_duration_hours, 6.5)

    def test_winter_standard_time_alignment(self):
        # 2026-01-15: both regions on standard time -> aligned, canonical -5h gap.
        report = self.engine.audit_global_dst_transitions("2026-01-15")
        self.assertFalse(report.is_us_eu_desync_window)
        self.assertEqual(report.us_eu_offset_delta_hours, -5.0)
        nyse_sess = next(s for s in report.sessions if s.exchange_id == "NYSE")
        self.assertEqual(nyse_sess.utc_open_iso, "2026-01-15T14:30:00Z")  # EST, UTC-5

    # ------------------------------------------------------------------
    # Desync window boundaries and length
    # ------------------------------------------------------------------

    def test_spring_desync_window_boundaries_2026(self):
        # 2026 spring window: 2026-03-08 (US starts) .. 2026-03-29 (EU starts) = 21 days,
        # NOT the "two weeks" that a fixed-length assumption would predict.
        self.assertFalse(self.engine.audit_global_dst_transitions("2026-03-07").is_us_eu_desync_window)
        self.assertTrue(self.engine.audit_global_dst_transitions("2026-03-08").is_us_eu_desync_window)
        self.assertTrue(self.engine.audit_global_dst_transitions("2026-03-28").is_us_eu_desync_window)
        self.assertFalse(self.engine.audit_global_dst_transitions("2026-03-29").is_us_eu_desync_window)

        desync_days = sum(
            self.engine.audit_global_dst_transitions(
                (datetime.date(2026, 3, 1) + datetime.timedelta(days=i)).isoformat()
            ).is_us_eu_desync_window
            for i in range(31)
        )
        self.assertEqual(desync_days, 21)

    def test_spring_desync_window_is_fourteen_days_in_2027(self):
        # 2027: US starts 2027-03-14, EU starts 2027-03-28 -> 14 days. The window length is
        # year-dependent, so it must never be hard-coded.
        desync_days = sum(
            self.engine.audit_global_dst_transitions(
                (datetime.date(2027, 3, 1) + datetime.timedelta(days=i)).isoformat()
            ).is_us_eu_desync_window
            for i in range(31)
        )
        self.assertEqual(desync_days, 14)

    def test_autumn_desync_window_is_always_seven_days(self):
        # EU ends summer time on the last Sunday of October, the US on the 1st Sunday of
        # November -> exactly 7 days, in every year. The EU is then on standard time while
        # the US is still on DST, i.e. the mirror image of the March window.
        for year, start, end in ((2026, "2026-10-25", "2026-11-01"), (2027, "2027-10-31", "2027-11-07")):
            with self.subTest(year=year):
                start_date = datetime.date.fromisoformat(start)
                end_date = datetime.date.fromisoformat(end)
                self.assertEqual((end_date - start_date).days, 7)
                self.assertTrue(self.engine.audit_global_dst_transitions(start).is_us_eu_desync_window)
                self.assertFalse(self.engine.audit_global_dst_transitions(end).is_us_eu_desync_window)
                report = self.engine.audit_global_dst_transitions(start)
                nyse = next(s for s in report.sessions if s.exchange_id == "NYSE")
                lse = next(s for s in report.sessions if s.exchange_id == "LSE")
                self.assertTrue(nyse.is_dst_active)
                self.assertFalse(lse.is_dst_active)
                # US on DST, EU on standard time -> the gap narrows to 4 hours.
                self.assertEqual(report.us_eu_offset_delta_hours, -4.0)

    def test_desync_shifts_transatlantic_overlap_by_one_hour(self):
        # The practical consequence: the NYSE/LSE UTC overlap gains an hour in the window.
        # Aligned: LSE 07:00-15:30Z (BST) vs NYSE 13:30-20:00Z (EDT) -> 13:30-15:30 = 2.0h.
        # Desync: LSE 08:00-16:30Z (GMT) vs NYSE 13:30-20:00Z (EDT) -> 13:30-16:30 = 3.0h.
        aligned = self.engine.audit_global_dst_transitions("2026-04-15").us_eu_overlap_hours
        desync = self.engine.audit_global_dst_transitions("2026-03-15").us_eu_overlap_hours
        self.assertEqual(aligned, 2.0)
        self.assertEqual(desync, 3.0)
        self.assertEqual(round(desync - aligned, 2), 1.0)

    # ------------------------------------------------------------------
    # Regression: audit must not silently no-op on non-legacy exchange ids
    # ------------------------------------------------------------------

    def test_audit_resolves_legs_for_mic_coded_exchange_ids(self):
        # Regression: the audit previously matched only the literal ids "NYSE"/"LSE", so a
        # repo-conventional MIC registration returned is_desync=False on a real desync date.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XNYS", "NYSE", "America/New_York", "09:30", "16:00"))
        engine.register_exchange(ExchangeScheduleSpec("XLON", "LSE", "Europe/London", "08:00", "16:30"))

        report = engine.audit_global_dst_transitions("2026-03-15")
        self.assertTrue(report.is_us_eu_desync_window)
        self.assertEqual(report.us_exchange_id, "XNYS")
        self.assertEqual(report.eu_exchange_id, "XLON")
        self.assertGreater(report.us_eu_overlap_hours, 0.0)

    def test_audit_honours_explicit_leg_overrides(self):
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XCME", "CME", "America/Chicago", "08:30", "15:15"))
        engine.register_exchange(ExchangeScheduleSpec("XETR", "Xetra", "Europe/Berlin", "09:00", "17:30"))

        report = engine.audit_global_dst_transitions(
            "2026-03-15", us_exchange_id="XCME", eu_exchange_id="XETR"
        )
        self.assertTrue(report.is_us_eu_desync_window)
        self.assertEqual(report.us_exchange_id, "XCME")
        self.assertEqual(report.eu_exchange_id, "XETR")

        with self.assertRaises(DstScheduleError):
            engine.audit_global_dst_transitions("2026-03-15", us_exchange_id="NOPE")

    def test_audit_warns_instead_of_silently_reporting_no_desync(self):
        # Only an Asian exchange registered: neither leg resolves, so the report must say so
        # rather than presenting is_us_eu_desync_window=False as a clean result.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XTKS", "TSE", "Asia/Tokyo", "09:00", "15:30"))

        report = engine.audit_global_dst_transitions("2026-03-15")
        self.assertFalse(report.is_us_eu_desync_window)
        self.assertIsNone(report.us_exchange_id)
        self.assertIsNone(report.eu_exchange_id)
        self.assertIsNone(report.us_eu_offset_delta_hours)
        self.assertTrue(any("SKIPPED" in w for w in report.warnings))

    def test_multiple_us_candidates_warns_and_picks_first_registered(self):
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XNYS", "NYSE", "America/New_York", "09:30", "16:00"))
        engine.register_exchange(ExchangeScheduleSpec("XCME", "CME", "America/Chicago", "08:30", "15:15"))
        engine.register_exchange(ExchangeScheduleSpec("XLON", "LSE", "Europe/London", "08:00", "16:30"))

        report = engine.audit_global_dst_transitions("2026-03-15")
        self.assertEqual(report.us_exchange_id, "XNYS")
        self.assertTrue(any("Multiple US exchanges" in w for w in report.warnings))

    # ------------------------------------------------------------------
    # Non-existent / ambiguous local wall times
    # ------------------------------------------------------------------

    def test_nonexistent_local_open_is_flagged(self):
        # 02:30 on 2026-03-08 never occurs in America/New_York: the clock jumps 02:00 -> 03:00.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("GAP", "Gap Venue", "America/New_York", "02:30", "16:00"))

        window = engine.calculate_utc_session("GAP", "2026-03-08")
        self.assertTrue(window.local_open_is_nonexistent)
        self.assertFalse(window.local_open_is_ambiguous)
        self.assertTrue(any("does not exist" in w for w in window.warnings))
        # A normal day is not flagged.
        self.assertFalse(engine.calculate_utc_session("GAP", "2026-03-09").local_open_is_nonexistent)

    def test_ambiguous_local_open_is_flagged_and_resolves_to_first_occurrence(self):
        # 01:30 on 2026-11-01 occurs twice in America/New_York (EDT then EST).
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("AMB", "Amb Venue", "America/New_York", "01:30", "16:00"))

        window = engine.calculate_utc_session("AMB", "2026-11-01")
        self.assertTrue(window.local_open_is_ambiguous)
        self.assertFalse(window.local_open_is_nonexistent)
        self.assertTrue(any("occurs twice" in w for w in window.warnings))
        # fold=0 -> the first (EDT, UTC-4) occurrence: 01:30 EDT == 05:30Z.
        self.assertEqual(window.utc_open_iso, "2026-11-01T05:30:00Z")

    def test_strict_mode_rejects_nonexistent_and_ambiguous_wall_times(self):
        engine = DstTransitionHandlerEngine(strict=True)
        engine.register_exchange(ExchangeScheduleSpec("GAP", "Gap Venue", "America/New_York", "02:30", "16:00"))
        engine.register_exchange(ExchangeScheduleSpec("AMB", "Amb Venue", "America/New_York", "01:30", "16:00"))

        with self.assertRaises(DstScheduleError):
            engine.calculate_utc_session("GAP", "2026-03-08")
        with self.assertRaises(DstScheduleError):
            engine.calculate_utc_session("AMB", "2026-11-01")
        # Non-transition dates still succeed in strict mode.
        self.assertEqual(engine.calculate_utc_session("GAP", "2026-03-09").utc_open_iso, "2026-03-09T06:30:00Z")

    # ------------------------------------------------------------------
    # DST transition inside a session
    # ------------------------------------------------------------------

    def test_spring_forward_inside_session_yields_short_elapsed_session(self):
        # An overnight-into-morning session spanning 02:00 loses an hour of elapsed time even
        # though the local clock span is unchanged.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("OVN", "Overnight", "America/New_York", "01:00", "09:00"))

        window = engine.calculate_utc_session("OVN", "2026-03-08")
        self.assertTrue(window.dst_shift_inside_session)
        self.assertEqual(window.session_duration_hours, 7.0)  # nominal local span is 8h
        self.assertFalse(window.is_dst_active)                # EST at open
        self.assertTrue(window.is_dst_active_at_close)        # EDT at close
        self.assertEqual(window.utc_offset_open_hours, -5.0)
        self.assertEqual(window.utc_offset_close_hours, -4.0)
        self.assertTrue(any("DST transition falls inside" in w for w in window.warnings))

    def test_fall_back_inside_session_yields_long_elapsed_session(self):
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("OVN", "Overnight", "America/New_York", "01:00", "09:00"))

        window = engine.calculate_utc_session("OVN", "2026-11-01")
        self.assertTrue(window.dst_shift_inside_session)
        self.assertEqual(window.session_duration_hours, 9.0)  # nominal local span is 8h

    def test_ordinary_session_has_no_intra_session_shift(self):
        window = self.engine.calculate_utc_session("NYSE", "2026-03-08")
        self.assertFalse(window.dst_shift_inside_session)
        self.assertEqual(window.session_duration_hours, 6.5)

    # ------------------------------------------------------------------
    # Overnight sessions and input validation
    # ------------------------------------------------------------------

    def test_overnight_session_requires_explicit_opt_in(self):
        # Regression: a close earlier than the open previously produced a negative duration.
        engine = DstTransitionHandlerEngine()
        with self.assertRaises(DstScheduleError):
            engine.register_exchange(
                ExchangeScheduleSpec("CME", "CME Globex", "America/Chicago", "17:00", "16:00")
            )

    def test_overnight_session_rolls_close_to_next_day(self):
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(
            ExchangeScheduleSpec(
                "CME", "CME Globex", "America/Chicago", "17:00", "16:00", spans_midnight=True
            )
        )
        window = engine.calculate_utc_session("CME", "2026-04-15")
        self.assertEqual(window.utc_open_iso, "2026-04-15T22:00:00Z")   # 17:00 CDT
        self.assertEqual(window.utc_close_iso, "2026-04-16T21:00:00Z")  # 16:00 CDT next day
        self.assertEqual(window.session_duration_hours, 23.0)
        self.assertGreater(window.utc_close_ns, window.utc_open_ns)

    def test_zero_length_session_is_rejected(self):
        engine = DstTransitionHandlerEngine()
        with self.assertRaises(DstScheduleError):
            engine.register_exchange(
                ExchangeScheduleSpec("Z", "Zero", "America/New_York", "09:30", "09:30")
            )

    def test_invalid_timezone_is_rejected_at_registration(self):
        engine = DstTransitionHandlerEngine()
        with self.assertRaises(DstScheduleError):
            engine.register_exchange(
                ExchangeScheduleSpec("BAD", "Bad", "Mars/Olympus_Mons", "09:30", "16:00")
            )

    def test_invalid_time_strings_are_rejected_at_registration(self):
        engine = DstTransitionHandlerEngine()
        for open_time, close_time in (("9.30", "16:00"), ("25:00", "26:00"), ("09:70", "16:00"), ("0930", "16:00")):
            with self.subTest(open_time=open_time):
                with self.assertRaises(DstScheduleError):
                    engine.register_exchange(
                        ExchangeScheduleSpec("BAD", "Bad", "America/New_York", open_time, close_time)
                    )

    def test_invalid_date_is_rejected(self):
        for bad_date in ("15/04/2026", "2026-02-30", "not-a-date", ""):
            with self.subTest(bad_date=bad_date):
                with self.assertRaises(DstScheduleError):
                    self.engine.calculate_utc_session("NYSE", bad_date)

    def test_unregistered_exchange_is_rejected(self):
        with self.assertRaises(DstScheduleError):
            self.engine.calculate_utc_session("XNSE", "2026-04-15")

    # ------------------------------------------------------------------
    # Non-DST and southern-hemisphere zones
    # ------------------------------------------------------------------

    def test_non_dst_zone_reports_stable_offset(self):
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XTKS", "TSE", "Asia/Tokyo", "09:00", "15:30"))
        for date_str in ("2026-01-15", "2026-03-15", "2026-07-15", "2026-11-15"):
            with self.subTest(date_str=date_str):
                window = engine.calculate_utc_session("XTKS", date_str)
                self.assertFalse(window.is_dst_active)
                self.assertEqual(window.utc_offset_open_hours, 9.0)  # JST, no DST

    def test_southern_hemisphere_dst_runs_opposite_to_the_north(self):
        # Australia/Sydney observes DST over the Northern-Hemisphere winter.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XASX", "ASX", "Australia/Sydney", "10:00", "16:00"))
        january = engine.calculate_utc_session("XASX", "2026-01-15")
        july = engine.calculate_utc_session("XASX", "2026-07-15")
        self.assertTrue(january.is_dst_active)
        self.assertEqual(january.utc_offset_open_hours, 11.0)   # AEDT
        self.assertFalse(july.is_dst_active)
        self.assertEqual(july.utc_offset_open_hours, 10.0)      # AEST

    def test_negative_dst_zone_is_not_inverted(self):
        # Regression: the IANA database models Europe/Dublin with *negative* DST -- Irish
        # Standard Time is the summer offset and GMT is a negative-DST winter -- so
        # `bool(dt.dst())` is truthy in January and falsy in July there. Using it as the
        # summer-time predicate inverted the desync verdict for a Dublin EU leg.
        engine = DstTransitionHandlerEngine()
        engine.register_exchange(ExchangeScheduleSpec("XNYS", "NYSE", "America/New_York", "09:30", "16:00"))
        engine.register_exchange(ExchangeScheduleSpec("XDUB", "Euronext Dublin", "Europe/Dublin", "08:00", "16:30"))

        january = engine.calculate_utc_session("XDUB", "2026-01-15")
        july = engine.calculate_utc_session("XDUB", "2026-07-15")
        self.assertFalse(january.is_dst_active)          # GMT, offset 0 -- winter
        self.assertEqual(january.utc_offset_open_hours, 0.0)
        self.assertTrue(july.is_dst_active)              # IST, offset +1 -- summer
        self.assertEqual(july.utc_offset_open_hours, 1.0)

        # And the desync verdict follows the offsets, not the inverted dst() flag.
        for date_str, expected in (
            ("2026-01-15", False),   # both on standard time
            ("2026-03-15", True),    # US on DST, Dublin still on GMT
            ("2026-04-15", False),   # both on summer time
            ("2026-10-28", True),    # Dublin back on GMT, US still on DST
        ):
            with self.subTest(date_str=date_str):
                report = engine.audit_global_dst_transitions(date_str)
                self.assertEqual(report.is_us_eu_desync_window, expected)

    def test_pre_2007_us_rule_is_taken_from_tzdata(self):
        # Before the Energy Policy Act of 2005 took effect in 2007, US DST began on the
        # first Sunday of April. In 2006 the EU therefore moved FIRST (2006-03-26) and the
        # US followed on 2006-04-02, reversing the usual direction of the spring window.
        # A hard-coded post-2007 rule would miss this entirely.
        self.assertFalse(self.engine.audit_global_dst_transitions("2006-03-20").is_us_eu_desync_window)
        reversed_window = self.engine.audit_global_dst_transitions("2006-03-30")
        self.assertTrue(reversed_window.is_us_eu_desync_window)
        # EU on summer time (+1) while the US is still on EST (-5) -> a -6h gap, the
        # opposite sign of the modern March window's -4h.
        self.assertEqual(reversed_window.us_eu_offset_delta_hours, -6.0)
        self.assertFalse(self.engine.audit_global_dst_transitions("2006-04-10").is_us_eu_desync_window)

    # ------------------------------------------------------------------
    # Dataclass shape
    # ------------------------------------------------------------------

    def test_report_and_window_types(self):
        report = self.engine.audit_global_dst_transitions("2026-04-15")
        self.assertIsInstance(report, DstTransitionAuditReport)
        self.assertEqual(len(report.sessions), 2)
        for session in report.sessions:
            self.assertIsInstance(session, UtcSessionWindow)


if __name__ == "__main__":
    unittest.main()
