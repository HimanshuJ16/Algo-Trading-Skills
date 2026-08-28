"""Behavioural tests for the real-time vs delayed entitlement gate.

Expected values are derived from the venues' published definitions, not from the
implementation: Nasdaq/ESMA treat data as delayed at 15 minutes, while CME Group
and ICE Futures Europe require *more than* ten minutes, and CME caps delayed
Information below eight hours (479 minutes). Several tests are regressions
against v1.0.0 behaviour and fail against it by construction -- they are marked
inline.
"""

import unittest

from real_time_vs_delayed_data_entitlement_handling import (
    ALL_STATUSES,
    DEFAULT_DELAY_MESSAGE_REFRESH_SECONDS,
    EntitlementAuditReport,
    EntitlementConfigurationError,
    MarketDataRequest,
    RealTimeVsDelayedEntitlementEngine,
    UserEntitlement,
    VenueDelayPolicy,
)

# Nasdaq: delayed data is data made available 15 minutes after publication, and
# every display of it must carry a Prominent Delay Message -- "Data Delayed 15
# minutes" is one of Nasdaq's own example strings.
NASDAQ_POLICY = VenueDelayPolicy(
    venue_id="NASDAQ",
    delay_minutes=15,
    min_delay_minutes=15,
    display_label="Data Delayed 15 minutes",
    policy_source="Nasdaq, Display Requirements Policy (2022)",
)

# CME Group: real-time Information is made available within ten minutes; delayed
# Information is more than ten minutes but less than eight hours old. In whole
# minutes that is [11, 479].
CME_POLICY = VenueDelayPolicy(
    venue_id="CME",
    delay_minutes=11,
    min_delay_minutes=11,
    max_delay_minutes=479,
    display_label="Data Delayed 11 minutes",
    policy_source="CME Group, Data Licensing Policy Guidelines - Non-Display Use",
)


def _engine(*policies: VenueDelayPolicy) -> RealTimeVsDelayedEntitlementEngine:
    return RealTimeVsDelayedEntitlementEngine(policies)


def _pro(tier: str, *exchanges: str) -> UserEntitlement:
    return UserEntitlement("USER_PRO_01", "PROFESSIONAL", list(exchanges), tier)


class TestRealTimeServing(unittest.TestCase):

    def test_realtime_entitlement_serves_zero_delay_and_allows_execution(self):
        report = _engine().evaluate_request(
            _pro("REAL_TIME", "NASDAQ", "NYSE"),
            MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True),
        )
        self.assertEqual(report.status, "REALTIME_STREAM_ENTITLED")
        self.assertTrue(report.is_permitted)
        self.assertFalse(report.is_delayed)
        self.assertEqual(report.delay_minutes, 0)
        self.assertTrue(report.trading_execution_allowed)
        # A real-time stream carries no delay message.
        self.assertIsNone(report.required_display_label)
        self.assertIsNone(report.delay_message_refresh_seconds)

    def test_realtime_serving_needs_no_venue_delay_policy(self):
        report = _engine().evaluate_request(
            _pro("REAL_TIME", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "REALTIME_STREAM_ENTITLED")

    def test_classification_and_tier_are_recorded_on_the_report(self):
        user = UserEntitlement("U1", "non_professional", ["nasdaq"], "real_time")
        report = _engine().evaluate_request(user, MarketDataRequest("AAPL", "nasdaq"))
        self.assertEqual(report.subscriber_type, "NON_PROFESSIONAL")
        self.assertEqual(report.entitlement_tier, "REAL_TIME")
        self.assertEqual(report.exchange, "NASDAQ")


class TestDelayedServing(unittest.TestCase):

    def test_delayed_stream_carries_venue_delay_and_display_label(self):
        report = _engine(NASDAQ_POLICY).evaluate_request(
            _pro("DELAYED", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")
        self.assertTrue(report.is_permitted)
        self.assertTrue(report.is_delayed)
        self.assertEqual(report.delay_minutes, 15)
        self.assertFalse(report.trading_execution_allowed)
        self.assertEqual(report.required_display_label, "Data Delayed 15 minutes")
        self.assertEqual(
            report.delay_message_refresh_seconds, DEFAULT_DELAY_MESSAGE_REFRESH_SECONDS)
        self.assertIn("Nasdaq", report.policy_source)

    def test_delay_interval_comes_from_the_venue_not_a_fixed_fifteen(self):
        # Regression against v1.0.0, which reported 15 minutes for every venue.
        # CME's boundary sits above ten minutes, so its delayed feed is 11 here.
        report = _engine(CME_POLICY).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")
        self.assertEqual(report.delay_minutes, 11)

    def test_unknown_venue_delay_policy_blocks_rather_than_assuming_fifteen(self):
        # Regression against v1.0.0: an unconfigured venue was served at a
        # hard-coded 15-minute delay it had no authority for.
        report = _engine(NASDAQ_POLICY).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_BLOCKED_NO_DELAY_POLICY")
        self.assertFalse(report.is_permitted)
        self.assertIsNone(report.delay_minutes)

    def test_throttle_below_the_venue_boundary_is_refused(self):
        # A ten-minute throttle does not clear CME's "more than ten minutes"
        # boundary: the feed is still real-time Information at real-time rates.
        under_throttled = VenueDelayPolicy(
            venue_id="CME", delay_minutes=10, min_delay_minutes=11,
            max_delay_minutes=479, display_label="Data Delayed 10 minutes")
        report = _engine(under_throttled).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_BLOCKED_INSUFFICIENT_DELAY")
        self.assertFalse(report.is_permitted)
        self.assertIsNone(report.delay_minutes)

    def test_delay_at_the_venue_boundary_is_served(self):
        boundary = VenueDelayPolicy(
            venue_id="CME", delay_minutes=11, min_delay_minutes=11,
            display_label="Data Delayed 11 minutes")
        report = _engine(boundary).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")

    def test_delay_beyond_the_delayed_ceiling_is_refused(self):
        # 480 minutes is eight hours: at CME the feed has become end-of-day or
        # historical Information, licensed separately.
        over = VenueDelayPolicy(
            venue_id="CME", delay_minutes=480, min_delay_minutes=11,
            max_delay_minutes=479, display_label="Data Delayed 8 hours")
        report = _engine(over).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_BLOCKED_DELAY_EXCEEDS_POLICY")
        self.assertFalse(report.is_permitted)

    def test_delay_at_the_ceiling_is_served(self):
        at_ceiling = VenueDelayPolicy(
            venue_id="CME", delay_minutes=479, min_delay_minutes=11,
            max_delay_minutes=479, display_label="Data Delayed 479 minutes")
        report = _engine(at_ceiling).evaluate_request(
            _pro("DELAYED", "CME"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")

    def test_untracked_ceiling_is_not_gated(self):
        no_ceiling = VenueDelayPolicy(
            venue_id="XETR", delay_minutes=900, min_delay_minutes=15,
            display_label="Data Delayed 15 hours")
        report = _engine(no_ceiling).evaluate_request(
            _pro("DELAYED", "XETR"), MarketDataRequest("SAP", "XETR"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")
        self.assertEqual(report.delay_minutes, 900)


class TestExecutionBlock(unittest.TestCase):

    def test_delayed_entitlement_blocks_live_trading(self):
        report = _engine(NASDAQ_POLICY).evaluate_request(
            UserEntitlement("USER_RETAIL_01", "NON_PROFESSIONAL", ["NASDAQ"], "DELAYED"),
            MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True),
        )
        self.assertEqual(report.status, "LIVE_TRADING_BLOCKED_DELAYED_DATA")
        self.assertFalse(report.is_permitted)
        self.assertFalse(report.trading_execution_allowed)
        # No stream was authorised, so the report asserts no delay for it.
        self.assertFalse(report.is_delayed)
        self.assertIsNone(report.delay_minutes)

    def test_execution_block_precedes_the_venue_policy_checks(self):
        # Ordering contract: an execution request on a delayed tier is refused
        # for the execution reason even when the venue policy is also missing,
        # so the auditor sees the unsafe intent rather than a config gap.
        report = _engine().evaluate_request(
            _pro("DELAYED", "CME"),
            MarketDataRequest("ESZ6", "CME", is_trading_execution_request=True),
        )
        self.assertEqual(report.status, "LIVE_TRADING_BLOCKED_DELAYED_DATA")

    def test_unsubscribed_venue_is_refused_before_the_execution_check(self):
        report = _engine().evaluate_request(
            _pro("DELAYED", "NASDAQ"),
            MarketDataRequest("ESZ6", "CME", is_trading_execution_request=True),
        )
        self.assertEqual(report.status, "EXCHANGE_NOT_SUBSCRIBED")


class TestUnrecognisedTier(unittest.TestCase):

    def test_misspelled_tier_is_denied_not_served(self):
        # Regression against v1.0.0: any tier that merely failed to equal
        # 'DELAYED' skipped the execution block and returned is_permitted=True.
        report = _engine(NASDAQ_POLICY).evaluate_request(
            _pro("REALTIME", "NASDAQ"),
            MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True),
        )
        self.assertEqual(report.status, "ENTITLEMENT_DENIED_UNRECOGNISED_TIER")
        self.assertFalse(report.is_permitted)
        self.assertFalse(report.trading_execution_allowed)
        self.assertIsNone(report.delay_minutes)
        self.assertEqual(report.entitlement_tier, "")

    def test_vendor_specific_tier_label_is_denied(self):
        report = _engine(NASDAQ_POLICY).evaluate_request(
            _pro("DELAYED_15", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ"))
        self.assertEqual(report.status, "ENTITLEMENT_DENIED_UNRECOGNISED_TIER")

    def test_tier_check_precedes_the_subscription_check(self):
        report = _engine().evaluate_request(
            _pro("REALTIME", "NASDAQ"), MarketDataRequest("ESZ6", "CME"))
        self.assertEqual(report.status, "ENTITLEMENT_DENIED_UNRECOGNISED_TIER")


class TestSubscriptionAndNormalisation(unittest.TestCase):

    def test_unsubscribed_exchange_access_denied_without_asserting_a_delay(self):
        # Regression against v1.0.0, which reported is_delayed=True and
        # delay_minutes=15 on a request that served nothing at all.
        report = _engine().evaluate_request(
            UserEntitlement("USER_02", "NON_PROFESSIONAL", ["NASDAQ"], "REAL_TIME"),
            MarketDataRequest("ES_FUT", "CME"),
        )
        self.assertEqual(report.status, "EXCHANGE_NOT_SUBSCRIBED")
        self.assertFalse(report.is_permitted)
        self.assertFalse(report.is_delayed)
        self.assertIsNone(report.delay_minutes)

    def test_exchange_matching_is_case_and_whitespace_insensitive_on_both_sides(self):
        user = UserEntitlement("U1", "PROFESSIONAL", [" nasdaq "], "REAL_TIME")
        report = _engine().evaluate_request(user, MarketDataRequest("AAPL", "NaSdAq "))
        self.assertEqual(report.status, "REALTIME_STREAM_ENTITLED")

    def test_empty_subscription_list_denies(self):
        report = _engine().evaluate_request(
            UserEntitlement("U1", "PROFESSIONAL", [], "REAL_TIME"),
            MarketDataRequest("AAPL", "NASDAQ"),
        )
        self.assertEqual(report.status, "EXCHANGE_NOT_SUBSCRIBED")

    def test_venue_policy_lookup_is_case_insensitive(self):
        policy = VenueDelayPolicy(
            venue_id="nasdaq", delay_minutes=15, display_label="Del-15")
        report = _engine(policy).evaluate_request(
            _pro("DELAYED", "NASDAQ"), MarketDataRequest("AAPL", "nasdaq"))
        self.assertEqual(report.status, "DELAYED_STREAM_ENTITLED")


class TestInputValidation(unittest.TestCase):

    def test_bare_string_subscription_list_is_rejected(self):
        # Iterating "NASDAQ" character by character would deny every request for
        # a reason no one could read off the report.
        user = UserEntitlement("U1", "PROFESSIONAL", "NASDAQ", "REAL_TIME")
        with self.assertRaises(EntitlementConfigurationError):
            _engine().evaluate_request(user, MarketDataRequest("AAPL", "NASDAQ"))

    def test_set_and_tuple_subscription_lists_are_accepted(self):
        for container in ({"NASDAQ"}, ("NASDAQ",)):
            with self.subTest(container=type(container).__name__):
                user = UserEntitlement("U1", "PROFESSIONAL", container, "REAL_TIME")
                report = _engine().evaluate_request(user, MarketDataRequest("AAPL", "NASDAQ"))
                self.assertEqual(report.status, "REALTIME_STREAM_ENTITLED")

    def test_unknown_subscriber_type_is_rejected(self):
        user = UserEntitlement("U1", "RETAIL", ["NASDAQ"], "REAL_TIME")
        with self.assertRaises(EntitlementConfigurationError):
            _engine().evaluate_request(user, MarketDataRequest("AAPL", "NASDAQ"))

    def test_blank_identifiers_are_rejected(self):
        for user_id, symbol, exchange in (
            ("", "AAPL", "NASDAQ"),
            ("U1", "   ", "NASDAQ"),
            ("U1", "AAPL", ""),
        ):
            with self.subTest(user_id=user_id, symbol=symbol, exchange=exchange):
                user = UserEntitlement(user_id, "PROFESSIONAL", ["NASDAQ"], "REAL_TIME")
                with self.assertRaises(EntitlementConfigurationError):
                    _engine().evaluate_request(user, MarketDataRequest(symbol, exchange))

    def test_non_bool_execution_flag_is_rejected(self):
        request = MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request="yes")
        with self.assertRaises(EntitlementConfigurationError):
            _engine().evaluate_request(_pro("DELAYED", "NASDAQ"), request)

    def test_configuration_error_is_a_value_error(self):
        self.assertTrue(issubclass(EntitlementConfigurationError, ValueError))


class TestVenuePolicyValidation(unittest.TestCase):

    def test_display_label_is_mandatory(self):
        with self.assertRaises(EntitlementConfigurationError):
            VenueDelayPolicy(venue_id="NASDAQ", delay_minutes=15, display_label="  ")

    def test_bool_delay_is_rejected(self):
        # bool subclasses int; True would silently become a one-minute delay.
        with self.assertRaises(EntitlementConfigurationError):
            VenueDelayPolicy(venue_id="NASDAQ", delay_minutes=True, display_label="Del-15")

    def test_zero_delay_is_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            VenueDelayPolicy(venue_id="NASDAQ", delay_minutes=0, display_label="Del-0")

    def test_ceiling_below_floor_is_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            VenueDelayPolicy(
                venue_id="CME", delay_minutes=11, min_delay_minutes=11,
                max_delay_minutes=5, display_label="Del-11")

    def test_duplicate_venue_policies_are_rejected(self):
        other = VenueDelayPolicy(
            venue_id="nasdaq", delay_minutes=20, display_label="Data Delayed 20 minutes")
        with self.assertRaises(EntitlementConfigurationError):
            _engine(NASDAQ_POLICY, other)

    def test_non_policy_entries_are_rejected(self):
        with self.assertRaises(EntitlementConfigurationError):
            RealTimeVsDelayedEntitlementEngine([{"venue_id": "NASDAQ"}])

    def test_policy_table_is_read_only(self):
        engine = _engine(NASDAQ_POLICY)
        with self.assertRaises(TypeError):
            engine.venue_delay_policies["NYSE"] = NASDAQ_POLICY


class TestReportContract(unittest.TestCase):

    def _cases(self):
        engine = _engine(NASDAQ_POLICY, CME_POLICY)
        return [
            (engine, _pro("REAL_TIME", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ")),
            (engine, _pro("DELAYED", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ")),
            (engine, _pro("DELAYED", "NASDAQ"),
             MarketDataRequest("AAPL", "NASDAQ", is_trading_execution_request=True)),
            (engine, _pro("DELAYED", "NYSE"), MarketDataRequest("IBM", "NYSE")),
            (engine, _pro("BOGUS", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ")),
            (engine, _pro("REAL_TIME", "NASDAQ"), MarketDataRequest("ESZ6", "CME")),
        ]

    def test_every_status_is_declared_and_permission_matches_status(self):
        permitted = {"REALTIME_STREAM_ENTITLED", "DELAYED_STREAM_ENTITLED"}
        for engine, user, request in self._cases():
            with self.subTest(tier=user.entitlement_tier, exchange=request.exchange):
                report = engine.evaluate_request(user, request)
                self.assertIn(report.status, ALL_STATUSES)
                self.assertEqual(report.is_permitted, report.status in permitted)
                # Execution is allowed on the real-time path only.
                self.assertEqual(
                    report.trading_execution_allowed,
                    report.status == "REALTIME_STREAM_ENTITLED")
                # A denial never asserts a delay it did not serve.
                if not report.is_permitted:
                    self.assertIsNone(report.delay_minutes)
                    self.assertFalse(report.is_delayed)
                self.assertTrue(report.audit_notes.strip())

    def test_statuses_are_unique(self):
        self.assertEqual(len(set(ALL_STATUSES)), len(ALL_STATUSES))

    def test_evaluation_is_deterministic(self):
        engine = _engine(NASDAQ_POLICY)
        user = _pro("DELAYED", "NASDAQ")
        request = MarketDataRequest("AAPL", "NASDAQ")
        self.assertEqual(
            engine.evaluate_request(user, request), engine.evaluate_request(user, request))

    def test_report_is_a_dataclass_instance(self):
        report = _engine().evaluate_request(
            _pro("REAL_TIME", "NASDAQ"), MarketDataRequest("AAPL", "NASDAQ"))
        self.assertIsInstance(report, EntitlementAuditReport)


if __name__ == '__main__':
    unittest.main()
