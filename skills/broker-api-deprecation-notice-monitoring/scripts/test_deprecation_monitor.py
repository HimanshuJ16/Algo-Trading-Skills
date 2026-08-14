"""
Unit tests for broker-api-deprecation-notice-monitoring skill.

Expected values are derived independently of the implementation: day counts are
calendar arithmetic done by hand against a frozen clock of 2026-11-01T00:00:00Z, and
the RFC 9745 timestamp is the example from the RFC itself.
"""
import datetime
import logging
import threading
import unittest
from unittest.mock import MagicMock

from deprecation_monitor import (
    BrokerDeprecationMonitor,
    DeprecationUrgency,
)

UTC = datetime.timezone.utc


def utc(year, month, day, hour=0, minute=0, second=0):
    return datetime.datetime(year, month, day, hour, minute, second, tzinfo=UTC)


class MonitorTestBase(unittest.TestCase):
    def setUp(self):
        # Frozen clock: Sunday 1 November 2026, 00:00:00 UTC.
        self.now = utc(2026, 11, 1)
        self.mock_callback = MagicMock()
        self.monitor = BrokerDeprecationMonitor(
            now_fn=lambda: self.now,
            alert_callback=self.mock_callback,
        )


class TestHeaderDetection(MonitorTestBase):
    def test_rfc8594_sunset_header_detection(self):
        # 1 Nov -> 5 Nov 2026 is 4 days.
        headers = {
            "Sunset": "Thu, 05 Nov 2026 00:00:00 GMT",
            "Deprecation": "true",
            "X-API-Deprecation-Warning": "V1 endpoint will be retired.",
            "Link": '<https://api.alpaca.markets/sunset>; rel="sunset"',
        }
        notice = self.monitor.inspect_http_headers("alpaca", "/v1/orders", headers)

        self.assertIsNotNone(notice)
        self.assertEqual(notice.days_remaining, 4)
        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)
        self.assertEqual(notice.reference_link, "https://api.alpaca.markets/sunset")
        self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 5))
        self.assertEqual(notice.evaluated_at_utc, self.now)
        self.mock_callback.assert_called_once_with(notice)

    def test_sunset_30_days_warning(self):
        # 1 Nov -> 25 Nov 2026 is 24 days.
        notice = self.monitor.inspect_http_headers(
            "ibkr", "/v1/marketdata", {"Sunset": "2026-11-25"}
        )
        self.assertEqual(notice.days_remaining, 24)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)
        self.mock_callback.assert_called_once_with(notice)

    def test_no_deprecation_signal_returns_none(self):
        notice = self.monitor.inspect_http_headers(
            "ibkr", "/v1/quotes", {"Content-Type": "application/json"}
        )
        self.assertIsNone(notice)
        self.assertEqual(self.monitor.active_notices, [])
        self.mock_callback.assert_not_called()

    def test_urgency_thresholds_at_exact_boundaries(self):
        # Hand-computed: Nov has 30 days, so 1 Nov + 30d = 1 Dec, + 31d = 2 Dec.
        cases = [
            ("2026-11-08", 7, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT),
            ("2026-11-09", 8, DeprecationUrgency.WARNING_30_DAYS),
            ("2026-12-01", 30, DeprecationUrgency.WARNING_30_DAYS),
            ("2026-12-02", 31, DeprecationUrgency.NOTICE),
        ]
        for raw, expected_days, expected_urgency in cases:
            with self.subTest(sunset=raw):
                monitor = BrokerDeprecationMonitor(now_fn=lambda: self.now)
                notice = monitor.inspect_http_headers("b", "/e", {"Sunset": raw})
                self.assertEqual(notice.days_remaining, expected_days)
                self.assertEqual(notice.urgency, expected_urgency)

    def test_sunset_within_24h_is_critical_not_expired(self):
        """Regression: a sunset 23 hours away has 0 whole days left but is not expired.

        The old implementation used timedelta.days, which floors 0.96 days to 0 and
        then matched the `days_remaining <= 0` expiry branch, telling the desk the
        migration window had closed while a full day of it remained.
        """
        notice = self.monitor.inspect_http_headers(
            "b", "/e", {"Sunset": "Sun, 01 Nov 2026 23:00:00 GMT"}
        )
        self.assertEqual(notice.days_remaining, 0)
        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)

    def test_past_sunset_is_expired_with_negative_days(self):
        notice = self.monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-10-30"})
        self.assertEqual(notice.urgency, DeprecationUrgency.EXPIRED)
        self.assertEqual(notice.days_remaining, -2)

    def test_sunset_exactly_now_is_expired(self):
        notice = self.monitor.inspect_http_headers(
            "b", "/e", {"Sunset": "Sun, 01 Nov 2026 00:00:00 GMT"}
        )
        self.assertEqual(notice.urgency, DeprecationUrgency.EXPIRED)
        self.assertEqual(notice.days_remaining, 0)


class TestDeprecationHeader(MonitorTestBase):
    def test_rfc9745_structured_date_parsed(self):
        """RFC 9745 Section 2.1: the value MUST be a Structured Field Date.

        @1688169599 is the RFC's own example, 30 June 2023 23:59:59 UTC.
        """
        parsed = BrokerDeprecationMonitor.parse_deprecation_header("@1688169599")
        self.assertEqual(parsed, utc(2023, 6, 30, 23, 59, 59))

    def test_deprecation_date_recorded_on_notice(self):
        notice = self.monitor.inspect_http_headers(
            "b", "/v2/e", {"Deprecation": "@1688169599"}
        )
        self.assertIsNotNone(notice)
        self.assertEqual(notice.deprecation_date_utc, utc(2023, 6, 30, 23, 59, 59))
        # A deprecation date is not a removal date, so urgency stays at NOTICE.
        self.assertIsNone(notice.sunset_date_utc)
        self.assertEqual(notice.urgency, DeprecationUrgency.NOTICE)

    def test_legacy_boolean_deprecation_still_raises_a_notice(self):
        """`Deprecation: true` predates RFC 9745 but is still emitted in the wild."""
        self.assertIsNone(BrokerDeprecationMonitor.parse_deprecation_header("true"))
        notice = self.monitor.inspect_http_headers("b", "/v1/e", {"Deprecation": "true"})
        self.assertIsNotNone(notice)
        self.assertIsNone(notice.deprecation_date_utc)

    def test_sunset_before_deprecation_is_logged_as_rfc_violation(self):
        # RFC 9745 Section 4: Sunset MUST NOT be earlier than Deprecation.
        headers = {"Deprecation": "@1798761599", "Sunset": "2026-11-25"}
        with self.assertLogs("deprecation_monitor", level=logging.WARNING) as captured:
            self.monitor.inspect_http_headers("b", "/e", headers)
        self.assertTrue(any("RFC 9745" in line for line in captured.output))

    def test_out_of_range_deprecation_timestamp_returns_none(self):
        self.assertIsNone(
            BrokerDeprecationMonitor.parse_deprecation_header("@999999999999999999999")
        )


class TestDateParsing(unittest.TestCase):
    def test_all_three_http_date_formats(self):
        """RFC 8594 Section 3 defines Sunset as HTTP-date, which admits three forms."""
        expected = utc(1994, 11, 6, 8, 49, 37)
        for raw in (
            "Sun, 06 Nov 1994 08:49:37 GMT",  # IMF-fixdate
            "Sunday, 06-Nov-94 08:49:37 GMT",  # obsolete RFC 850
            "Sun Nov  6 08:49:37 1994",  # asctime
        ):
            with self.subTest(raw=raw):
                self.assertEqual(BrokerDeprecationMonitor.parse_http_date(raw), expected)

    def test_non_utc_offsets_are_converted_not_dropped(self):
        # 25 Nov 00:00 +05:30 is 24 Nov 18:30 UTC. Truncating to the date prefix
        # would place it 5.5 hours later than the broker actually announced.
        self.assertEqual(
            BrokerDeprecationMonitor.parse_http_date("2026-11-25T00:00:00+05:30"),
            utc(2026, 11, 24, 18, 30),
        )
        self.assertEqual(
            BrokerDeprecationMonitor.parse_http_date("Wed, 25 Nov 2026 00:00:00 +0530"),
            utc(2026, 11, 24, 18, 30),
        )

    def test_iso_datetime_with_z_keeps_time_of_day(self):
        self.assertEqual(
            BrokerDeprecationMonitor.parse_http_date("2026-11-25T12:00:00Z"),
            utc(2026, 11, 25, 12),
        )

    def test_iso_date_with_trailing_text_falls_back_to_date_prefix(self):
        self.assertEqual(
            BrokerDeprecationMonitor.parse_http_date("2026-11-25 (subject to change)"),
            utc(2026, 11, 25),
        )

    def test_unparseable_values_return_none(self):
        for raw in ("", "   ", "true", "soon", "2026-13-45", None, 12345):
            with self.subTest(raw=raw):
                self.assertIsNone(BrokerDeprecationMonitor.parse_http_date(raw))

    def test_impossible_calendar_date_in_http_format_returns_none(self):
        self.assertIsNone(
            BrokerDeprecationMonitor.parse_http_date("Wed, 32 Nov 2026 00:00:00 GMT")
        )


class TestLinkHeader(MonitorTestBase):
    def test_link_header_extraction_only(self):
        headers = {
            "Link": (
                '<https://api.exchange.com/docs/deprecation>; rel="deprecation", '
                '<https://api.exchange.com/docs/sunset>; rel="sunset"'
            )
        }
        notice = self.monitor.inspect_http_headers("binance", "/v3/depth", headers)
        self.assertIsNotNone(notice)
        self.assertEqual(notice.reference_link, "https://api.exchange.com/docs/sunset")
        self.assertIsNone(notice.days_remaining)
        self.assertEqual(notice.urgency, DeprecationUrgency.NOTICE)

    def test_uri_containing_a_comma_is_not_truncated(self):
        """Regression: splitting the header on ',' destroyed URIs containing commas."""
        link = '<https://api.example.com/docs?tags=v1,legacy>; rel="sunset"'
        self.assertEqual(
            BrokerDeprecationMonitor.extract_sunset_link(link),
            "https://api.example.com/docs?tags=v1,legacy",
        )

    def test_rel_is_matched_as_a_whole_token(self):
        multi = '<https://a.example/s>; rel="sunset alternate"'
        self.assertEqual(
            BrokerDeprecationMonitor.extract_sunset_link(multi), "https://a.example/s"
        )
        # "presunset" contains "sunset" but is a different relation type.
        self.assertIsNone(
            BrokerDeprecationMonitor.extract_sunset_link(
                '<https://a.example/p>; rel="presunset"'
            )
        )

    def test_unquoted_and_single_quoted_rel(self):
        self.assertEqual(
            BrokerDeprecationMonitor.extract_sunset_link("<https://a.example/s>; rel=sunset"),
            "https://a.example/s",
        )

    def test_deprecation_rel_alone_still_raises_a_notice(self):
        # RFC 9745 Section 3 defines the `deprecation` relation type.
        headers = {"Link": '<https://dev.example.com/deprecation>; rel="deprecation"'}
        notice = self.monitor.inspect_http_headers("b", "/e", headers)
        self.assertIsNotNone(notice)
        self.assertEqual(notice.reference_link, "https://dev.example.com/deprecation")

    def test_unrelated_link_header_is_not_a_deprecation_signal(self):
        headers = {"Link": '<https://api.example.com/page/2>; rel="next"'}
        self.assertIsNone(self.monitor.inspect_http_headers("b", "/e", headers))


class TestChangelogParsing(MonitorTestBase):
    def test_changelog_rss_parsing(self):
        title = "Deprecation Notice: Legacy WebSockets V1"
        content = "The legacy WebSockets V1 feed is deprecated and will sunset on 2026-11-20."
        notice = self.monitor.parse_changelog_entry(
            "coinbase", title, content, self.now, "https://blog.coinbase.com/update"
        )

        self.assertIsNotNone(notice)
        self.assertEqual(notice.broker_name, "coinbase")
        self.assertEqual(notice.source, "CHANGELOG_FEED")
        self.assertEqual(notice.days_remaining, 19)  # 1 Nov -> 20 Nov
        self.assertEqual(notice.reference_link, "https://blog.coinbase.com/update")
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)

    def test_publication_date_is_not_mistaken_for_the_sunset_date(self):
        """Regression: the old parser took the first ISO date in the text.

        Here that was the publication date, which made a real 19-day deadline read as
        EXPIRED — and once a notice is EXPIRED the desk stops treating it as work
        that still has to be scheduled.
        """
        content = (
            "Posted 2026-01-05. The v1 order endpoint is deprecated and "
            "will sunset on 2026-11-20."
        )
        notice = self.monitor.parse_changelog_entry(
            "ibkr", "Breaking change", content, utc(2026, 1, 5)
        )
        self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 20))
        self.assertEqual(notice.days_remaining, 19)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)

    def test_nearest_future_deadline_wins_over_a_later_support_window(self):
        """Regression: a later date mentioned first must not mask a nearer deadline.

        Taking the first match here yields 2030-01-01 and downgrades a 13-day
        deadline to NOTICE — the silent failure this skill exists to prevent.
        """
        content = (
            "Our v3 API is supported through 2030-01-01. The v1 API is deprecated "
            "and will be retired on 2026-11-14."
        )
        notice = self.monitor.parse_changelog_entry(
            "b", "Retirement schedule", content, utc(2026, 10, 1)
        )
        self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 14))
        self.assertEqual(notice.days_remaining, 13)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)
        self.assertIn("candidate dates", notice.message)

    def test_long_form_dates_are_recognised(self):
        """Real broker sunset notices write prose dates, not ISO dates.

        Alpaca's Market Data API v1 notice is the reference case: "sunsetting on
        March 15, 2022".
        """
        for content in (
            "The v1 endpoints are deprecated and will sunset on November 20, 2026.",
            "The v1 endpoints are deprecated and will sunset on 20 November 2026.",
            "The v1 endpoints are deprecated and will sunset on Nov 20, 2026.",
            "The v1 endpoints are deprecated and will sunset on November 20th, 2026.",
        ):
            with self.subTest(content=content):
                monitor = BrokerDeprecationMonitor(now_fn=lambda: self.now)
                notice = monitor.parse_changelog_entry("b", "Notice", content, self.now)
                self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 20))
                self.assertEqual(notice.days_remaining, 19)

    def test_ambiguous_numeric_dates_are_not_guessed(self):
        # 11/12/2026 is 11 December or 12 November depending on locale; guessing
        # wrong moves the deadline by a month, so no date is claimed at all.
        notice = self.monitor.parse_changelog_entry(
            "b", "Notice", "The v1 API is deprecated as of 11/12/2026.", self.now
        )
        self.assertIsNone(notice.sunset_date_utc)
        self.assertEqual(notice.urgency, DeprecationUrgency.NOTICE)
        self.assertIn("no sunset date found", notice.message)

    def test_impossible_dates_in_prose_are_ignored(self):
        notice = self.monitor.parse_changelog_entry(
            "b",
            "Notice",
            "Deprecated; typo dates 2026-13-45 and February 30, 2026 ignored, "
            "real sunset 2026-11-20.",
            self.now,
        )
        self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 20))

    def test_entry_without_keywords_is_ignored(self):
        notice = self.monitor.parse_changelog_entry(
            "b", "Performance improvements", "We made things faster.", self.now
        )
        self.assertIsNone(notice)
        self.assertEqual(self.monitor.active_notices, [])

    def test_keyword_variants_are_matched(self):
        for text in ("deprecating v1", "v1 will be retired", "end-of-life for v1"):
            with self.subTest(text=text):
                monitor = BrokerDeprecationMonitor(now_fn=lambda: self.now)
                self.assertIsNotNone(
                    monitor.parse_changelog_entry("b", "t", text, self.now)
                )

    def test_naive_publish_date_is_accepted_as_utc(self):
        notice = self.monitor.parse_changelog_entry(
            "b",
            "Notice",
            "deprecated, sunset 2026-11-20",
            datetime.datetime(2026, 11, 1),
        )
        self.assertEqual(notice.sunset_date_utc, utc(2026, 11, 20))

    def test_distinct_entries_from_one_broker_are_tracked_separately(self):
        """Regression: every changelog notice used the key `broker:GLOBAL_FEED`.

        The second announcement evicted the first, so a broker retiring two
        endpoints showed one outstanding deprecation.
        """
        self.monitor.parse_changelog_entry(
            "ibkr", "Deprecating /v1/orders", "sunset 2026-11-10", self.now,
            "https://ibkr.example/1",
        )
        self.monitor.parse_changelog_entry(
            "ibkr", "Deprecating /v1/quotes", "sunset 2026-12-20", self.now,
            "https://ibkr.example/2",
        )
        self.assertEqual(len(self.monitor.active_notices), 2)
        self.assertEqual(self.mock_callback.call_count, 2)

    def test_repeated_polling_of_one_entry_alerts_once(self):
        for _ in range(3):
            self.monitor.parse_changelog_entry(
                "ibkr", "Deprecating /v1/orders", "sunset 2026-11-10", self.now,
                "https://ibkr.example/1",
            )
        self.assertEqual(len(self.monitor.active_notices), 1)
        self.assertEqual(self.mock_callback.call_count, 1)

    def test_entries_without_links_are_still_distinguished(self):
        self.monitor.parse_changelog_entry("b", "Deprecating A", "sunset 2026-11-10", self.now)
        self.monitor.parse_changelog_entry("b", "Deprecating B", "sunset 2026-11-11", self.now)
        self.assertEqual(len(self.monitor.active_notices), 2)


class TestAlertDeduplication(MonitorTestBase):
    def test_unchanged_notice_alerts_once(self):
        headers = {"Sunset": "2026-11-25"}
        self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)
        self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        self.assertEqual(len(self.monitor.active_notices), 1)
        self.assertEqual(self.mock_callback.call_count, 1)

    def test_escalation_re_alerts(self):
        headers = {"Sunset": "2026-11-25"}
        self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)
        self.now = utc(2026, 11, 20)  # 5 days out -> CRITICAL
        notice = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)
        self.assertEqual(self.mock_callback.call_count, 2)

    def test_de_escalation_alone_does_not_re_alert(self):
        """workflows.md promises escalation-only alerting; the old code alerted on
        any urgency change, including downgrades."""
        headers = {"Sunset": "2026-11-25"}
        self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)
        self.now = utc(2026, 10, 1)  # further from the sunset -> NOTICE
        notice = self.monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        self.assertEqual(notice.urgency, DeprecationUrgency.NOTICE)
        self.assertEqual(self.mock_callback.call_count, 1)
        # The de-escalated notice is still what the registry reports.
        self.assertEqual(self.monitor.active_notices[0].urgency, DeprecationUrgency.NOTICE)

    def test_broker_moving_the_sunset_date_re_alerts(self):
        self.monitor.inspect_http_headers("ibkr", "/v1/md", {"Sunset": "2026-11-25"})
        self.monitor.inspect_http_headers("ibkr", "/v1/md", {"Sunset": "2026-12-25"})
        self.assertEqual(self.mock_callback.call_count, 2)
        self.assertEqual(len(self.monitor.active_notices), 1)

    def test_distinct_endpoints_are_tracked_separately(self):
        self.monitor.inspect_http_headers("ibkr", "/v1/a", {"Sunset": "2026-11-25"})
        self.monitor.inspect_http_headers("ibkr", "/v1/b", {"Sunset": "2026-11-25"})
        self.assertEqual(len(self.monitor.active_notices), 2)


class TestLivePathSafety(MonitorTestBase):
    """inspect_http_headers runs on the same thread as live orders. It must never
    raise into the caller, whatever it is handed."""

    def test_naive_now_fn_does_not_crash_the_request_path(self):
        """Regression: `now_fn=datetime.datetime.now` raised TypeError on subtraction."""
        monitor = BrokerDeprecationMonitor(now_fn=lambda: datetime.datetime(2026, 11, 1))
        with self.assertLogs("deprecation_monitor", level=logging.WARNING):
            notice = monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-25"})
        self.assertEqual(notice.days_remaining, 24)
        self.assertEqual(notice.urgency, DeprecationUrgency.WARNING_30_DAYS)

    def test_non_utc_aware_now_fn_is_normalised(self):
        ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        monitor = BrokerDeprecationMonitor(
            now_fn=lambda: datetime.datetime(2026, 11, 1, 5, 30, tzinfo=ist)
        )
        notice = monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-25"})
        self.assertEqual(notice.days_remaining, 24)

    def test_tzinfo_without_an_offset_does_not_leak_host_local_time(self):
        """A tzinfo whose utcoffset() is None leaves the datetime effectively naive.

        Testing only `tzinfo is not None` sends it to astimezone(), which resolves it
        against the *host's* local timezone — so the same code produces different
        countdowns on a UTC server and an IST laptop.
        """

        class OffsetlessTZ(datetime.tzinfo):
            def utcoffset(self, dt):
                return None

        monitor = BrokerDeprecationMonitor(
            now_fn=lambda: datetime.datetime(2026, 11, 1, tzinfo=OffsetlessTZ())
        )
        with self.assertLogs("deprecation_monitor", level=logging.WARNING):
            notice = monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-25"})
        self.assertEqual(notice.evaluated_at_utc, utc(2026, 11, 1))
        self.assertEqual(notice.days_remaining, 24)

    def test_none_headers_returns_none_without_raising(self):
        """Regression: a None header mapping raised AttributeError at the call site."""
        self.assertIsNone(self.monitor.inspect_http_headers("b", "/e", None))

    def test_non_mapping_headers_returns_none_without_raising(self):
        with self.assertLogs("deprecation_monitor", level=logging.WARNING):
            self.assertIsNone(
                self.monitor.inspect_http_headers("b", "/e", ["Sunset", "2026-11-25"])
            )

    def test_non_string_header_value_does_not_manufacture_a_notice(self):
        """Regression: str(None) is the truthy literal 'None', which used to be read
        as a deprecation signal the broker never sent."""
        self.assertIsNone(self.monitor.inspect_http_headers("b", "/e", {"Sunset": None}))
        self.assertIsNone(self.monitor.inspect_http_headers("b", "/e", {"Sunset": "  "}))

    def test_alert_callback_failure_is_contained(self):
        boom = MagicMock(side_effect=RuntimeError("pagerduty down"))
        monitor = BrokerDeprecationMonitor(now_fn=lambda: self.now, alert_callback=boom)
        with self.assertLogs("deprecation_monitor", level=logging.ERROR):
            notice = monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-25"})
        self.assertIsNotNone(notice)
        self.assertEqual(len(monitor.active_notices), 1)

    def test_internal_failure_is_logged_and_swallowed(self):
        monitor = BrokerDeprecationMonitor(
            now_fn=MagicMock(side_effect=RuntimeError("clock source failed"))
        )
        with self.assertLogs("deprecation_monitor", level=logging.ERROR):
            self.assertIsNone(
                monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-25"})
            )

    def test_changelog_failure_does_not_kill_the_poll_loop(self):
        with self.assertLogs("deprecation_monitor", level=logging.ERROR):
            self.assertIsNone(
                self.monitor.parse_changelog_entry("b", "t", "deprecated", "not-a-date")
            )


class TestConfiguration(unittest.TestCase):
    def test_inverted_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            BrokerDeprecationMonitor(critical_days=45, warning_days=30)

    def test_negative_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            BrokerDeprecationMonitor(critical_days=-1)

    def test_custom_thresholds_apply(self):
        monitor = BrokerDeprecationMonitor(
            now_fn=lambda: utc(2026, 11, 1), critical_days=14, warning_days=60
        )
        notice = monitor.inspect_http_headers("b", "/e", {"Sunset": "2026-11-10"})
        self.assertEqual(notice.days_remaining, 9)
        self.assertEqual(notice.urgency, DeprecationUrgency.CRITICAL_SUNSET_IMMINENT)


class TestConcurrency(unittest.TestCase):
    def test_concurrent_inspection_alerts_exactly_once(self):
        """The previous test of this name started no threads.

        Twenty threads racing on the same endpoint must produce one registry entry
        and one alert; a check-then-set that is not atomic pages the on-call rota
        once per racing thread.
        """
        now = utc(2026, 11, 1)
        calls = []
        lock = threading.Lock()

        def record(notice):
            with lock:
                calls.append(notice)

        monitor = BrokerDeprecationMonitor(now_fn=lambda: now, alert_callback=record)
        headers = {"Sunset": "2026-11-25"}
        barrier = threading.Barrier(20)

        def worker():
            barrier.wait()
            monitor.inspect_http_headers("ibkr", "/v1/marketdata", headers)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(monitor.active_notices), 1)
        self.assertEqual(len(calls), 1)

    def test_concurrent_distinct_endpoints_all_registered(self):
        now = utc(2026, 11, 1)
        monitor = BrokerDeprecationMonitor(now_fn=lambda: now)
        barrier = threading.Barrier(20)

        def worker(index):
            barrier.wait()
            monitor.inspect_http_headers("ibkr", f"/v1/e{index}", {"Sunset": "2026-11-25"})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(monitor.active_notices), 20)


if __name__ == "__main__":
    unittest.main()
