"""
Unit tests for the multi-timezone-session-scheduling skill.

Expected UTC instants are derived independently of the implementation, from the published
local session times plus the statutory DST rules for each zone (US: 2nd Sunday of March to
1st Sunday of November; EU: last Sunday of March to last Sunday of October, both at 01:00
GMT; Australia: 1st Sunday of October to 1st Sunday of April). 2026 dates are used because
1 March 2026 is a Sunday, which produces the long 21-day US/EU spring desynchronisation
window (US DST starts 8 March, EU summer time starts 29 March).
"""
import datetime
import logging
import unittest
from zoneinfo import ZoneInfo

from session_scheduler import (
    DEFAULT_EXCHANGE_SCHEDULES,
    ExchangeSchedule,
    MarketSessionState,
    MultiTimezoneSessionScheduler,
    ResolvedSession,
    SessionScheduleError,
    cross_exchange_gap_minutes,
    exchange_session_utc,
    logger as scheduler_logger,
)

UTC = datetime.timezone.utc


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, minute, tzinfo=UTC)


class TestSessionUtcConversion(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = MultiTimezoneSessionScheduler()

    def test_nyse_summer_session_uses_edt(self) -> None:
        # 15 Jul 2026 is inside US DST: 09:30/16:00 EDT (UTC-4) == 13:30/20:00 UTC.
        open_utc, close_utc = self.scheduler.get_session_utc("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(open_utc, utc(2026, 7, 15, 13, 30))
        self.assertEqual(close_utc, utc(2026, 7, 15, 20, 0))

    def test_nyse_winter_session_uses_est(self) -> None:
        # 15 Jan 2026 is outside US DST: 09:30/16:00 EST (UTC-5) == 14:30/21:00 UTC.
        open_utc, close_utc = self.scheduler.get_session_utc("XNYS", datetime.date(2026, 1, 15))
        self.assertEqual(open_utc, utc(2026, 1, 15, 14, 30))
        self.assertEqual(close_utc, utc(2026, 1, 15, 21, 0))

    def test_sydney_southern_hemisphere_dst_is_inverted(self) -> None:
        # January is Sydney's summer: AEDT (UTC+11), so 10:00 local is 23:00 UTC the day BEFORE.
        summer_open, _ = self.scheduler.get_session_utc("XASX", datetime.date(2026, 1, 15))
        self.assertEqual(summer_open, utc(2026, 1, 14, 23, 0))
        # July is Sydney's winter: AEST (UTC+10), so 10:00 local is 00:00 UTC the same day.
        winter_open, _ = self.scheduler.get_session_utc("XASX", datetime.date(2026, 7, 15))
        self.assertEqual(winter_open, utc(2026, 7, 15, 0, 0))
        # The inversion: Sydney is on summer time in January and standard time in July,
        # while New York is the exact reverse in the same two months.
        nyse_jan, _ = self.scheduler.get_session_utc("XNYS", datetime.date(2026, 1, 15))
        nyse_jul, _ = self.scheduler.get_session_utc("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(summer_open.astimezone(ZoneInfo("Australia/Sydney")).utcoffset(),
                         datetime.timedelta(hours=11))
        self.assertEqual(winter_open.astimezone(ZoneInfo("Australia/Sydney")).utcoffset(),
                         datetime.timedelta(hours=10))
        self.assertEqual(nyse_jan.astimezone(ZoneInfo("America/New_York")).utcoffset(),
                         datetime.timedelta(hours=-5))
        self.assertEqual(nyse_jul.astimezone(ZoneInfo("America/New_York")).utcoffset(),
                         datetime.timedelta(hours=-4))

    def test_kolkata_has_no_dst_so_session_is_offset_stable(self) -> None:
        # Asia/Kolkata is UTC+5:30 year-round; 09:15 local == 03:45 UTC in both seasons.
        for date in (datetime.date(2026, 1, 15), datetime.date(2026, 7, 15)):
            open_utc, close_utc = self.scheduler.get_session_utc("XNSE", date)
            self.assertEqual(open_utc.time(), datetime.time(3, 45))
            self.assertEqual(close_utc.time(), datetime.time(10, 0))

    def test_exchange_code_lookup_is_case_and_whitespace_insensitive(self) -> None:
        self.assertEqual(
            self.scheduler.get_session_utc(" xnys ", datetime.date(2026, 7, 15)),
            self.scheduler.get_session_utc("XNYS", datetime.date(2026, 7, 15)),
        )


class TestUsEuDesynchronisationWindow(unittest.TestCase):
    """The transatlantic gap is NOT constant year-round -- the core claim of this skill."""

    def setUp(self) -> None:
        self.scheduler = MultiTimezoneSessionScheduler()

    def test_gap_differs_inside_and_outside_the_spring_desync_window(self) -> None:
        # 16 Mar 2026 (Mon) is inside the window: New York is on EDT (UTC-4) but London is
        # still on GMT (UTC+0). LSE closes 16:30 GMT = 16:30Z; NYSE opens 09:30 EDT = 13:30Z.
        in_window = self.scheduler.calculate_exchange_gap_minutes(
            "XLON", "XNYS", datetime.date(2026, 3, 16)
        )
        self.assertEqual(in_window, -180.0)

        # 15 Apr 2026 (Wed) is outside it: London is on BST (UTC+1), so LSE closes 15:30Z
        # while NYSE still opens 13:30Z -- the overlap is one hour shorter.
        out_of_window = self.scheduler.calculate_exchange_gap_minutes(
            "XLON", "XNYS", datetime.date(2026, 4, 15)
        )
        self.assertEqual(out_of_window, -120.0)

        self.assertNotEqual(in_window, out_of_window)

    def test_gap_differs_inside_the_autumn_desync_window(self) -> None:
        # 28 Oct 2026 (Wed): EU summer time ended 25 Oct, US DST does not end until 1 Nov,
        # so London is on GMT while New York is still on EDT -- the same -180 minute gap.
        self.assertEqual(
            self.scheduler.calculate_exchange_gap_minutes(
                "XLON", "XNYS", datetime.date(2026, 10, 28)
            ),
            -180.0,
        )

    def test_sequential_handoff_gap_is_positive_for_tokyo_into_london(self) -> None:
        # 15 Jul 2026: TSE closes 15:30 JST (UTC+9) = 06:30Z; LSE opens 08:00 BST = 07:00Z.
        self.assertEqual(
            self.scheduler.calculate_exchange_gap_minutes(
                "XTKS", "XLON", datetime.date(2026, 7, 15)
            ),
            30.0,
        )


class TestDstTransitionBoundaries(unittest.TestCase):
    def test_skipped_wall_time_is_flagged_not_silently_accepted(self) -> None:
        # US DST begins 02:00 local on 8 Mar 2026, so 02:30 America/New_York never occurs.
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        scheduler.register_schedule(
            ExchangeSchedule(
                exchange_code="TEST",
                iana_timezone="America/New_York",
                open_time=datetime.time(2, 30),
                close_time=datetime.time(9, 0),
            )
        )
        with self.assertLogs(scheduler_logger.name, level=logging.WARNING):
            resolved = scheduler.resolve_session("TEST", datetime.date(2026, 3, 8))
        self.assertIn("open_time", resolved.nonexistent_boundaries)
        self.assertEqual(resolved.ambiguous_boundaries, ())
        # PEP 495 fold=0 normalises the skipped time with the pre-transition offset (EST).
        self.assertEqual(resolved.open_utc, utc(2026, 3, 8, 7, 30))

    def test_repeated_wall_time_is_flagged_and_resolves_to_first_occurrence(self) -> None:
        # US DST ends 02:00 local on 1 Nov 2026, so 01:30 America/New_York occurs twice.
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        scheduler.register_schedule(
            ExchangeSchedule(
                exchange_code="TEST",
                iana_timezone="America/New_York",
                open_time=datetime.time(1, 30),
                close_time=datetime.time(9, 0),
            )
        )
        with self.assertLogs(scheduler_logger.name, level=logging.WARNING):
            resolved = scheduler.resolve_session("TEST", datetime.date(2026, 11, 1))
        self.assertIn("open_time", resolved.ambiguous_boundaries)
        self.assertEqual(resolved.nonexistent_boundaries, ())
        # fold=0 is the FIRST occurrence, still on EDT (UTC-4): 01:30 EDT == 05:30Z.
        # The second occurrence, on EST, would be 06:30Z.
        self.assertEqual(resolved.open_utc, utc(2026, 11, 1, 5, 30))

    def test_strict_mode_raises_on_a_transition_boundary(self) -> None:
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={}, strict=True)
        scheduler.register_schedule(
            ExchangeSchedule(
                exchange_code="TEST",
                iana_timezone="America/New_York",
                open_time=datetime.time(2, 30),
                close_time=datetime.time(9, 0),
            )
        )
        with self.assertRaises(SessionScheduleError):
            scheduler.resolve_session("TEST", datetime.date(2026, 3, 8))
        # A date away from the transition is unaffected by strict mode.
        self.assertEqual(
            scheduler.resolve_session("TEST", datetime.date(2026, 3, 9)).open_utc,
            utc(2026, 3, 9, 6, 30),
        )

    def test_ordinary_session_on_a_transition_day_is_not_flagged(self) -> None:
        scheduler = MultiTimezoneSessionScheduler()
        resolved = scheduler.resolve_session("XNYS", datetime.date(2026, 3, 8))
        self.assertEqual(resolved.nonexistent_boundaries, ())
        self.assertEqual(resolved.ambiguous_boundaries, ())
        # 09:30 on the transition day is already EDT: 13:30Z, not 14:30Z.
        self.assertEqual(resolved.open_utc, utc(2026, 3, 8, 13, 30))


class TestIntradayBreaks(unittest.TestCase):
    """TSE trades 09:00-11:30 and 12:30-15:30 JST; the lunch break is not trading time."""

    def setUp(self) -> None:
        self.scheduler = MultiTimezoneSessionScheduler()
        self.date = datetime.date(2026, 7, 15)  # a Wednesday

    def test_trading_windows_exclude_the_lunch_break(self) -> None:
        resolved = self.scheduler.resolve_session("XTKS", self.date)
        # JST is UTC+9 year-round.
        self.assertEqual(
            resolved.trading_windows_utc,
            (
                (utc(2026, 7, 15, 0, 0), utc(2026, 7, 15, 2, 30)),
                (utc(2026, 7, 15, 3, 30), utc(2026, 7, 15, 6, 30)),
            ),
        )
        self.assertEqual(resolved.close_utc, utc(2026, 7, 15, 6, 30))

    def test_status_during_lunch_break_is_break_not_regular_trading(self) -> None:
        # 12:00 JST == 03:00 UTC, inside the 11:30-12:30 halt.
        self.assertEqual(
            self.scheduler.get_market_status("XTKS", utc(2026, 7, 15, 3, 0)),
            MarketSessionState.BREAK,
        )

    def test_status_in_each_continuous_session_is_regular_trading(self) -> None:
        for hour, minute in ((0, 30), (2, 29), (3, 30), (6, 29)):
            with self.subTest(utc_time=(hour, minute)):
                self.assertEqual(
                    self.scheduler.get_market_status("XTKS", utc(2026, 7, 15, hour, minute)),
                    MarketSessionState.REGULAR_TRADING,
                )

    def test_tse_close_reflects_the_2024_extension_to_1530_jst(self) -> None:
        # 15:20 JST (06:20Z) still trades; an earlier-11-05 15:00 close would have ended it.
        self.assertEqual(
            self.scheduler.get_market_status("XTKS", utc(2026, 7, 15, 6, 20)),
            MarketSessionState.REGULAR_TRADING,
        )


class TestMarketStatusBoundaries(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = MultiTimezoneSessionScheduler()

    def test_boundaries_are_half_open(self) -> None:
        # 15 Jul 2026: open 13:30Z, close 20:00Z, pre-market from 11:00Z, post until 00:00Z.
        cases = [
            (utc(2026, 7, 15, 10, 59), MarketSessionState.MARKET_CLOSED),
            (utc(2026, 7, 15, 11, 0), MarketSessionState.PRE_MARKET),
            (utc(2026, 7, 15, 13, 29), MarketSessionState.PRE_MARKET),
            (utc(2026, 7, 15, 13, 30), MarketSessionState.REGULAR_TRADING),
            (utc(2026, 7, 15, 19, 59), MarketSessionState.REGULAR_TRADING),
            (utc(2026, 7, 15, 20, 0), MarketSessionState.POST_MARKET),
            (utc(2026, 7, 15, 23, 59), MarketSessionState.POST_MARKET),
        ]
        for query, expected in cases:
            with self.subTest(query=query.isoformat()):
                self.assertEqual(self.scheduler.get_market_status("XNYS", query), expected)

    def test_post_market_end_is_exclusive(self) -> None:
        # 20:00 ET on 15 Jul is 00:00Z on 16 Jul, which is also a fresh (closed) local day.
        self.assertEqual(
            self.scheduler.get_market_status("XNYS", utc(2026, 7, 16, 0, 0)),
            MarketSessionState.MARKET_CLOSED,
        )

    def test_weekend_is_closed_in_exchange_local_time(self) -> None:
        # 18 Jul 2026 is a Saturday in New York.
        self.assertEqual(
            self.scheduler.get_market_status("XNYS", utc(2026, 7, 18, 14, 0)),
            MarketSessionState.MARKET_CLOSED,
        )

    def test_weekend_is_evaluated_in_local_not_utc_time(self) -> None:
        # 04:00Z Saturday 18 Jul is 00:00 Saturday in New York -- closed. But 04:00Z on
        # Monday 20 Jul is still Sunday 00:00 ET, which must also be closed even though the
        # UTC weekday is a Monday.
        self.assertEqual(
            self.scheduler.get_market_status("XNYS", utc(2026, 7, 20, 4, 0)),
            MarketSessionState.MARKET_CLOSED,
        )

    def test_sydney_open_is_correctly_classified_across_the_utc_date_boundary(self) -> None:
        # Thu 15 Jan 2026 10:30 AEDT == Wed 14 Jan 23:30 UTC. The local date, not the UTC
        # date, determines the session -- and 15 Jan is a weekday.
        self.assertEqual(
            self.scheduler.get_market_status("XASX", utc(2026, 1, 14, 23, 30)),
            MarketSessionState.REGULAR_TRADING,
        )

    def test_default_query_time_is_accepted(self) -> None:
        self.assertIsInstance(
            self.scheduler.get_market_status("XNYS"), MarketSessionState
        )


class TestOvernightSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        self.scheduler.register_schedule(
            ExchangeSchedule(
                exchange_code="NIGHT",
                iana_timezone="America/New_York",
                open_time=datetime.time(18, 0),
                close_time=datetime.time(17, 0),
                spans_midnight=True,
            )
        )

    def test_close_is_resolved_on_the_following_local_date(self) -> None:
        # Wed 15 Jul 2026 18:00 EDT == 22:00Z; close Thu 16 Jul 17:00 EDT == 21:00Z.
        open_utc, close_utc = self.scheduler.get_session_utc("NIGHT", datetime.date(2026, 7, 15))
        self.assertEqual(open_utc, utc(2026, 7, 15, 22, 0))
        self.assertEqual(close_utc, utc(2026, 7, 16, 21, 0))

    def test_status_after_midnight_belongs_to_the_previous_local_date(self) -> None:
        # 03:00Z Thu 16 Jul is 23:00 Wed ET -- still inside the session opened on the 15th.
        self.assertEqual(
            self.scheduler.get_market_status("NIGHT", utc(2026, 7, 16, 3, 0)),
            MarketSessionState.REGULAR_TRADING,
        )
        # 10:00Z Thu 16 Jul is 06:00 Thu ET -- also inside Wednesday's overnight session.
        self.assertEqual(
            self.scheduler.get_market_status("NIGHT", utc(2026, 7, 16, 10, 0)),
            MarketSessionState.REGULAR_TRADING,
        )

    def test_close_time_before_open_time_requires_spans_midnight(self) -> None:
        with self.assertRaises(SessionScheduleError):
            MultiTimezoneSessionScheduler(
                custom_schedules={
                    "BAD": ExchangeSchedule(
                        exchange_code="BAD",
                        iana_timezone="America/New_York",
                        open_time=datetime.time(18, 0),
                        close_time=datetime.time(17, 0),
                    )
                }
            )


class TestScheduleValidation(unittest.TestCase):
    def test_unknown_exchange_raises_rather_than_reporting_closed(self) -> None:
        scheduler = MultiTimezoneSessionScheduler()
        with self.assertRaises(SessionScheduleError):
            scheduler.get_market_status("XXXX", utc(2026, 7, 15, 14, 0))
        with self.assertRaises(SessionScheduleError):
            scheduler.get_session_utc("XXXX", datetime.date(2026, 7, 15))

    def test_naive_query_time_is_rejected(self) -> None:
        scheduler = MultiTimezoneSessionScheduler()
        with self.assertRaises(SessionScheduleError):
            scheduler.get_market_status("XNYS", datetime.datetime(2026, 7, 15, 14, 0))

    def test_non_utc_aware_query_time_is_converted_not_rejected(self) -> None:
        # 09:30 America/New_York on 15 Jul is exactly the open.
        aware_local = datetime.datetime(
            2026, 7, 15, 9, 30, tzinfo=ZoneInfo("America/New_York")
        )
        self.assertEqual(
            MultiTimezoneSessionScheduler().get_market_status("XNYS", aware_local),
            MarketSessionState.REGULAR_TRADING,
        )

    def test_invalid_timezone_is_rejected_at_registration(self) -> None:
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        with self.assertRaises(SessionScheduleError):
            scheduler.register_schedule(
                ExchangeSchedule(
                    exchange_code="BAD",
                    iana_timezone="UTC-5",
                    open_time=datetime.time(9, 30),
                    close_time=datetime.time(16, 0),
                )
            )

    def test_pre_and_post_market_ordering_is_validated(self) -> None:
        base = dict(
            exchange_code="BAD",
            iana_timezone="America/New_York",
            open_time=datetime.time(9, 30),
            close_time=datetime.time(16, 0),
        )
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        with self.assertRaises(SessionScheduleError):
            scheduler.register_schedule(
                ExchangeSchedule(**base, pre_market_time=datetime.time(10, 0))
            )
        with self.assertRaises(SessionScheduleError):
            scheduler.register_schedule(
                ExchangeSchedule(**base, post_market_time=datetime.time(15, 0))
            )

    def test_breaks_must_be_inside_the_session_and_non_overlapping(self) -> None:
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        base = dict(
            exchange_code="BAD",
            iana_timezone="Asia/Tokyo",
            open_time=datetime.time(9, 0),
            close_time=datetime.time(15, 30),
        )
        for bad_breaks in (
            ((datetime.time(8, 0), datetime.time(9, 30)),),  # starts before the open
            ((datetime.time(15, 0), datetime.time(16, 0)),),  # ends after the close
            ((datetime.time(12, 0), datetime.time(11, 0)),),  # inverted
            (
                (datetime.time(10, 0), datetime.time(11, 0)),
                (datetime.time(10, 30), datetime.time(12, 0)),
            ),  # overlapping
        ):
            with self.subTest(breaks=bad_breaks):
                with self.assertRaises(SessionScheduleError):
                    scheduler.register_schedule(ExchangeSchedule(**base, breaks=bad_breaks))

    def test_spans_midnight_cannot_be_combined_with_breaks(self) -> None:
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        with self.assertRaises(SessionScheduleError):
            scheduler.register_schedule(
                ExchangeSchedule(
                    exchange_code="BAD",
                    iana_timezone="America/New_York",
                    open_time=datetime.time(18, 0),
                    close_time=datetime.time(17, 0),
                    spans_midnight=True,
                    breaks=((datetime.time(23, 0), datetime.time(23, 30)),),
                )
            )

    def test_datetime_is_rejected_where_a_date_is_required(self) -> None:
        scheduler = MultiTimezoneSessionScheduler()
        with self.assertRaises(SessionScheduleError):
            scheduler.get_session_utc("XNYS", datetime.datetime(2026, 7, 15, 12, 0))


class TestRegistryIsolation(unittest.TestCase):
    def test_registering_does_not_mutate_module_level_defaults(self) -> None:
        original = dict(DEFAULT_EXCHANGE_SCHEDULES)
        scheduler_a = MultiTimezoneSessionScheduler()
        scheduler_a.register_schedule(
            ExchangeSchedule(
                exchange_code="ZZZZ",
                iana_timezone="Europe/Paris",
                open_time=datetime.time(9, 0),
                close_time=datetime.time(17, 30),
            )
        )
        self.assertIn("ZZZZ", scheduler_a.schedules)
        self.assertEqual(DEFAULT_EXCHANGE_SCHEDULES, original)

        scheduler_b = MultiTimezoneSessionScheduler()
        self.assertNotIn("ZZZZ", scheduler_b.schedules)
        with self.assertRaises(SessionScheduleError):
            scheduler_b.get_session_utc("ZZZZ", datetime.date(2026, 7, 15))

    def test_empty_custom_schedules_yields_an_empty_registry(self) -> None:
        scheduler = MultiTimezoneSessionScheduler(custom_schedules={})
        self.assertEqual(scheduler.schedules, {})

    def test_caller_supplied_mapping_is_not_mutated(self) -> None:
        supplied = {"XLON": DEFAULT_EXCHANGE_SCHEDULES["XLON"]}
        scheduler = MultiTimezoneSessionScheduler(custom_schedules=supplied)
        scheduler.register_schedule(DEFAULT_EXCHANGE_SCHEDULES["XNYS"])
        self.assertEqual(set(supplied), {"XLON"})


class TestBackwardCompatibleHelpers(unittest.TestCase):
    def test_exchange_session_utc_matches_the_scheduler(self) -> None:
        date = datetime.date(2026, 7, 15)
        open_utc, close_utc = exchange_session_utc(
            date, datetime.time(9, 30), datetime.time(16, 0), "America/New_York"
        )
        self.assertEqual(open_utc, utc(2026, 7, 15, 13, 30))
        self.assertEqual(close_utc, utc(2026, 7, 15, 20, 0))
        self.assertEqual(
            (open_utc, close_utc),
            MultiTimezoneSessionScheduler().get_session_utc("XNYS", date),
        )

    def test_cross_exchange_gap_matches_the_scheduler_method(self) -> None:
        date = datetime.date(2026, 3, 16)
        scheduler = MultiTimezoneSessionScheduler()
        _, lse_close = scheduler.get_session_utc("XLON", date)
        nyse_open, _ = scheduler.get_session_utc("XNYS", date)
        self.assertEqual(cross_exchange_gap_minutes(lse_close, nyse_open), -180.0)
        self.assertEqual(
            cross_exchange_gap_minutes(lse_close, nyse_open),
            scheduler.calculate_exchange_gap_minutes("XLON", "XNYS", date),
        )

    def test_cross_exchange_gap_rejects_naive_inputs(self) -> None:
        with self.assertRaises(SessionScheduleError):
            cross_exchange_gap_minutes(
                datetime.datetime(2026, 7, 15, 16, 30), utc(2026, 7, 15, 13, 30)
            )

    def test_resolved_session_is_exported(self) -> None:
        resolved = MultiTimezoneSessionScheduler().resolve_session(
            "XLON", datetime.date(2026, 7, 15)
        )
        self.assertIsInstance(resolved, ResolvedSession)
        self.assertEqual(resolved.exchange_code, "XLON")


if __name__ == "__main__":
    unittest.main()
