"""Unit tests for the deployment freeze guard.

Expected epochs are derived independently of the module under test: every
reference instant is built as an explicit UTC ``datetime`` with the offset
worked out by hand (New York is UTC-4 in June, UTC-5 in January), so a test
failure means the engine's timezone handling is wrong rather than that two
copies of the same call agree with each other.
"""

import logging
import unittest
from datetime import datetime, timezone

from deployment_freeze_guard import (
    STATUS_APPROVED,
    STATUS_BLOCKED_FREEZE,
    STATUS_BLOCKED_STALE_CALENDAR,
    STATUS_BREAK_GLASS_APPROVED,
    STATUS_INVALID_DUAL_AUTH,
    STATUS_MISSING_DUAL_AUTH,
    STATUS_UNKNOWN_ENVIRONMENT,
    DailyMarketFreezeWindow,
    DeploymentFreezeError,
    DeploymentFreezeGuardEngine,
    DeploymentRequest,
    MacroEventFreezeWindow,
)

logging.disable(logging.CRITICAL)


def utc_epoch(year, month, day, hour, minute=0) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


# FOMC statement for the 16-17 June 2026 meeting: 2:00 p.m. ET, which is EDT
# (UTC-4) in June -> 18:00 UTC.
FOMC_JUNE_2026 = utc_epoch(2026, 6, 17, 18, 0)

MINUTE = 60.0


def make_request(**overrides) -> DeploymentRequest:
    kwargs = dict(
        deployment_id="DEP_101",
        service_name="execution-router",
        target_environment="PRODUCTION",
        requested_epoch_sec=FOMC_JUNE_2026 - 30 * MINUTE,
    )
    kwargs.update(overrides)
    return DeploymentRequest(**kwargs)


def signed_off(**overrides) -> DeploymentRequest:
    """A fully authorised break-glass request."""
    kwargs = dict(
        is_emergency_hotfix=True,
        risk_officer_approval=True,
        head_of_trading_approval=True,
        risk_officer_id="r.okafor",
        head_of_trading_id="j.tanaka",
        justification="INC-4471 execution-router null deref on cancel",
    )
    kwargs.update(overrides)
    return make_request(**kwargs)


def fomc_engine(**engine_kwargs) -> DeploymentFreezeGuardEngine:
    engine = DeploymentFreezeGuardEngine(**engine_kwargs)
    engine.register_freeze_event(
        MacroEventFreezeWindow(
            event_id="FOMC_2026_JUN",
            event_name="FOMC Rate Decision Announcement",
            event_start_epoch_sec=FOMC_JUNE_2026,
            pre_event_buffer_minutes=60.0,
            post_event_buffer_minutes=60.0,
        )
    )
    return engine


class TestMacroEventFreeze(unittest.TestCase):
    def setUp(self):
        self.engine = fomc_engine()

    def test_routine_production_deployment_blocked_during_freeze(self):
        report = self.engine.evaluate_deployment_request(make_request())
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)
        self.assertEqual(report.active_freeze_event_name, "FOMC Rate Decision Announcement")

    def test_report_states_when_the_freeze_lifts(self):
        # An operator's first question on a blocked deploy is "until when".
        report = self.engine.evaluate_deployment_request(make_request())
        self.assertEqual(report.freeze_ends_epoch_sec, FOMC_JUNE_2026 + 60 * MINUTE)

    def test_deployment_outside_the_window_is_approved(self):
        report = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=FOMC_JUNE_2026 - 61 * MINUTE)
        )
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertIsNone(report.freeze_ends_epoch_sec)

    def test_window_boundaries_are_inclusive(self):
        for edge in (FOMC_JUNE_2026 - 60 * MINUTE, FOMC_JUNE_2026 + 60 * MINUTE):
            report = self.engine.evaluate_deployment_request(
                make_request(requested_epoch_sec=edge)
            )
            self.assertFalse(report.is_approved, f"boundary {edge} must be inside the freeze")

    def test_overlapping_windows_report_the_latest_lift_time(self):
        # Regression: taking the first registered match reported a freeze that
        # lifts at 15:00 while a second, later window was still active.
        self.engine.register_freeze_event(
            MacroEventFreezeWindow(
                event_id="FOMC_PRESSER",
                event_name="FOMC Press Conference",
                # Press conference starts 30 minutes after the statement.
                event_start_epoch_sec=FOMC_JUNE_2026 + 30 * MINUTE,
                pre_event_buffer_minutes=0.0,
                post_event_buffer_minutes=60.0,
            )
        )
        report = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=FOMC_JUNE_2026 + 30 * MINUTE)
        )
        self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)
        self.assertEqual(report.active_freeze_event_name, "FOMC Press Conference")
        self.assertEqual(report.freeze_ends_epoch_sec, FOMC_JUNE_2026 + 90 * MINUTE)
        self.assertEqual(len(report.active_freeze_labels), 2)

    def test_selection_is_independent_of_registration_order(self):
        def build(order):
            engine = DeploymentFreezeGuardEngine()
            for event in order:
                engine.register_freeze_event(event)
            report = engine.evaluate_deployment_request(
                make_request(requested_epoch_sec=FOMC_JUNE_2026)
            )
            return report.active_freeze_event_name, report.freeze_ends_epoch_sec

        early = MacroEventFreezeWindow("E1", "Early", FOMC_JUNE_2026, 60.0, 10.0)
        late = MacroEventFreezeWindow("E2", "Late", FOMC_JUNE_2026, 60.0, 90.0)
        self.assertEqual(build([early, late]), build([late, early]))
        self.assertEqual(build([early, late])[0], "Late")


class TestBreakGlassDualSignOff(unittest.TestCase):
    def setUp(self):
        self.engine = fomc_engine()

    def test_break_glass_approved_with_two_named_approvers(self):
        report = self.engine.evaluate_deployment_request(signed_off())
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, STATUS_BREAK_GLASS_APPROVED)
        # RTS 6 Art. 11 requires knowing who approved a material change.
        self.assertEqual(report.approvers, ["r.okafor", "j.tanaka"])

    def test_self_approval_by_one_person_is_rejected(self):
        # Regression: two booleans can both be set by the same individual, which
        # is precisely what dual sign-off exists to prevent.
        report = self.engine.evaluate_deployment_request(
            signed_off(risk_officer_id="a.singh", head_of_trading_id="A.Singh")
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_INVALID_DUAL_AUTH)

    def test_single_approval_is_rejected(self):
        report = self.engine.evaluate_deployment_request(
            signed_off(head_of_trading_approval=False)
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_MISSING_DUAL_AUTH)

    def test_anonymous_approvals_are_rejected(self):
        report = self.engine.evaluate_deployment_request(
            signed_off(risk_officer_id=None, head_of_trading_id=None)
        )
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_MISSING_DUAL_AUTH)
        self.assertIn("risk_officer_id", report.applied_policy)

    def test_missing_justification_is_rejected_by_default(self):
        report = self.engine.evaluate_deployment_request(signed_off(justification="   "))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_MISSING_DUAL_AUTH)

    def test_justification_can_be_made_optional(self):
        engine = fomc_engine(require_justification=False)
        report = engine.evaluate_deployment_request(signed_off(justification=None))
        self.assertTrue(report.is_approved)

    def test_emergency_flag_alone_does_not_bypass_the_freeze(self):
        report = self.engine.evaluate_deployment_request(make_request(is_emergency_hotfix=True))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_MISSING_DUAL_AUTH)

    def test_break_glass_outside_a_freeze_is_just_an_approval(self):
        report = self.engine.evaluate_deployment_request(
            signed_off(requested_epoch_sec=FOMC_JUNE_2026 - 5 * 3600)
        )
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, STATUS_APPROVED)


class TestEnvironmentHandling(unittest.TestCase):
    def setUp(self):
        self.engine = fomc_engine()

    def test_known_non_production_environments_are_exempt(self):
        for environment in ("STAGING", "research", "Development", "SANDBOX"):
            report = self.engine.evaluate_deployment_request(
                make_request(target_environment=environment)
            )
            self.assertTrue(report.is_approved, environment)
            self.assertEqual(report.status, STATUS_APPROVED)

    def test_unknown_environment_is_denied_not_exempted(self):
        # Regression: `env != "PRODUCTION"` exempted every typo, so a CI
        # variable of "PRODCUTION" deployed straight through an FOMC freeze.
        for environment in ("PRODCUTION", "prod", "PRODUCTION_EU", "production "):
            report = self.engine.evaluate_deployment_request(
                make_request(target_environment=environment)
            )
            if environment.strip().upper() == "PRODUCTION":
                self.assertFalse(report.is_approved, environment)
                self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)
            else:
                self.assertFalse(report.is_approved, environment)
                self.assertEqual(report.status, STATUS_UNKNOWN_ENVIRONMENT, environment)

    def test_production_environment_set_is_configurable(self):
        engine = fomc_engine(
            production_environments=("PRODUCTION", "PROD_EU"),
            exempt_environments=("STAGING",),
        )
        report = engine.evaluate_deployment_request(make_request(target_environment="prod_eu"))
        self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)

    def test_environment_cannot_be_both_production_and_exempt(self):
        with self.assertRaises(DeploymentFreezeError):
            DeploymentFreezeGuardEngine(
                production_environments=("PRODUCTION",), exempt_environments=("production",)
            )


class TestDailySessionWindows(unittest.TestCase):
    """The NYSE core session is 09:30-16:00 ET; ET is UTC-4 in June, UTC-5 in January."""

    def setUp(self):
        self.engine = DeploymentFreezeGuardEngine()
        self.engine.register_daily_window(
            DailyMarketFreezeWindow(
                window_id="NYSE_OPEN",
                label="NYSE Open",
                timezone="America/New_York",
                local_time_hhmm="09:30",
                pre_buffer_minutes=15.0,
                post_buffer_minutes=15.0,
            )
        )

    def test_open_window_follows_daylight_saving(self):
        # Regression against a UTC-hardcoded window: 09:30 New York is 13:30 UTC
        # in June but 14:30 UTC in January. One fixed UTC rule cannot cover both.
        summer_open = utc_epoch(2026, 6, 17, 13, 30)
        winter_open = utc_epoch(2026, 1, 28, 14, 30)

        for instant in (summer_open, winter_open):
            report = self.engine.evaluate_deployment_request(
                make_request(requested_epoch_sec=instant)
            )
            self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)
            self.assertEqual(report.active_freeze_event_name, "NYSE Open")

        # And the same wall-clock UTC time is *not* frozen in the other season.
        report = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=utc_epoch(2026, 1, 28, 13, 30))
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_window_edges_and_outside(self):
        open_utc = utc_epoch(2026, 6, 17, 13, 30)
        inside = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=open_utc + 15 * MINUTE)
        )
        outside = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=open_utc + 16 * MINUTE)
        )
        self.assertEqual(inside.status, STATUS_BLOCKED_FREEZE)
        self.assertEqual(outside.status, STATUS_APPROVED)

    def test_weekend_has_no_session_window(self):
        # 2026-06-20 is a Saturday.
        report = self.engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=utc_epoch(2026, 6, 20, 13, 30))
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_early_close_override_moves_the_window(self):
        # NYSE closes at 1:00 p.m. ET on Christmas Eve 2026 (a Thursday), so a
        # fixed 16:00 close rule would guard an hour when nothing is trading.
        engine = DeploymentFreezeGuardEngine()
        engine.register_daily_window(
            DailyMarketFreezeWindow(
                window_id="NYSE_CLOSE",
                label="NYSE Close",
                timezone="America/New_York",
                local_time_hhmm="16:00",
                pre_buffer_minutes=15.0,
                post_buffer_minutes=15.0,
                session_overrides={"2026-12-24": "13:00"},
            )
        )
        early_close = utc_epoch(2026, 12, 24, 18, 0)  # 13:00 EST
        normal_close = utc_epoch(2026, 12, 24, 21, 0)  # 16:00 EST

        self.assertEqual(
            engine.evaluate_deployment_request(
                make_request(requested_epoch_sec=early_close)
            ).status,
            STATUS_BLOCKED_FREEZE,
        )
        self.assertEqual(
            engine.evaluate_deployment_request(
                make_request(requested_epoch_sec=normal_close)
            ).status,
            STATUS_APPROVED,
        )

    def test_holiday_override_disables_the_window(self):
        engine = DeploymentFreezeGuardEngine()
        engine.register_daily_window(
            DailyMarketFreezeWindow(
                window_id="NYSE_OPEN",
                label="NYSE Open",
                timezone="America/New_York",
                local_time_hhmm="09:30",
                session_overrides={"2026-06-17": None},
            )
        )
        report = engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=utc_epoch(2026, 6, 17, 13, 30))
        )
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_nonexistent_local_time_in_dst_gap_is_skipped(self):
        # 02:30 does not exist on 2026-03-08 in New York (clocks jump 02:00->03:00).
        engine = DeploymentFreezeGuardEngine()
        engine.register_daily_window(
            DailyMarketFreezeWindow(
                window_id="GAP",
                label="Gap Window",
                timezone="America/New_York",
                local_time_hhmm="02:30",
                weekdays=(0, 1, 2, 3, 4, 5, 6),
            )
        )
        # 07:30 UTC on that date would be 02:30 EST had the hour existed.
        report = engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=utc_epoch(2026, 3, 8, 7, 30))
        )
        self.assertEqual(report.status, STATUS_APPROVED)
        # The equivalent window on an ordinary day still fires.
        report = engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=utc_epoch(2026, 3, 9, 6, 30))
        )
        self.assertEqual(report.status, STATUS_BLOCKED_FREEZE)

    def test_unknown_timezone_is_rejected(self):
        with self.assertRaises(DeploymentFreezeError):
            DailyMarketFreezeWindow(
                window_id="BAD", label="Bad", timezone="America/Nowhere",
                local_time_hhmm="09:30",
            )

    def test_malformed_local_time_is_rejected(self):
        for bad in ("9:30 AM", "25:00", "0930", "09:60"):
            with self.assertRaises(DeploymentFreezeError):
                DailyMarketFreezeWindow(
                    window_id="BAD", label="Bad", timezone="America/New_York",
                    local_time_hhmm=bad,
                )


class TestCalendarStaleness(unittest.TestCase):
    def test_stale_calendar_blocks_production(self):
        # BLS moved the September 2025 Employment Situation from 3 Oct to 20 Nov
        # 2025; a calendar older than the refresh SLA cannot be trusted to know
        # where the freeze windows are.
        engine = fomc_engine(max_calendar_staleness_sec=24 * 3600.0)
        now = FOMC_JUNE_2026 - 10 * 24 * 3600
        engine.set_calendar_as_of(now - 48 * 3600)

        report = engine.evaluate_deployment_request(make_request(requested_epoch_sec=now))
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, STATUS_BLOCKED_STALE_CALENDAR)

    def test_never_refreshed_calendar_blocks_production(self):
        engine = fomc_engine(max_calendar_staleness_sec=24 * 3600.0)
        report = engine.evaluate_deployment_request(
            make_request(requested_epoch_sec=FOMC_JUNE_2026 - 10 * 24 * 3600)
        )
        self.assertEqual(report.status, STATUS_BLOCKED_STALE_CALENDAR)

    def test_fresh_calendar_allows_deployment(self):
        engine = fomc_engine(max_calendar_staleness_sec=24 * 3600.0)
        now = FOMC_JUNE_2026 - 10 * 24 * 3600
        engine.set_calendar_as_of(now - 3600)
        report = engine.evaluate_deployment_request(make_request(requested_epoch_sec=now))
        self.assertTrue(report.is_approved)

    def test_staleness_check_does_not_apply_to_exempt_environments(self):
        engine = fomc_engine(max_calendar_staleness_sec=24 * 3600.0)
        report = engine.evaluate_deployment_request(
            make_request(target_environment="STAGING")
        )
        self.assertTrue(report.is_approved)

    def test_break_glass_can_lift_a_stale_calendar_block(self):
        engine = fomc_engine(max_calendar_staleness_sec=24 * 3600.0)
        report = engine.evaluate_deployment_request(
            signed_off(requested_epoch_sec=FOMC_JUNE_2026 - 10 * 24 * 3600)
        )
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, STATUS_BREAK_GLASS_APPROVED)


class TestInputValidation(unittest.TestCase):
    def test_nan_request_timestamp_is_rejected(self):
        # Regression: NaN compares False against every bound, so the request
        # sailed through the freeze check and was APPROVED.
        with self.assertRaises(DeploymentFreezeError):
            make_request(requested_epoch_sec=float("nan"))

    def test_infinite_event_timestamp_is_rejected(self):
        with self.assertRaises(DeploymentFreezeError):
            MacroEventFreezeWindow("E", "Event", float("inf"))

    def test_negative_buffer_is_rejected(self):
        # A negative buffer inverts the interval, silently disabling the freeze.
        with self.assertRaises(DeploymentFreezeError):
            MacroEventFreezeWindow("E", "Event", FOMC_JUNE_2026, pre_event_buffer_minutes=-60.0)

    def test_absurd_buffer_is_rejected(self):
        with self.assertRaises(DeploymentFreezeError):
            MacroEventFreezeWindow(
                "E", "Event", FOMC_JUNE_2026, post_event_buffer_minutes=60.0 * 24 * 30
            )

    def test_empty_identifiers_are_rejected(self):
        with self.assertRaises(DeploymentFreezeError):
            make_request(deployment_id="  ")
        with self.assertRaises(DeploymentFreezeError):
            make_request(target_environment="")

    def test_duplicate_event_id_is_rejected(self):
        engine = fomc_engine()
        with self.assertRaises(DeploymentFreezeError):
            engine.register_freeze_event(
                MacroEventFreezeWindow(
                    "FOMC_2026_JUN", "FOMC Rate Decision Announcement (moved)",
                    FOMC_JUNE_2026 + 7 * 24 * 3600,
                )
            )

    def test_register_rejects_wrong_types(self):
        engine = DeploymentFreezeGuardEngine()
        with self.assertRaises(DeploymentFreezeError):
            engine.register_freeze_event({"event_id": "E"})
        with self.assertRaises(DeploymentFreezeError):
            engine.register_daily_window({"window_id": "W"})
        with self.assertRaises(DeploymentFreezeError):
            engine.evaluate_deployment_request({"deployment_id": "D"})

    def test_invalid_session_override_is_rejected(self):
        with self.assertRaises(DeploymentFreezeError):
            DailyMarketFreezeWindow(
                window_id="W", label="W", timezone="America/New_York",
                local_time_hhmm="16:00", session_overrides={"24/12/2026": "13:00"},
            )
        with self.assertRaises(DeploymentFreezeError):
            DailyMarketFreezeWindow(
                window_id="W", label="W", timezone="America/New_York",
                local_time_hhmm="16:00", session_overrides={"2026-12-24": "1pm"},
            )

    def test_invalid_weekdays_are_rejected(self):
        for bad in ((), (7,), ("Monday",)):
            with self.assertRaises(DeploymentFreezeError):
                DailyMarketFreezeWindow(
                    window_id="W", label="W", timezone="America/New_York",
                    local_time_hhmm="16:00", weekdays=bad,
                )


if __name__ == "__main__":
    unittest.main()
