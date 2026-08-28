"""Unit tests for systemd-supervision-for-trading-bots.

Tests target observable behaviour -- the payload that would go on the wire, the
finding codes an audit produces, the fault/closed distinction a healthcheck
draws -- rather than internal structure.

Tests marked REGRESSION name a specific defect in the pre-2.0.0 skill and assert
the input that used to pass now does not. Each fails against the old
implementation and passes against this one, which is what makes them worth
keeping:

* the shipped unit file put StartLimitIntervalSec= in [Service], where systemd
  ignores it, and the old substring-scanning validator called that unit valid;
* a status string containing a newline was interpolated straight into the
  sd_notify datagram, appending an arbitrary protocol field;
* the watchdog ping interval was documented as a constant instead of derived
  from WATCHDOG_USEC.

Everything here runs without a systemd, without root, and without AF_UNIX, so it
passes on Windows and macOS as well as Linux. Transport is exercised by
substituting a recording sender, which is what keeps the payload assertions
honest on a platform where the real socket cannot exist.
"""

import datetime
import logging
import os
import unittest
from unittest.mock import Mock

from supervision_helper import (
    CODE_MISSING_MEMORY_LIMIT,
    CODE_MISSING_RESTART_POLICY,
    CODE_MISSING_STOP_TIMEOUT,
    CODE_MISSING_WATCHDOG,
    CODE_START_LIMIT_IGNORED_IN_SERVICE_SECTION,
    CODE_START_LIMIT_LEGACY_SERVICE_SECTION,
    CODE_START_LIMIT_UNREACHABLE,
    CODE_UNBOUNDED_RESTART_POLICY,
    CODE_UNPARSEABLE_DIRECTIVE,
    CODE_WATCHDOG_PINGS_IGNORED,
    HealthCheckResult,
    NotifyProtocolError,
    PreMarketHealthCheckError,
    SystemdSupervisionHelper,
    build_notify_message,
    parse_systemd_timespan,
    parse_unit_file,
)

# The module logs expected failures at ERROR/WARNING. Silence it so a passing
# run does not print alarming noise.
logging.getLogger("supervision_helper").setLevel(logging.CRITICAL)

UNIT_PATH = os.path.join(os.path.dirname(__file__), "trading-bot.service")

MINIMAL_GOOD_UNIT = """
[Unit]
Description=bot
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=notify
WatchdogSec=30
ExecStartPre=/opt/bot/healthcheck.sh
ExecStart=/opt/bot/venv/bin/python /opt/bot/main.py
Restart=on-failure
RestartSec=10
MemoryMax=1G
TimeoutStopSec=30
"""


class RecordingHelper(SystemdSupervisionHelper):
    """Helper whose transport records payloads instead of touching a socket."""

    def __init__(self, *args, send_result: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.sent = []
        self._send_result = send_result

    def sd_notify(self, state: str) -> bool:
        self.sent.append(state)
        return self._send_result


class TestNotifyPayloadConstruction(unittest.TestCase):
    """The wire format, asserted without a socket."""

    def test_payload_is_newline_separated_in_argument_order(self):
        self.assertEqual(
            build_notify_message([("READY", "1"), ("STATUS", "up")]),
            "READY=1\nSTATUS=up",
        )

    def test_ready_payload_carries_ready_and_status(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertTrue(helper.notify_ready("broker session live"))
        self.assertEqual(helper.sent, ["READY=1\nSTATUS=broker session live"])

    def test_watchdog_payload_is_exactly_the_keepalive(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertTrue(helper.notify_watchdog())
        self.assertEqual(helper.sent, ["WATCHDOG=1"])

    def test_stopping_payload_carries_stopping_and_status(self):
        helper = RecordingHelper("/run/systemd/notify")
        helper.notify_stopping("cancelling 3 open orders")
        self.assertEqual(helper.sent, ["STOPPING=1\nSTATUS=cancelling 3 open orders"])

    def test_extend_timeout_converts_seconds_to_microseconds(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertTrue(helper.notify_extend_timeout(12.5))
        self.assertEqual(helper.sent, ["EXTEND_TIMEOUT_USEC=12500000"])

    def test_extend_timeout_rejects_non_positive_and_infinite(self):
        helper = RecordingHelper("/run/systemd/notify")
        for bad in (0, -5, float("inf")):
            with self.subTest(seconds=bad):
                with self.assertRaises(ValueError):
                    helper.notify_extend_timeout(bad)
        self.assertEqual(helper.sent, [])

    def test_build_rejects_bad_field_names_and_empty_payload(self):
        with self.assertRaises(NotifyProtocolError):
            build_notify_message([])
        with self.assertRaises(NotifyProtocolError):
            build_notify_message([("ready", "1")])
        with self.assertRaises(NotifyProtocolError):
            build_notify_message([("READY", 1)])

    # -- REGRESSION ----------------------------------------------------------

    def test_regression_newline_in_status_cannot_inject_a_protocol_field(self):
        """A status string carrying \\n used to append a real sd_notify field.

        The old wrappers built the payload with an f-string, so a broker error
        message pasted into the status could smuggle in MAINPID= or WATCHDOG=1.
        """
        helper = RecordingHelper("/run/systemd/notify")
        helper.notify_stopping("order rejected\nMAINPID=1\nWATCHDOG=1")

        # The payload must still be exactly two protocol fields. The injected
        # text survives as inert characters inside the STATUS value, which is
        # harmless; what must not happen is MAINPID= or WATCHDOG= becoming
        # lines of their own, which is how systemd would read them as fields.
        lines = helper.sent[0].split("\n")
        self.assertEqual(len(lines), 2, f"extra protocol lines in {lines!r}")
        self.assertEqual(lines[0], "STOPPING=1")
        self.assertTrue(lines[1].startswith("STATUS="))

    def test_regression_control_characters_are_refused_by_the_primitive(self):
        with self.assertRaises(NotifyProtocolError):
            build_notify_message([("STATUS", "a\nREADY=1")])
        with self.assertRaises(NotifyProtocolError):
            build_notify_message([("STATUS", "a\x00b")])

    def test_long_status_is_truncated_not_dropped(self):
        helper = RecordingHelper("/run/systemd/notify")
        helper.notify_status("x" * 9000)
        value = helper.sent[0].split("=", 1)[1]
        self.assertLessEqual(len(value), 2048)
        self.assertTrue(value.startswith("x"))


class TestNotifyTransport(unittest.TestCase):
    """Transport behaviour that must hold with no systemd present."""

    def test_no_notify_socket_is_a_no_op_returning_false(self):
        helper = SystemdSupervisionHelper(env={})
        self.assertIsNone(helper.notify_socket_path)
        self.assertFalse(helper.sd_notify("WATCHDOG=1"))
        self.assertFalse(helper.notify_ready())
        self.assertFalse(helper.notify_watchdog())
        self.assertFalse(helper.notify_stopping())

    def test_notify_socket_is_read_from_the_injected_environment(self):
        helper = SystemdSupervisionHelper(env={"NOTIFY_SOCKET": "@abstract-sock"})
        self.assertEqual(helper.notify_socket_path, "@abstract-sock")

    def test_explicit_path_overrides_the_environment(self):
        helper = SystemdSupervisionHelper(
            "/run/explicit", env={"NOTIFY_SOCKET": "/run/from-env"}
        )
        self.assertEqual(helper.notify_socket_path, "/run/explicit")

    def test_unusable_socket_address_returns_false_instead_of_raising(self):
        """Neither '/' nor '@' is a form sd_notify(3) defines; refuse quietly."""
        helper = SystemdSupervisionHelper(env={"NOTIFY_SOCKET": "tcp://nope"})
        self.assertFalse(helper.sd_notify("WATCHDOG=1"))

    def test_send_failure_is_reported_not_raised(self):
        """A missing socket file must not propagate OSError into the trading loop."""
        helper = SystemdSupervisionHelper("/nonexistent/systemd/notify/socket")
        self.assertFalse(helper.sd_notify("WATCHDOG=1"))


class TestWatchdogInterval(unittest.TestCase):
    """REGRESSION: the ping cadence must come from systemd, not from a constant."""

    def test_interval_is_half_the_timeout_systemd_configured(self):
        helper = SystemdSupervisionHelper(env={"WATCHDOG_USEC": "30000000"})
        self.assertTrue(helper.watchdog_enabled())
        self.assertAlmostEqual(helper.watchdog_timeout_seconds(), 30.0)
        self.assertAlmostEqual(helper.watchdog_ping_interval_seconds(), 15.0)

    def test_interval_tracks_a_lowered_watchdogsec(self):
        """The documented 15s constant would be a death sentence at WatchdogSec=10."""
        helper = SystemdSupervisionHelper(env={"WATCHDOG_USEC": "10000000"})
        interval = helper.watchdog_ping_interval_seconds()
        self.assertAlmostEqual(interval, 5.0)
        self.assertLess(interval, 15.0)

    def test_watchdog_disabled_when_env_absent(self):
        helper = SystemdSupervisionHelper(env={})
        self.assertFalse(helper.watchdog_enabled())
        self.assertIsNone(helper.watchdog_timeout_seconds())
        self.assertIsNone(helper.watchdog_ping_interval_seconds())

    def test_watchdog_pid_belonging_to_another_process_disables_it(self):
        helper = SystemdSupervisionHelper(
            env={"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": str(os.getpid() + 1)}
        )
        self.assertFalse(helper.watchdog_enabled())

    def test_watchdog_pid_matching_this_process_enables_it(self):
        helper = SystemdSupervisionHelper(
            env={"WATCHDOG_USEC": "30000000", "WATCHDOG_PID": str(os.getpid())}
        )
        self.assertTrue(helper.watchdog_enabled())

    def test_malformed_or_non_positive_watchdog_env_is_treated_as_disabled(self):
        for value in ("not-a-number", "0", "-1"):
            with self.subTest(watchdog_usec=value):
                helper = SystemdSupervisionHelper(env={"WATCHDOG_USEC": value})
                self.assertFalse(helper.watchdog_enabled())

    def test_safety_factor_must_be_a_proper_fraction(self):
        helper = SystemdSupervisionHelper(env={"WATCHDOG_USEC": "30000000"})
        for bad in (0.0, 1.0, 1.5, -0.5):
            with self.subTest(factor=bad):
                with self.assertRaises(ValueError):
                    helper.watchdog_ping_interval_seconds(bad)


class TestProgressGatedWatchdog(unittest.TestCase):
    """The ping must vouch for the trading loop, not for the pinger."""

    def test_ping_sent_while_the_loop_is_progressing(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertTrue(
            helper.notify_watchdog_if_progressing(
                last_progress_monotonic=100.0, max_stall_seconds=20.0, now=105.0
            )
        )
        self.assertEqual(helper.sent, ["WATCHDOG=1"])

    def test_exactly_at_the_stall_threshold_still_pings(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertTrue(
            helper.notify_watchdog_if_progressing(100.0, 20.0, now=120.0)
        )
        self.assertEqual(helper.sent, ["WATCHDOG=1"])

    def test_stalled_loop_withholds_the_ping_so_the_watchdog_can_fire(self):
        helper = RecordingHelper("/run/systemd/notify")
        self.assertFalse(
            helper.notify_watchdog_if_progressing(100.0, 20.0, now=120.001)
        )
        self.assertEqual(helper.sent, [], "a wedged loop must not be vouched for")

    def test_non_positive_stall_budget_is_rejected(self):
        helper = RecordingHelper("/run/systemd/notify")
        for bad in (0, -1):
            with self.subTest(max_stall=bad):
                with self.assertRaises(ValueError):
                    helper.notify_watchdog_if_progressing(100.0, bad, now=101.0)


class TestPreMarketHealthcheck(unittest.TestCase):

    def test_all_checks_pass_on_a_trading_day(self):
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "key123", "BROKER_SECRET": "sec456"},
            broker_connectivity_fn=Mock(return_value=True),
            is_holiday_fn=Mock(return_value=False),
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertTrue(res.passed)
        self.assertFalse(res.is_fault)
        self.assertFalse(res.market_closed)
        self.assertEqual(res.as_of_date, datetime.date(2026, 7, 15))
        self.assertTrue(res.checks["secrets_present"])
        self.assertTrue(res.checks["not_market_holiday"])
        self.assertTrue(res.checks["broker_connectivity"])

    def test_missing_secret_is_a_fault(self):
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": ""},
            broker_connectivity_fn=Mock(return_value=True),
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertFalse(res.passed)
        self.assertTrue(res.is_fault)
        self.assertFalse(res.checks["secrets_present"])
        self.assertIn("BROKER_API_KEY", res.blocking_failures[0])
        self.assertIn("BROKER_SECRET", res.blocking_failures[0])

    def test_whitespace_only_secret_counts_as_missing(self):
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "   ", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=True),
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertFalse(res.checks["secrets_present"])

    def test_required_secret_names_are_configurable(self):
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"KITE_TOKEN": "t"},
            broker_connectivity_fn=Mock(return_value=True),
            as_of_date=datetime.date(2026, 7, 15),
            required_secrets=("KITE_TOKEN",),
        )
        self.assertTrue(res.passed)

    def test_holiday_is_not_a_fault(self):
        """The distinction that keeps a holiday out of the start rate limiter."""
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=True),
            is_holiday_fn=Mock(return_value=True),
            as_of_date=datetime.date(2026, 1, 26),
        )
        self.assertFalse(res.passed)
        self.assertTrue(res.market_closed)
        self.assertFalse(res.is_fault, "a holiday must not exit non-zero from ExecStartPre")

    def test_unreachable_broker_is_a_fault_with_a_stated_reason(self):
        """Returning False, not raising, used to produce a failure with no detail."""
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=False),
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertFalse(res.passed)
        self.assertTrue(res.is_fault)
        self.assertTrue(any("unreachable" in d for d in res.details))

    def test_probe_that_raises_is_caught_and_recorded(self):
        def boom():
            raise ConnectionResetError("broker closed the socket")

        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=boom,
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertFalse(res.checks["broker_connectivity"])
        self.assertTrue(res.is_fault)
        self.assertIn("ConnectionResetError", res.blocking_failures[0])

    def test_calendar_failure_fails_closed_rather_than_assuming_open(self):
        def broken_calendar(_date):
            raise RuntimeError("calendar service 503")

        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=True),
            is_holiday_fn=broken_calendar,
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertFalse(res.checks["not_market_holiday"])
        self.assertTrue(res.is_fault)
        self.assertFalse(res.market_closed, "unknown is not the same as closed")

    def test_exchange_timezone_decides_the_date_asked_about(self):
        """REGRESSION: the host's local date is the wrong question to ask."""
        seen = []

        def record(date):
            seen.append(date)
            return False

        tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=True),
            is_holiday_fn=record,
            exchange_timezone=tz,
        )
        self.assertEqual(seen, [datetime.datetime.now(tz).date()])

    def test_invalid_arguments_raise_rather_than_reporting_a_pass(self):
        with self.assertRaises(PreMarketHealthCheckError):
            SystemdSupervisionHelper.run_premarket_healthcheck(
                secrets_dict=None, broker_connectivity_fn=Mock(return_value=True)
            )
        with self.assertRaises(PreMarketHealthCheckError):
            SystemdSupervisionHelper.run_premarket_healthcheck(
                secrets_dict={}, broker_connectivity_fn="not callable"
            )
        with self.assertRaises(PreMarketHealthCheckError):
            SystemdSupervisionHelper.run_premarket_healthcheck(
                secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
                broker_connectivity_fn=Mock(return_value=True),
                as_of_date=datetime.datetime(2026, 7, 15, 9, 15),
            )

    def test_result_is_a_healthcheckresult(self):
        res = SystemdSupervisionHelper.run_premarket_healthcheck(
            secrets_dict={"BROKER_API_KEY": "k", "BROKER_SECRET": "s"},
            broker_connectivity_fn=Mock(return_value=True),
            as_of_date=datetime.date(2026, 7, 15),
        )
        self.assertIsInstance(res, HealthCheckResult)


class TestUnitFileParsing(unittest.TestCase):

    def test_sections_directives_and_repeats_are_preserved(self):
        parsed = parse_unit_file(
            "[Service]\nExecStartPre=/a\nExecStartPre=/b\nType=notify\n"
        )
        self.assertEqual(parsed["Service"]["ExecStartPre"], ["/a", "/b"])
        self.assertEqual(parsed["Service"]["Type"], ["notify"])

    def test_comments_and_continuations_are_handled(self):
        parsed = parse_unit_file(
            "[Service]\n# a comment\n; another\nExecStart=/bin/bot \\\n    --flag\n"
        )
        self.assertEqual(parsed["Service"]["ExecStart"], ["/bin/bot --flag"])
        self.assertNotIn("# a comment", parsed["Service"])

    def test_timespans_parse_in_the_forms_units_actually_use(self):
        self.assertAlmostEqual(parse_systemd_timespan("30"), 30.0)
        self.assertAlmostEqual(parse_systemd_timespan("30s"), 30.0)
        self.assertAlmostEqual(parse_systemd_timespan("10min"), 600.0)
        self.assertAlmostEqual(parse_systemd_timespan("1min 30s"), 90.0)
        self.assertAlmostEqual(parse_systemd_timespan("500ms"), 0.5)
        self.assertEqual(parse_systemd_timespan("infinity"), float("inf"))
        self.assertIsNone(parse_systemd_timespan("30ss"))
        self.assertIsNone(parse_systemd_timespan(""))


class TestUnitFileValidation(unittest.TestCase):

    def test_shipped_unit_file_validates_clean(self):
        with open(UNIT_PATH, "r", encoding="utf-8") as handle:
            content = handle.read()
        report = SystemdSupervisionHelper.validate_unit_file_content(content)
        self.assertTrue(report.is_valid, f"unit file findings: {report.issues}")

    def test_legacy_tuple_unpacking_still_works(self):
        valid, issues = SystemdSupervisionHelper.validate_unit_file_content(
            MINIMAL_GOOD_UNIT
        )
        self.assertTrue(valid)
        self.assertEqual(issues, [])

    # -- REGRESSION ----------------------------------------------------------

    def test_regression_start_limit_interval_in_service_section_is_flagged(self):
        """The defect the shipped unit file used to have, and the old validator passed.

        systemd's directive table has no Service.StartLimitIntervalSec, so this
        is an unknown key: the unit silently keeps DefaultStartLimitIntervalSec
        (10s) and the crash-loop brake never engages.
        """
        broken = MINIMAL_GOOD_UNIT.replace(
            "StartLimitIntervalSec=600\nStartLimitBurst=5",
            "StartLimitBurst=5",
        ).replace(
            "Restart=on-failure", "StartLimitIntervalSec=600\nRestart=on-failure"
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(broken)
        self.assertFalse(report.is_valid)
        self.assertIn(CODE_START_LIMIT_IGNORED_IN_SERVICE_SECTION, report.codes)

    def test_regression_legacy_start_limit_keys_in_service_are_flagged(self):
        unit = MINIMAL_GOOD_UNIT.replace(
            "Restart=on-failure", "StartLimitBurst=5\nRestart=on-failure"
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertIn(CODE_START_LIMIT_LEGACY_SERVICE_SECTION, report.codes)

    def test_regression_watchdog_without_a_notify_type_is_critical(self):
        """NotifyAccess defaults to none, so every ping is discarded."""
        unit = MINIMAL_GOOD_UNIT.replace("Type=notify", "Type=simple")
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertIn(CODE_WATCHDOG_PINGS_IGNORED, report.codes)

    def test_explicit_notify_access_rescues_a_non_notify_type(self):
        unit = MINIMAL_GOOD_UNIT.replace(
            "Type=notify", "Type=exec\nNotifyAccess=main"
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertNotIn(CODE_WATCHDOG_PINGS_IGNORED, report.codes)

    def test_notify_reload_type_is_accepted(self):
        unit = MINIMAL_GOOD_UNIT.replace("Type=notify", "Type=notify-reload")
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertNotIn(CODE_WATCHDOG_PINGS_IGNORED, report.codes)

    def test_unreachable_start_limit_is_flagged(self):
        """RestartSec x (burst - 1) >= window means the limiter can never trip."""
        unit = MINIMAL_GOOD_UNIT.replace(
            "StartLimitIntervalSec=600", "StartLimitIntervalSec=10"
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertIn(CODE_START_LIMIT_UNREACHABLE, report.codes)

    def test_reachable_start_limit_is_not_flagged(self):
        report = SystemdSupervisionHelper.validate_unit_file_content(MINIMAL_GOOD_UNIT)
        self.assertNotIn(CODE_START_LIMIT_UNREACHABLE, report.codes)

    def test_zero_interval_or_burst_disables_the_limiter_and_is_flagged(self):
        """systemd: a limit counts as configured only if interval > 0 AND burst > 0."""
        for original, replacement in (
            ("StartLimitIntervalSec=600", "StartLimitIntervalSec=0"),
            ("StartLimitBurst=5", "StartLimitBurst=0"),
        ):
            with self.subTest(directive=replacement):
                unit = MINIMAL_GOOD_UNIT.replace(original, replacement)
                report = SystemdSupervisionHelper.validate_unit_file_content(unit)
                self.assertIn(CODE_START_LIMIT_UNREACHABLE, report.codes)
                self.assertFalse(report.is_valid)

    # -- policy checks -------------------------------------------------------

    def test_restart_always_is_flagged(self):
        unit = MINIMAL_GOOD_UNIT.replace("Restart=on-failure", "Restart=always")
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertIn(CODE_UNBOUNDED_RESTART_POLICY, report.codes)

    def test_commented_out_restart_always_is_not_flagged(self):
        """The old scanner matched the raw text, so a comment tripped the check."""
        unit = MINIMAL_GOOD_UNIT.replace(
            "Restart=on-failure", "# Restart=always\nRestart=on-failure"
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertNotIn(CODE_UNBOUNDED_RESTART_POLICY, report.codes)
        self.assertTrue(report.is_valid, report.issues)

    def test_missing_directives_are_each_reported(self):
        report = SystemdSupervisionHelper.validate_unit_file_content(
            "[Unit]\nStartLimitIntervalSec=600\nStartLimitBurst=5\n"
            "[Service]\nType=notify\nExecStart=/bin/bot\nExecStartPre=/bin/pre\n"
        )
        self.assertFalse(report.is_valid)
        for code in (
            CODE_MISSING_WATCHDOG,
            CODE_MISSING_RESTART_POLICY,
            CODE_MISSING_MEMORY_LIMIT,
            CODE_MISSING_STOP_TIMEOUT,
        ):
            self.assertIn(code, report.codes)

    def test_memory_high_alone_satisfies_the_memory_limit_check(self):
        unit = MINIMAL_GOOD_UNIT.replace("MemoryMax=1G", "MemoryHigh=1G")
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertNotIn(CODE_MISSING_MEMORY_LIMIT, report.codes)

    def test_unparseable_time_value_is_reported(self):
        unit = MINIMAL_GOOD_UNIT.replace("WatchdogSec=30", "WatchdogSec=30 seconds!")
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertIn(CODE_UNPARSEABLE_DIRECTIVE, report.codes)

    def test_empty_or_sectionless_content_is_not_reported_as_valid(self):
        for content in ("", "Type=notify\nWatchdogSec=30\n"):
            with self.subTest(content=content):
                report = SystemdSupervisionHelper.validate_unit_file_content(content)
                self.assertFalse(report.is_valid)

    def test_findings_are_ordered_most_severe_first(self):
        unit = MINIMAL_GOOD_UNIT.replace("Type=notify", "Type=simple").replace(
            "MemoryMax=1G", ""
        )
        report = SystemdSupervisionHelper.validate_unit_file_content(unit)
        self.assertEqual(report.findings[0].severity, "CRITICAL")


if __name__ == "__main__":
    unittest.main()
