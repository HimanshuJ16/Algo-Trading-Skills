"""Unit tests for exchange-gateway-redundancy-and-failover-testing."""
import logging
import unittest

from exchange_gateway_redundancy_and_failover_testing import (
    EUREX_T7_ETI_PROFILE,
    ExchangeGatewayRedundancyEngine,
    FailoverOutcome,
    GatewayNodeConfig,
    GatewayStatus,
    GENERIC_FIX_PROFILE,
    InFlightOrder,
    OrderRecoveryAction,
    OrderStateRecoveryMethod,
    ReconciliationVerdict,
    SequenceResetPolicy,
    VenueRecoveryProfile,
    heartbeat_timeout_from_interval,
)

# The engine logs at WARNING/ERROR on every failover path; silence it so the test
# output shows test results rather than the module's own alerting.
logging.getLogger("exchange_gateway_redundancy_and_failover_testing").addHandler(logging.NullHandler())
logging.getLogger("exchange_gateway_redundancy_and_failover_testing").propagate = False

HEARTBEAT_TIMEOUT_MS = 60_000.0  # 2 x a 30s HeartBtInt


def primary_node(**overrides) -> GatewayNodeConfig:
    kwargs = dict(
        gateway_id="GW_PRIMARY_FIX", ip_address="192.0.2.10", port=9800,
        role="PRIMARY", status="ACTIVE", last_sent_seq_num=150,
        heartbeat_delay_ms=50.0, latency_rtt_ms=2.5, tcp_connected=True,
    )
    kwargs.update(overrides)
    return GatewayNodeConfig(**kwargs)


def secondary_node(**overrides) -> GatewayNodeConfig:
    kwargs = dict(
        gateway_id="GW_SECONDARY_FIX", ip_address="192.0.2.11", port=9800,
        role="SECONDARY", status="STANDBY", last_sent_seq_num=145,
        heartbeat_delay_ms=0.0, latency_rtt_ms=2.8, tcp_connected=True,
    )
    kwargs.update(overrides)
    return GatewayNodeConfig(**kwargs)


def build_engine(primary=None, secondary=None, **overrides) -> ExchangeGatewayRedundancyEngine:
    kwargs = dict(
        primary_config=primary or primary_node(),
        secondary_config=secondary or secondary_node(),
        max_heartbeat_delay_ms=HEARTBEAT_TIMEOUT_MS,
    )
    kwargs.update(overrides)
    return ExchangeGatewayRedundancyEngine(**kwargs)


class TestHeartbeatTimeoutDerivation(unittest.TestCase):
    def test_derives_timeout_from_interval(self):
        # CME iLink 3 declares a fault-tolerant session failed at 2 x KeepAliveInterval.
        self.assertEqual(heartbeat_timeout_from_interval(30_000.0), 60_000.0)
        self.assertEqual(heartbeat_timeout_from_interval(1_000.0, multiplier=1.5), 1_500.0)

    def test_rejects_nonsense_intervals(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(interval=bad):
                with self.assertRaises(ValueError):
                    heartbeat_timeout_from_interval(bad)

    def test_rejects_multiplier_at_or_below_one(self):
        # A multiplier of 1.0 fires on the very first late heartbeat.
        with self.assertRaises(ValueError):
            heartbeat_timeout_from_interval(30_000.0, multiplier=1.0)


class TestConfigValidation(unittest.TestCase):
    def test_rejects_unknown_status_and_role(self):
        with self.assertRaises(ValueError):
            primary_node(status="ONLINE")
        with self.assertRaises(ValueError):
            primary_node(role="MASTER")

    def test_rejects_negative_sequence_number_and_bad_port(self):
        with self.assertRaises(ValueError):
            primary_node(last_sent_seq_num=-1)
        with self.assertRaises(ValueError):
            primary_node(port=0)
        with self.assertRaises(ValueError):
            primary_node(port=70_000)

    def test_rejects_non_finite_health_metrics(self):
        # A NaN RTT silently fails every ">" comparison, so a dead session would
        # read as healthy forever.
        with self.assertRaises(ValueError):
            primary_node(latency_rtt_ms=float("nan"))
        with self.assertRaises(ValueError):
            primary_node(heartbeat_delay_ms=float("inf"))
        with self.assertRaises(ValueError):
            primary_node(latency_rtt_ms=-5.0)

    def test_rejects_duplicate_gateway_ids(self):
        with self.assertRaises(ValueError):
            build_engine(secondary=secondary_node(gateway_id="GW_PRIMARY_FIX", status="STANDBY"))

    def test_requires_exactly_one_active_gateway(self):
        with self.assertRaises(ValueError):
            build_engine(secondary=secondary_node(status="ACTIVE"))
        with self.assertRaises(ValueError):
            build_engine(primary=primary_node(status="STANDBY"))

    def test_rejects_unusable_thresholds(self):
        with self.assertRaises(ValueError):
            build_engine(max_heartbeat_delay_ms=0.0)
        with self.assertRaises(ValueError):
            build_engine(max_latency_rtt_ms=-1.0)
        with self.assertRaises(ValueError):
            build_engine(min_consecutive_latency_breaches=0)

    def test_rejects_unknown_order_status(self):
        with self.assertRaises(ValueError):
            InFlightOrder("ORD_1", "AAPL", "BUY", 100, 185.0, "IN_FLIGHT")

    def test_rejects_nonpositive_quantity_and_bad_price(self):
        with self.assertRaises(ValueError):
            InFlightOrder("ORD_1", "AAPL", "BUY", 0, 185.0, "PENDING_NEW")
        with self.assertRaises(ValueError):
            InFlightOrder("ORD_1", "AAPL", "BUY", 100, float("nan"), "PENDING_NEW")

    def test_rejects_venue_profile_without_id(self):
        with self.assertRaises(ValueError):
            VenueRecoveryProfile(
                venue_id="  ",
                sequence_policy=SequenceResetPolicy.CONTINUE_SESSION,
                order_state_recovery=OrderStateRecoveryMethod.ORDER_STATUS_REQUEST,
                non_persistent_orders_survive_disconnect=True,
                resend_marking="PossResend(97)=Y",
                source_note="",
            )


class TestHealthAudit(unittest.TestCase):
    def test_healthy_primary_no_failover(self):
        engine = build_engine()
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.outcome, FailoverOutcome.NO_ACTION)
        self.assertFalse(report.failover_executed)
        self.assertIsNone(report.promoted_gateway_id)
        self.assertEqual(engine.active_gateway_id, "GW_PRIMARY_FIX")

    def test_missed_heartbeat_alone_probes_instead_of_failing_over(self):
        # FIX liveness: a late heartbeat means "send TestRequest(35=1)", not
        # "abandon the session". Failing over here drops a session that is merely idle.
        primary = primary_node(heartbeat_delay_ms=90_000.0, test_request_unanswered=False)
        engine = build_engine(primary=primary)
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.outcome, FailoverOutcome.TEST_REQUEST_REQUIRED)
        self.assertIn("ISSUE_TEST_REQUEST", report.required_operator_action)
        self.assertEqual(engine.active_gateway_id, "GW_PRIMARY_FIX")
        self.assertEqual(primary.status, GatewayStatus.ACTIVE)

    def test_single_latency_spike_does_not_trigger_failover(self):
        primary = primary_node(latency_rtt_ms=250.0, consecutive_latency_breaches=1)
        engine = build_engine(primary=primary, max_latency_rtt_ms=100.0)
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.outcome, FailoverOutcome.NO_ACTION)
        self.assertIn("CONTINUE_MONITORING", report.required_operator_action)
        self.assertEqual(engine.active_gateway_id, "GW_PRIMARY_FIX")

    def test_latency_trigger_disabled_by_default(self):
        # max_latency_rtt_ms defaults to None: a slow but live session is not a
        # failover trigger unless the operator opts in.
        primary = primary_node(latency_rtt_ms=5_000.0, consecutive_latency_breaches=99)
        engine = build_engine(primary=primary)
        self.assertEqual(
            engine.audit_gateway_health_and_failover([]).outcome, FailoverOutcome.NO_ACTION
        )


class TestSplitBrainGuard(unittest.TestCase):
    def test_live_socket_blocks_failover_until_fenced(self):
        # Regression: promoting the standby while the failing session still holds a
        # live socket puts two sessions on the wire — the split-brain this skill
        # claims to prevent.
        primary = primary_node(
            latency_rtt_ms=500.0, consecutive_latency_breaches=5, tcp_connected=True
        )
        secondary = secondary_node()
        engine = build_engine(primary=primary, secondary=secondary, max_latency_rtt_ms=100.0)

        report = engine.audit_gateway_health_and_failover([])

        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_BLOCKED)
        self.assertIn("FENCE_ACTIVE_GATEWAY", report.required_operator_action)
        self.assertIsNone(report.promoted_gateway_id)
        self.assertEqual(engine.active_gateway_id, "GW_PRIMARY_FIX")
        self.assertEqual(primary.status, GatewayStatus.ACTIVE)
        self.assertEqual(secondary.status, GatewayStatus.STANDBY)

    def test_fence_confirmation_allows_failover(self):
        primary = primary_node(
            latency_rtt_ms=500.0, consecutive_latency_breaches=5, tcp_connected=True
        )
        secondary = secondary_node()
        engine = build_engine(primary=primary, secondary=secondary, max_latency_rtt_ms=100.0)

        report = engine.audit_gateway_health_and_failover([], fence_confirmed=True)

        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_SUCCESS)
        self.assertEqual(primary.status, GatewayStatus.QUIESCED)
        self.assertEqual(secondary.status, GatewayStatus.ACTIVE)
        self.assertEqual(engine.active_gateway_id, "GW_SECONDARY_FIX")

    def test_dead_socket_needs_no_fence(self):
        primary = primary_node(tcp_connected=False)
        engine = build_engine(primary=primary)
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_SUCCESS)
        self.assertIn("TCP_DISCONNECT", report.failover_trigger_reason)
        self.assertEqual(primary.status, GatewayStatus.DISCONNECTED)


class TestStandbyPreflight(unittest.TestCase):
    def test_unhealthy_standby_is_not_promoted(self):
        primary = primary_node(tcp_connected=False)
        secondary = secondary_node(tcp_connected=False)
        engine = build_engine(primary=primary, secondary=secondary)

        report = engine.audit_gateway_health_and_failover([])

        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_FAILED)
        self.assertIsNone(report.promoted_gateway_id)
        self.assertIn("NO_HEALTHY_STANDBY", report.required_operator_action)
        self.assertEqual(engine.active_gateway_id, "GW_PRIMARY_FIX")
        self.assertEqual(secondary.status, GatewayStatus.STANDBY)

    def test_second_failover_does_not_promote_the_failed_node_back(self):
        # After failover the old primary is DISCONNECTED, not STANDBY. A later
        # failure of the new active must NOT hand flow back to a node nobody repaired.
        primary = primary_node(tcp_connected=False)
        secondary = secondary_node()
        engine = build_engine(primary=primary, secondary=secondary)
        self.assertEqual(
            engine.audit_gateway_health_and_failover([]).outcome, FailoverOutcome.FAILOVER_SUCCESS
        )

        secondary.tcp_connected = False
        report = engine.audit_gateway_health_and_failover([])

        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_FAILED)
        self.assertEqual(engine.active_gateway_id, "GW_SECONDARY_FIX")
        self.assertEqual(primary.status, GatewayStatus.DISCONNECTED)


class TestSequenceHandling(unittest.TestCase):
    def test_standby_continues_its_own_sequence_not_the_failed_nodes(self):
        # Regression against copying max(primary, secondary) into the standby:
        # sequence numbers belong to a session, and 150/151 here would desynchronise
        # the standby's own session and trigger an immediate counterparty logout.
        engine = build_engine(primary=primary_node(tcp_connected=False))
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.standby_next_out_seq_num, 146)  # 145 + 1
        self.assertNotIn(report.standby_next_out_seq_num, (150, 151))
        self.assertEqual(report.sequence_policy, SequenceResetPolicy.CONTINUE_SESSION)

    def test_reset_on_logon_policy_starts_at_one(self):
        profile = VenueRecoveryProfile(
            venue_id="FIX_WITH_RESET",
            sequence_policy=SequenceResetPolicy.RESET_ON_LOGON,
            order_state_recovery=OrderStateRecoveryMethod.ORDER_STATUS_REQUEST,
            non_persistent_orders_survive_disconnect=True,
            resend_marking="PossResend(97)=Y",
            source_note="ResetSeqNumFlag(141)=Y negotiated bilaterally.",
        )
        engine = build_engine(primary=primary_node(tcp_connected=False), venue_profile=profile)
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.standby_next_out_seq_num, 1)
        self.assertIn("ResetSeqNumFlag(141)=Y", report.sequence_policy_note)

    def test_eti_restarts_every_connection_at_one(self):
        # T7 ETI s6.6: every connection, reconnects included, logs on at MsgSeqNum=1
        # and there is no sequence recovery.
        engine = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        )
        report = engine.audit_gateway_health_and_failover([])
        self.assertEqual(report.standby_next_out_seq_num, 1)
        self.assertEqual(report.sequence_policy, SequenceResetPolicy.RESTART_AT_ONE)


class TestOrderRecoveryPlan(unittest.TestCase):
    def setUp(self):
        self.orders = [
            InFlightOrder("ORD_101", "AAPL", "BUY", 100, 185.0, "PENDING_NEW"),
            InFlightOrder("ORD_102", "AAPL", "SELL", 50, 186.0, "FILLED"),
            InFlightOrder("ORD_103", "AAPL", "BUY", 25, 184.0, "PENDING_CANCEL"),
        ]
        self.engine = build_engine(primary=primary_node(tcp_connected=False))

    def test_unknown_orders_are_never_auto_resent(self):
        # The original engine flipped a duplicate flag on every PENDING_NEW order and
        # called it recovery. An unacknowledged order may already be live at the venue.
        report = self.engine.audit_gateway_health_and_failover(self.orders)
        self.assertEqual(report.orders_requiring_reconciliation, ["ORD_101", "ORD_103"])
        actions = {d.cl_ord_id: d.action for d in report.order_recovery_plan}
        self.assertEqual(actions["ORD_101"], OrderRecoveryAction.RECONCILE_REQUIRED)
        self.assertEqual(actions["ORD_103"], OrderRecoveryAction.RECONCILE_REQUIRED)
        self.assertEqual(actions["ORD_102"], OrderRecoveryAction.NO_ACTION)
        self.assertFalse(any(order.poss_resend for order in self.orders))

    def test_caller_orders_are_not_mutated(self):
        before = [(o.order_status, o.poss_resend) for o in self.orders]
        self.engine.audit_gateway_health_and_failover(self.orders)
        self.assertEqual([(o.order_status, o.poss_resend) for o in self.orders], before)

    def test_recovery_method_is_venue_specific(self):
        report = self.engine.audit_gateway_health_and_failover(self.orders)
        methods = {d.recovery_method for d in report.order_recovery_plan if d.recovery_method}
        self.assertEqual(methods, {OrderStateRecoveryMethod.ORDER_STATUS_REQUEST})

        eti_engine = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        )
        eti_report = eti_engine.audit_gateway_health_and_failover(self.orders)
        eti_methods = {d.recovery_method for d in eti_report.order_recovery_plan if d.recovery_method}
        # ETI supports no order status inquiry at all; state comes from restatement.
        self.assertEqual(eti_methods, {OrderStateRecoveryMethod.RESTATEMENT_BROADCAST})

    def test_non_persistent_orders_on_eti_are_reentered_not_resent(self):
        # T7 ETI s5.4: non-persistent orders and quotes are deleted on session loss
        # and are never restated, so the original ClOrdID must not be resent.
        orders = [InFlightOrder("ORD_LEAN", "FDAX", "BUY", 1, 18_000.0, "PENDING_NEW", persistent=False)]
        engine = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        )
        report = engine.audit_gateway_health_and_failover(orders)
        self.assertEqual(report.orders_to_reenter_as_new, ["ORD_LEAN"])
        self.assertEqual(report.orders_requiring_reconciliation, [])

    def test_acknowledged_non_persistent_orders_are_also_deleted_by_eti(self):
        # An ack does not protect a lean order: T7 deletes non-persistent orders on
        # session loss whether or not we heard back about them.
        orders = [
            InFlightOrder("ORD_RESTING", "FDAX", "BUY", 5, 18_000.0, "NEW", persistent=False),
            InFlightOrder("ORD_PART", "FDAX", "BUY", 5, 17_990.0, "PARTIALLY_FILLED", persistent=False),
            InFlightOrder("ORD_DONE", "FDAX", "BUY", 5, 17_980.0, "FILLED", persistent=False),
        ]
        engine = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        )
        report = engine.audit_gateway_health_and_failover(orders)
        self.assertEqual(report.orders_to_reenter_as_new, ["ORD_RESTING", "ORD_PART"])
        by_id = {d.cl_ord_id: d for d in report.order_recovery_plan}
        self.assertEqual(by_id["ORD_DONE"].action, OrderRecoveryAction.NO_ACTION)
        self.assertIn("residual quantity", by_id["ORD_PART"].rationale)

    def test_acknowledged_persistent_resting_orders_need_no_reentry(self):
        orders = [InFlightOrder("ORD_RESTING", "FDAX", "BUY", 5, 18_000.0, "NEW", persistent=True)]
        report = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        ).audit_gateway_health_and_failover(orders)
        self.assertEqual(report.orders_to_reenter_as_new, [])
        self.assertEqual(report.orders_requiring_reconciliation, [])
        self.assertEqual(report.order_recovery_plan[0].action, OrderRecoveryAction.NO_ACTION)

    def test_non_persistent_orders_still_reconcile_where_they_survive(self):
        orders = [InFlightOrder("ORD_LEAN", "AAPL", "BUY", 1, 185.0, "PENDING_NEW", persistent=False)]
        report = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=GENERIC_FIX_PROFILE
        ).audit_gateway_health_and_failover(orders)
        self.assertEqual(report.orders_requiring_reconciliation, ["ORD_LEAN"])
        self.assertEqual(report.orders_to_reenter_as_new, [])

    def test_duplicate_cl_ord_id_is_rejected_without_half_failing_over(self):
        # ClOrdID is the venue's own dedup key; duplicates make the recovery plan
        # meaningless. Rejection must happen before any role change, or the engine
        # is left promoted with no report of it.
        dupes = [
            InFlightOrder("ORD_101", "AAPL", "BUY", 100, 185.0, "PENDING_NEW"),
            InFlightOrder("ORD_101", "AAPL", "BUY", 100, 185.0, "PENDING_NEW"),
        ]
        with self.assertRaises(ValueError):
            self.engine.audit_gateway_health_and_failover(dupes)
        self.assertEqual(self.engine.active_gateway_id, "GW_PRIMARY_FIX")
        self.assertEqual(self.engine.secondary.status, GatewayStatus.STANDBY)


class TestResendPlan(unittest.TestCase):
    def setUp(self):
        self.orders = [
            InFlightOrder("ORD_101", "AAPL", "BUY", 100, 185.0, "PENDING_NEW"),
            InFlightOrder("ORD_103", "AAPL", "BUY", 25, 184.0, "PENDING_CANCEL"),
        ]
        self.engine = build_engine(primary=primary_node(tcp_connected=False))
        self.report = self.engine.audit_gateway_health_and_failover(self.orders)

    def test_absent_orders_resend_with_poss_resend_not_poss_dup(self):
        # PossResend(97) is the field for content re-sent under a *different*
        # sequence number; PossDupFlag(43) covers session-layer retransmission
        # reusing the same MsgSeqNum and is wrong here.
        plan = self.engine.build_resend_plan(
            self.orders,
            self.report,
            {
                "ORD_101": ReconciliationVerdict.ABSENT_AT_VENUE,
                "ORD_103": ReconciliationVerdict.PRESENT_AT_VENUE,
            },
        )
        self.assertEqual([o.cl_ord_id for o in plan], ["ORD_101"])
        self.assertTrue(plan[0].poss_resend)
        self.assertFalse(any(hasattr(o, "poss_dup_flag") for o in plan))
        # Returned copies, originals untouched.
        self.assertFalse(self.orders[0].poss_resend)
        self.assertIsNot(plan[0], self.orders[0])

    def test_unknown_verdict_refuses_to_resend(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.build_resend_plan(
                self.orders,
                self.report,
                {
                    "ORD_101": ReconciliationVerdict.UNKNOWN,
                    "ORD_103": ReconciliationVerdict.ABSENT_AT_VENUE,
                },
            )
        self.assertIn("ORD_101", str(ctx.exception))

    def test_missing_verdict_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.build_resend_plan(
                self.orders, self.report, {"ORD_101": ReconciliationVerdict.ABSENT_AT_VENUE}
            )
        self.assertIn("ORD_103", str(ctx.exception))

    def test_invalid_verdict_value_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.build_resend_plan(
                self.orders,
                self.report,
                {"ORD_101": "PROBABLY_GONE", "ORD_103": ReconciliationVerdict.PRESENT_AT_VENUE},
            )

    def test_eti_has_no_business_level_resend_path(self):
        engine = build_engine(
            primary=primary_node(tcp_connected=False), venue_profile=EUREX_T7_ETI_PROFILE
        )
        report = engine.audit_gateway_health_and_failover(self.orders)
        with self.assertRaises(NotImplementedError):
            engine.build_resend_plan(
                self.orders,
                report,
                {
                    "ORD_101": ReconciliationVerdict.ABSENT_AT_VENUE,
                    "ORD_103": ReconciliationVerdict.ABSENT_AT_VENUE,
                },
            )


class TestRecoveryTimeReporting(unittest.TestCase):
    def test_no_rto_is_claimed_without_a_measurement(self):
        # The old engine timed a handful of in-memory attribute writes and reported
        # it as an RTO under 50ms. It measured nothing about recovery.
        report = build_engine(primary=primary_node(tcp_connected=False)) \
            .audit_gateway_health_and_failover([])
        self.assertIsNone(report.estimated_rto_ms)
        self.assertGreaterEqual(report.decision_latency_ms, 0.0)

    def test_rto_includes_caller_measured_activation(self):
        report = build_engine(primary=primary_node(tcp_connected=False)) \
            .audit_gateway_health_and_failover([], standby_activation_ms=820.0)
        self.assertIsNotNone(report.estimated_rto_ms)
        self.assertGreaterEqual(report.estimated_rto_ms, 820.0)
        # Real recovery is dominated by connect/logon/reconcile, not by the decision.
        self.assertLess(report.decision_latency_ms, report.estimated_rto_ms)

    def test_blocked_failover_reports_no_rto(self):
        primary = primary_node(latency_rtt_ms=500.0, consecutive_latency_breaches=5)
        report = build_engine(primary=primary, max_latency_rtt_ms=100.0) \
            .audit_gateway_health_and_failover([], standby_activation_ms=820.0)
        self.assertEqual(report.outcome, FailoverOutcome.FAILOVER_BLOCKED)
        self.assertIsNone(report.estimated_rto_ms)

    def test_rejects_impossible_activation_measurement(self):
        engine = build_engine(primary=primary_node(tcp_connected=False))
        with self.assertRaises(ValueError):
            engine.audit_gateway_health_and_failover([], standby_activation_ms=-1.0)
        with self.assertRaises(ValueError):
            engine.audit_gateway_health_and_failover([], standby_activation_ms=float("inf"))


if __name__ == "__main__":
    unittest.main()
