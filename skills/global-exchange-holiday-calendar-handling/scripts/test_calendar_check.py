"""
Unit tests for global-exchange-holiday-calendar-handling skill.

Expected UTC timestamps below are derived independently of the implementation,
from the exchange's published local session times and its UTC offset on the date
in question (e.g. NYSE 09:30 ET is 13:30 UTC under EDT and 14:30 UTC under EST;
IST is UTC+05:30 year-round). They are not recomputed with the module's own
conversion helper, so a regression in that helper fails these tests.

Covers:
1.  Weekend / non-trading-weekday closure via per-exchange weekmask.
2.  Full-day holiday detection (NYSE and NSE 2026 published calendars).
3.  Half-day / early-close detection and its exact UTC close time.
4.  DST correctness: the same exchange under EST and under EDT.
5.  Listing-venue resolution, including refusal to guess.
6.  The calendar-library path, driven by a fake library (regression tests for the
     `is_half_day` API that does not exist, and for Sunday-trading exchanges).
7.  Refusal to fabricate sessions for uncovered exchanges / out-of-coverage years.
8.  Input validation at the date/datetime boundary.
9.  Backward compatibility of the module-level helper functions.
"""
import datetime
import logging
import unittest

from calendar_check import (
    CalendarDataUnavailable,
    CalendarSource,
    GlobalExchangeCalendarManager,
    SessionStatus,
    UnresolvedInstrumentExchange,
    is_trading_day,
    session_open_close,
)

UTC = datetime.timezone.utc


def setUpModule():
    """Quieten the module's degraded-mode warnings; assertions cover behaviour."""
    logging.getLogger("calendar_check").setLevel(logging.CRITICAL)


def _utc(year, month, day, hour, minute):
    return datetime.datetime(year, month, day, hour, minute, tzinfo=UTC)


class _FakeTimestamp:
    """Minimal stand-in for the pd.Timestamp values a calendar returns."""

    def __init__(self, dt):
        self._dt = dt
        self.year = dt.year
        self.month = dt.month
        self.day = dt.day

    def to_pydatetime(self):
        return self._dt


class _FakeCalendar:
    """Fake `exchange_calendars` calendar exposing only the real public API.

    Deliberately does NOT define `is_half_day` -- `ExchangeCalendar` has no such
    method, and the previous implementation called it, so every library lookup
    raised AttributeError and silently degraded to the static tables.
    """

    def __init__(self, name, weekmask, sessions, opens, closes, early_closes=()):
        self.name = name
        self.weekmask = weekmask
        self._sessions = set(sessions)
        self._opens = opens
        self._closes = closes
        self.early_closes = [_FakeTimestamp(d) for d in early_closes]

    def is_session(self, date):
        return date in self._sessions

    def session_open(self, date):
        return _FakeTimestamp(self._opens[date])

    def session_close(self, date):
        return _FakeTimestamp(self._closes[date])


class _FakeLib:
    __name__ = "fake_exchange_calendars"

    def __init__(self, calendars):
        self._calendars = calendars

    def get_calendar(self, name):
        try:
            return self._calendars[name]
        except KeyError:
            raise ValueError(f"unknown calendar {name!r}") from None


class TestFallbackSessionResolution(unittest.TestCase):
    """Degraded mode: no calendar library available."""

    def setUp(self):
        # calendar_lib sentinel of None + no installed library exercises fallback.
        self.mgr = GlobalExchangeCalendarManager()

    def test_saturday_is_closed_for_a_monday_friday_exchange(self):
        saturday = datetime.date(2026, 1, 3)
        self.assertEqual(saturday.weekday(), 5)
        info = self.mgr.get_session_info("XNYS", saturday)
        self.assertEqual(info.status, SessionStatus.WEEKEND_CLOSED)
        self.assertIsNone(info.open_utc)
        self.assertFalse(self.mgr.is_trading_day("XNYS", saturday))

    def test_full_day_holiday_detection(self):
        info_nys = self.mgr.get_session_info("XNYS", datetime.date(2026, 1, 1))
        self.assertEqual(info_nys.status, SessionStatus.FULL_DAY_HOLIDAY)
        self.assertIn("New Year", info_nys.notes)

        info_nse = self.mgr.get_session_info("XNSE", datetime.date(2026, 1, 26))
        self.assertEqual(info_nse.status, SessionStatus.FULL_DAY_HOLIDAY)
        self.assertIn("Republic Day", info_nse.notes)

    def test_nse_holi_2026_is_march_3_not_march_6(self):
        """Regression: the table previously carried 2026-03-06, a trading day."""
        holi = self.mgr.get_session_info("XNSE", datetime.date(2026, 3, 3))
        self.assertEqual(holi.status, SessionStatus.FULL_DAY_HOLIDAY)

        march_6 = self.mgr.get_session_info("XNSE", datetime.date(2026, 3, 6))
        self.assertEqual(march_6.status, SessionStatus.REGULAR_SESSION)

    def test_nse_diwali_balipratipada_2026_is_november_10(self):
        """Regression: the table previously carried 2026-11-09, a trading day."""
        self.assertEqual(
            self.mgr.get_session_info("XNSE", datetime.date(2026, 11, 10)).status,
            SessionStatus.FULL_DAY_HOLIDAY,
        )
        self.assertEqual(
            self.mgr.get_session_info("XNSE", datetime.date(2026, 11, 9)).status,
            SessionStatus.REGULAR_SESSION,
        )

    def test_nyse_regular_session_under_est(self):
        """2026-01-02 is EST (UTC-5): 09:30 ET = 14:30 UTC, 16:00 ET = 21:00 UTC."""
        info = self.mgr.get_session_info("XNYS", datetime.date(2026, 1, 2))
        self.assertEqual(info.status, SessionStatus.REGULAR_SESSION)
        self.assertEqual(info.open_utc, _utc(2026, 1, 2, 14, 30))
        self.assertEqual(info.close_utc, _utc(2026, 1, 2, 21, 0))

    def test_nyse_regular_session_under_edt_is_one_hour_earlier_in_utc(self):
        """Regression for the fixed-offset bug.

        2026-07-15 is EDT (UTC-4): 09:30 ET = 13:30 UTC, 16:00 ET = 20:00 UTC.
        The previous implementation hardcoded 14:30/21:00 UTC year-round and was
        an hour late for the whole ~8-month DST period.
        """
        info = self.mgr.get_session_info("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(info.status, SessionStatus.REGULAR_SESSION)
        self.assertEqual(info.open_utc, _utc(2026, 7, 15, 13, 30))
        self.assertEqual(info.close_utc, _utc(2026, 7, 15, 20, 0))

    def test_nse_session_times_are_not_nyse_times(self):
        """IST is UTC+05:30: 09:15 = 03:45 UTC, 15:30 = 10:00 UTC.

        The previous implementation returned NYSE's 14:30-21:00 UTC for XNSE.
        """
        info = self.mgr.get_session_info("XNSE", datetime.date(2026, 7, 15))
        self.assertEqual(info.status, SessionStatus.REGULAR_SESSION)
        self.assertEqual(info.open_utc, _utc(2026, 7, 15, 3, 45))
        self.assertEqual(info.close_utc, _utc(2026, 7, 15, 10, 0))

    def test_half_day_early_close_utc_time(self):
        """Day after Thanksgiving 2026: 13:00 ET under EST = 18:00 UTC."""
        black_friday = datetime.date(2026, 11, 27)
        info = self.mgr.get_session_info("XNYS", black_friday)
        self.assertEqual(info.status, SessionStatus.HALF_DAY_EARLY_CLOSE)
        self.assertEqual(info.open_utc, _utc(2026, 11, 27, 14, 30))
        self.assertEqual(info.close_utc, _utc(2026, 11, 27, 18, 0))
        self.assertTrue(self.mgr.is_trading_day("XNYS", black_friday))

    def test_christmas_eve_2026_is_an_early_close(self):
        info = self.mgr.get_session_info("XNYS", datetime.date(2026, 12, 24))
        self.assertEqual(info.status, SessionStatus.HALF_DAY_EARLY_CLOSE)
        self.assertEqual(info.close_utc, _utc(2026, 12, 24, 18, 0))

    def test_all_session_timestamps_are_timezone_aware(self):
        info = self.mgr.get_session_info("XNYS", datetime.date(2026, 7, 15))
        self.assertIsNotNone(info.open_utc.tzinfo)
        self.assertEqual(info.open_utc.utcoffset(), datetime.timedelta(0))


class TestRefusalToFabricate(unittest.TestCase):
    """The module must report "unknown" rather than invent a session."""

    def setUp(self):
        self.mgr = GlobalExchangeCalendarManager()

    def test_uncovered_exchange_is_unknown_not_a_regular_session(self):
        """Regression: XLON on Christmas Day previously returned REGULAR_SESSION
        with NYSE's hours."""
        info = self.mgr.get_session_info("XLON", datetime.date(2026, 12, 25))
        self.assertEqual(info.status, SessionStatus.UNKNOWN_NO_CALENDAR_DATA)
        self.assertIsNone(info.open_utc)
        self.assertIsNone(info.close_utc)
        self.assertEqual(info.source, CalendarSource.NONE)

    def test_nonexistent_exchange_code_is_unknown(self):
        info = self.mgr.get_session_info("ZZZZ", datetime.date(2026, 7, 15))
        self.assertEqual(info.status, SessionStatus.UNKNOWN_NO_CALENDAR_DATA)

    def test_year_outside_fallback_coverage_is_unknown(self):
        """A stale static table must fail loudly instead of extrapolating."""
        info = self.mgr.get_session_info("XNYS", datetime.date(2031, 7, 15))
        self.assertEqual(info.status, SessionStatus.UNKNOWN_NO_CALENDAR_DATA)
        self.assertIn("out of coverage", info.notes)

    def test_is_trading_day_raises_rather_than_returning_false_for_unknown(self):
        with self.assertRaises(CalendarDataUnavailable):
            self.mgr.is_trading_day("XLON", datetime.date(2026, 7, 15))

    def test_session_open_close_raises_for_unknown(self):
        with self.assertRaises(CalendarDataUnavailable):
            session_open_close("XLON", datetime.date(2026, 7, 15))

    def test_fallback_results_are_labelled_with_their_source(self):
        info = self.mgr.get_session_info("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(info.source, CalendarSource.STATIC_FALLBACK)

    def test_is_tradeable_raises_on_unknown_rather_than_reporting_closed(self):
        """A caller reading the attribute directly must not see a silent False."""
        info = self.mgr.get_session_info("XLON", datetime.date(2026, 7, 15))
        with self.assertRaises(CalendarDataUnavailable):
            _ = info.is_tradeable

    def test_muhurat_sunday_is_not_reported_closed(self):
        """NSE holds a live Muhurat session on Sunday 2026-11-08.

        The weekmask alone would call it WEEKEND_CLOSED. Its timings are not in
        the annual holiday calendar, so the honest answer is "unknown, go look" --
        never a confident "closed" on a day the market is open.
        """
        muhurat = datetime.date(2026, 11, 8)
        self.assertEqual(muhurat.weekday(), 6)

        info = self.mgr.get_session_info("XNSE", muhurat)
        self.assertEqual(info.status, SessionStatus.UNKNOWN_NO_CALENDAR_DATA)
        self.assertNotEqual(info.status, SessionStatus.WEEKEND_CLOSED)
        self.assertIn("Special session", info.notes)

        with self.assertRaises(CalendarDataUnavailable):
            self.mgr.is_trading_day("XNSE", muhurat)

    def test_ordinary_sunday_is_still_weekend_closed(self):
        """The special-session table must not blanket-unknown every Sunday."""
        info = self.mgr.get_session_info("XNSE", datetime.date(2026, 11, 15))
        self.assertEqual(info.status, SessionStatus.WEEKEND_CLOSED)


class TestCalendarLibraryPath(unittest.TestCase):
    """The library path, previously untested because neither library is installed."""

    def _nyse_like_lib(self):
        sessions = {
            datetime.date(2026, 7, 15),
            datetime.date(2026, 11, 27),
        }
        opens = {
            datetime.date(2026, 7, 15): _utc(2026, 7, 15, 13, 30),
            datetime.date(2026, 11, 27): _utc(2026, 11, 27, 14, 30),
        }
        closes = {
            datetime.date(2026, 7, 15): _utc(2026, 7, 15, 20, 0),
            datetime.date(2026, 11, 27): _utc(2026, 11, 27, 18, 0),
        }
        cal = _FakeCalendar(
            "XNYS",
            "1111100",
            sessions,
            opens,
            closes,
            early_closes=[datetime.date(2026, 11, 27)],
        )
        return _FakeLib({"XNYS": cal})

    def test_library_regular_session_is_used_and_labelled(self):
        mgr = GlobalExchangeCalendarManager(calendar_lib=self._nyse_like_lib())
        info = mgr.get_session_info("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(info.source, CalendarSource.EXCHANGE_CALENDARS)
        self.assertEqual(info.status, SessionStatus.REGULAR_SESSION)
        self.assertEqual(info.open_utc, _utc(2026, 7, 15, 13, 30))

    def test_early_close_detected_without_is_half_day(self):
        """Regression: the previous code called `cal.is_half_day()`.

        `ExchangeCalendar` has no such method, so the call raised AttributeError,
        was swallowed, and every library lookup fell through to the static tables.
        This fake exposes only the real API, so a return to `is_half_day` fails.
        """
        lib = self._nyse_like_lib()
        self.assertFalse(hasattr(lib.get_calendar("XNYS"), "is_half_day"))

        mgr = GlobalExchangeCalendarManager(calendar_lib=lib)
        info = mgr.get_session_info("XNYS", datetime.date(2026, 11, 27))
        self.assertEqual(info.source, CalendarSource.EXCHANGE_CALENDARS)
        self.assertEqual(info.status, SessionStatus.HALF_DAY_EARLY_CLOSE)
        self.assertEqual(info.close_utc, _utc(2026, 11, 27, 18, 0))

    def test_sunday_trading_exchange_is_not_forced_closed(self):
        """Regression for the hardcoded `weekday() >= 5` short-circuit.

        The Saudi Exchange trades Sunday-Thursday (weekmask "1111001"), as did the
        Tel Aviv Stock Exchange until 2026-01-05. The previous implementation
        returned WEEKEND_CLOSED for those Sunday sessions before ever consulting
        the calendar library.
        """
        sunday = datetime.date(2026, 7, 12)
        self.assertEqual(sunday.weekday(), 6)
        cal = _FakeCalendar(
            "XSAU",
            "1111001",
            {sunday},
            {sunday: _utc(2026, 7, 12, 7, 0)},   # 10:00 Asia/Riyadh (UTC+3)
            {sunday: _utc(2026, 7, 12, 12, 0)},  # 15:00 Asia/Riyadh
        )
        mgr = GlobalExchangeCalendarManager(calendar_lib=_FakeLib({"XSAU": cal}))

        info = mgr.get_session_info("XSAU", sunday)
        self.assertEqual(info.status, SessionStatus.REGULAR_SESSION)
        self.assertEqual(info.open_utc, _utc(2026, 7, 12, 7, 0))
        self.assertTrue(mgr.is_trading_day("XSAU", sunday))

    def test_friday_closed_for_a_sunday_thursday_exchange(self):
        friday = datetime.date(2026, 7, 17)
        self.assertEqual(friday.weekday(), 4)
        cal = _FakeCalendar("XSAU", "1111001", set(), {}, {})
        mgr = GlobalExchangeCalendarManager(calendar_lib=_FakeLib({"XSAU": cal}))

        info = mgr.get_session_info("XSAU", friday)
        self.assertEqual(info.status, SessionStatus.WEEKEND_CLOSED)
        self.assertFalse(mgr.is_trading_day("XSAU", friday))

    def test_non_session_on_a_trading_weekday_is_a_holiday_not_a_weekend(self):
        cal = _FakeCalendar("XNYS", "1111100", set(), {}, {})
        mgr = GlobalExchangeCalendarManager(calendar_lib=_FakeLib({"XNYS": cal}))

        info = mgr.get_session_info("XNYS", datetime.date(2026, 1, 1))
        self.assertEqual(info.status, SessionStatus.FULL_DAY_HOLIDAY)

    def test_unknown_calendar_name_degrades_without_fabricating(self):
        """XNSE has no calendar in exchange_calendars; XLON is simply absent here."""
        mgr = GlobalExchangeCalendarManager(calendar_lib=_FakeLib({}))

        nse = mgr.get_session_info("XNSE", datetime.date(2026, 1, 26))
        self.assertEqual(nse.source, CalendarSource.STATIC_FALLBACK)
        self.assertEqual(nse.status, SessionStatus.FULL_DAY_HOLIDAY)

        lon = mgr.get_session_info("XLON", datetime.date(2026, 7, 15))
        self.assertEqual(lon.status, SessionStatus.UNKNOWN_NO_CALENDAR_DATA)

    def test_naive_library_timestamp_is_marked_utc(self):
        day = datetime.date(2026, 7, 15)
        cal = _FakeCalendar(
            "XNYS",
            "1111100",
            {day},
            {day: datetime.datetime(2026, 7, 15, 13, 30)},  # naive
            {day: datetime.datetime(2026, 7, 15, 20, 0)},
        )
        mgr = GlobalExchangeCalendarManager(calendar_lib=_FakeLib({"XNYS": cal}))

        info = mgr.get_session_info("XNYS", day)
        self.assertEqual(info.open_utc, _utc(2026, 7, 15, 13, 30))
        self.assertIsNotNone(info.open_utc.tzinfo)


class TestInstrumentExchangeResolution(unittest.TestCase):

    def setUp(self):
        self.mgr = GlobalExchangeCalendarManager()

    def test_adr_resolves_to_its_listing_venue(self):
        self.assertEqual(self.mgr.map_instrument_to_exchange("INFY"), "XNYS")

    def test_suffix_resolves_to_home_venue(self):
        self.assertEqual(self.mgr.map_instrument_to_exchange("INFY.NS"), "XNSE")
        self.assertEqual(self.mgr.map_instrument_to_exchange("RELIANCE.BO"), "XBOM")
        self.assertEqual(self.mgr.map_instrument_to_exchange("VOD.L"), "XLON")
        self.assertEqual(self.mgr.map_instrument_to_exchange("7203.T"), "XTKS")

    def test_unknown_symbol_raises_instead_of_defaulting_to_nyse(self):
        """Regression: every unresolvable symbol previously returned XNYS."""
        for symbol in ("SOMECO", "", "   "):
            with self.assertRaises(UnresolvedInstrumentExchange):
                self.mgr.map_instrument_to_exchange(symbol)

    def test_substring_adr_heuristic_is_gone(self):
        """Regression: `"ADR" in symbol` matched unrelated tickers like PADRE."""
        with self.assertRaises(UnresolvedInstrumentExchange):
            self.mgr.map_instrument_to_exchange("PADRE")

    def test_caller_supplied_listing_venues_override_defaults(self):
        mgr = GlobalExchangeCalendarManager(adr_listing_venues={"SOMECO": "XLON"})
        self.assertEqual(mgr.map_instrument_to_exchange("someco"), "XLON")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.mgr = GlobalExchangeCalendarManager()

    def test_datetime_is_rejected_at_the_boundary(self):
        """An instant maps to different dates in different exchange zones."""
        with self.assertRaises(TypeError):
            self.mgr.get_session_info(
                "XNYS", datetime.datetime(2026, 7, 15, 23, 30, tzinfo=UTC)
            )

    def test_non_date_is_rejected(self):
        with self.assertRaises(TypeError):
            self.mgr.get_session_info("XNYS", "2026-07-15")

    def test_empty_exchange_code_is_rejected(self):
        with self.assertRaises(ValueError):
            self.mgr.get_session_info("  ", datetime.date(2026, 7, 15))

    def test_exchange_code_is_case_and_whitespace_insensitive(self):
        info = self.mgr.get_session_info(" xnys ", datetime.date(2026, 1, 1))
        self.assertEqual(info.status, SessionStatus.FULL_DAY_HOLIDAY)


class TestModuleLevelHelpers(unittest.TestCase):

    def test_is_trading_day_on_a_holiday(self):
        self.assertFalse(is_trading_day("XNYS", datetime.date(2026, 1, 1)))

    def test_session_open_close_returns_none_when_closed(self):
        open_utc, close_utc = session_open_close("XNYS", datetime.date(2026, 1, 1))
        self.assertIsNone(open_utc)
        self.assertIsNone(close_utc)

    def test_session_open_close_returns_utc_bounds_when_open(self):
        open_utc, close_utc = session_open_close("XNYS", datetime.date(2026, 7, 15))
        self.assertEqual(open_utc, _utc(2026, 7, 15, 13, 30))
        self.assertEqual(close_utc, _utc(2026, 7, 15, 20, 0))

    def test_helpers_reject_datetime_input(self):
        with self.assertRaises(TypeError):
            is_trading_day("XNYS", datetime.datetime(2026, 7, 15, 12, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
