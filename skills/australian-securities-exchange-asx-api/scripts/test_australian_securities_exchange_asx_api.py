import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from australian_securities_exchange_asx_api import (
    AsxConnectionConfig,
    AsxConnectionState,
    AsxIntegrationEngine,
    AsxMarketPhase,
    AsxProtocol,
    AsxSequenceTracker,
    AsxSessionSchedule,
    InboundSeqNumStatus,
)


def _sydney_naive(h: int, m: int, s: int = 0) -> datetime:
    """A naive datetime interpreted by the schedule as Sydney wall-clock."""
    return datetime(2024, 3, 4, h, m, s)  # date is arbitrary; only time-of-day matters


class TestAsxIntegrationEngine(unittest.TestCase):

    def _fix_config(self, **overrides) -> AsxConnectionConfig:
        defaults = dict(
            host="fix.cde.asx.com.au",
            port=443,
            comp_id="TESTCOMP1",
            protocol=AsxProtocol.FIX_5_0_SP2,
            is_alc_colocated=False,
            is_cde_environment=True,
            heartbeat_interval_seconds=30,
        )
        defaults.update(overrides)
        return AsxConnectionConfig(**defaults)

    # ---- original, behaviour-preserving tests ----

    def test_valid_fix_connection(self):
        engine = AsxIntegrationEngine(self._fix_config())
        self.assertEqual(engine.state, AsxConnectionState.DISCONNECTED)
        self.assertTrue(engine.connect())
        self.assertEqual(engine.state, AsxConnectionState.CONNECTED)
        engine.disconnect()
        self.assertEqual(engine.state, AsxConnectionState.DISCONNECTED)

    def test_invalid_ouch_topology(self):
        config = AsxConnectionConfig(
            host="ouch.cde.asx.com.au", port=12345, comp_id="HFTCOMP",
            protocol=AsxProtocol.OUCH, is_alc_colocated=False, is_cde_environment=True,
        )
        with self.assertRaises(ValueError):
            AsxIntegrationEngine(config)

    def test_valid_itch_topology(self):
        config = AsxConnectionConfig(
            host="itch.prod.asx.com.au", port=54321, comp_id="MKT_DATA",
            protocol=AsxProtocol.ITCH, is_alc_colocated=True, is_cde_environment=False,
        )
        engine = AsxIntegrationEngine(config)
        self.assertEqual(engine.config.protocol, AsxProtocol.ITCH)

    # ---- connect / disconnect idempotency ----

    def test_connect_is_idempotent(self):
        engine = AsxIntegrationEngine(self._fix_config())
        self.assertTrue(engine.connect())
        self.assertTrue(engine.connect())  # second call is a no-op, still True
        self.assertEqual(engine.state, AsxConnectionState.CONNECTED)

    def test_disconnect_is_idempotent(self):
        engine = AsxIntegrationEngine(self._fix_config())
        self.assertTrue(engine.disconnect())  # disconnect when already DISCONNECTED
        engine.connect()
        self.assertTrue(engine.disconnect())
        self.assertTrue(engine.disconnect())  # second call is a no-op

    # ---- heartbeat interval validation ----

    def test_fix_heartbeat_too_low_rejected(self):
        with self.assertRaises(ValueError):
            AsxIntegrationEngine(self._fix_config(heartbeat_interval_seconds=9))

    def test_fix_heartbeat_too_high_rejected(self):
        with self.assertRaises(ValueError):
            AsxIntegrationEngine(self._fix_config(heartbeat_interval_seconds=61))

    def test_fix_heartbeat_boundaries_accepted(self):
        for hb in (10, 60):
            engine = AsxIntegrationEngine(self._fix_config(heartbeat_interval_seconds=hb))
            self.assertEqual(engine.config.heartbeat_interval_seconds, hb)

    def test_ouch_has_no_heartbeat_constraint(self):
        # heartbeat_interval_seconds is a FIX concept; OUCH/ITCH ignore it.
        config = AsxConnectionConfig(
            host="ouch.cde.asx.com.au", port=12345, comp_id="HFTCOMP",
            protocol=AsxProtocol.OUCH, is_alc_colocated=True, is_cde_environment=True,
            heartbeat_interval_seconds=999,
        )
        engine = AsxIntegrationEngine(config)  # must NOT raise
        self.assertEqual(engine.sequence, None)  # no FIX sequence tracker

    # ---- connection-parameter validation ----

    def test_empty_comp_id_rejected(self):
        for bad in ("", "   "):
            with self.assertRaises(ValueError):
                AsxIntegrationEngine(self._fix_config(comp_id=bad))

    def test_empty_host_rejected(self):
        for bad in ("", "  "):
            with self.assertRaises(ValueError):
                AsxIntegrationEngine(self._fix_config(host=bad))

    def test_out_of_range_port_rejected(self):
        for bad in (0, -1, 65536):
            with self.assertRaises(ValueError):
                AsxIntegrationEngine(self._fix_config(port=bad))

    def test_port_boundaries_accepted(self):
        for ok in (1, 65535):
            engine = AsxIntegrationEngine(self._fix_config(port=ok))
            self.assertEqual(engine.config.port, ok)

    # ---- test-host warning path ----

    def test_test_host_without_cde_flags_warning(self):
        # Should not raise, only warn. Validates the non-CDE + test-host branch.
        engine = AsxIntegrationEngine(self._fix_config(
            host="testfix.asx.com.au", is_cde_environment=False,
        ))
        self.assertEqual(engine.config.host, "testfix.asx.com.au")

    # ---- sequence tracker ----

    def test_sequence_tracker_monotonic(self):
        tracker = AsxSequenceTracker()
        self.assertEqual(tracker.next(), 1)
        self.assertEqual(tracker.next(), 2)
        self.assertEqual(tracker.next(), 3)
        self.assertEqual(tracker.expected_outbound, 4)

    def test_sequence_tracker_reset(self):
        tracker = AsxSequenceTracker()
        tracker.next(); tracker.next()
        tracker.reset()
        self.assertEqual(tracker.next(), 1)

    def test_inbound_gap_detection_first_message(self):
        self.assertFalse(AsxSequenceTracker.detect_inbound_gap(None, 1))
        self.assertTrue(AsxSequenceTracker.detect_inbound_gap(None, 2))  # not starting at 1

    def test_inbound_gap_detection_sequence(self):
        last = 5
        self.assertFalse(AsxSequenceTracker.detect_inbound_gap(last, 6))   # in-order
        self.assertTrue(AsxSequenceTracker.detect_inbound_gap(last, 8))    # gap forward
        self.assertTrue(AsxSequenceTracker.detect_inbound_gap(last, 5))    # replay/dup
        self.assertTrue(AsxSequenceTracker.detect_inbound_gap(last, 4))    # out-of-order

    def test_engine_provides_sequence_tracker_for_fix_only(self):
        fix_engine = AsxIntegrationEngine(self._fix_config())
        self.assertIsInstance(fix_engine.sequence, AsxSequenceTracker)
        itch_engine = AsxIntegrationEngine(AsxConnectionConfig(
            host="itch.prod.asx.com.au", port=54321, comp_id="MKT_DATA",
            protocol=AsxProtocol.ITCH, is_alc_colocated=True, is_cde_environment=False,
        ))
        self.assertIsNone(itch_engine.sequence)

    # ---- inbound sequence-number classification ----
    # A forward gap and a too-low number both "are a gap", but the FIX-correct
    # responses are opposite: ResendRequest (2) vs. Logout (5) + terminate. These
    # tests pin the distinction the boolean predicate cannot express.

    def test_classify_in_sequence(self):
        self.assertIs(
            AsxSequenceTracker.classify_inbound(None, 1), InboundSeqNumStatus.IN_SEQUENCE,
        )
        self.assertIs(
            AsxSequenceTracker.classify_inbound(5, 6), InboundSeqNumStatus.IN_SEQUENCE,
        )

    def test_classify_forward_gap_requests_resend(self):
        self.assertIs(AsxSequenceTracker.classify_inbound(5, 8), InboundSeqNumStatus.GAP)
        self.assertIs(AsxSequenceTracker.classify_inbound(None, 4), InboundSeqNumStatus.GAP)

    def test_classify_too_low_is_fatal_not_a_resend(self):
        # PossDupFlag (43) not set and MsgSeqNum below expected: FIX session layer
        # requires Logout (5) with SessionStatus (1409) = 9, not a ResendRequest.
        self.assertIs(AsxSequenceTracker.classify_inbound(5, 5), InboundSeqNumStatus.TOO_LOW)
        self.assertIs(AsxSequenceTracker.classify_inbound(5, 1), InboundSeqNumStatus.TOO_LOW)

    def test_classify_poss_dup_is_not_a_session_error(self):
        # Regression: messages replayed in response to our own ResendRequest carry
        # PossDupFlag (43) = Y and must not be treated as a fatal too-low sequence.
        self.assertIs(
            AsxSequenceTracker.classify_inbound(5, 3, poss_dup=True),
            InboundSeqNumStatus.POSS_DUP,
        )
        self.assertIs(
            AsxSequenceTracker.classify_inbound(5, 5, poss_dup=True),
            InboundSeqNumStatus.POSS_DUP,
        )

    def test_classify_poss_dup_does_not_mask_a_forward_gap(self):
        # PossDupFlag=Y on a number ABOVE expected is still a gap.
        self.assertIs(
            AsxSequenceTracker.classify_inbound(5, 9, poss_dup=True), InboundSeqNumStatus.GAP,
        )

    def test_classify_rejects_invalid_sequence_numbers(self):
        with self.assertRaises(ValueError):
            AsxSequenceTracker.classify_inbound(5, 0)
        with self.assertRaises(ValueError):
            AsxSequenceTracker.classify_inbound(5, -3)
        with self.assertRaises(ValueError):
            AsxSequenceTracker.classify_inbound(0, 4)

    # ---- session schedule / market phase ----

    def test_phase_pre_open(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(8, 30)), AsxMarketPhase.PRE_OPEN)

    def test_phase_opening_auction(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(9, 59, 20)), AsxMarketPhase.OPENING_AUCTION)

    def test_phase_normal(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(12, 0, 0)), AsxMarketPhase.NORMAL)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(10, 0, 0)), AsxMarketPhase.NORMAL)

    def test_phase_pre_cspa(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 5, 0)), AsxMarketPhase.PRE_CSPA)

    def test_phase_closing_auction(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 10, 30)), AsxMarketPhase.CLOSING_AUCTION)

    # Regression: before the Service Release 15 schedule was modelled, everything
    # from 16:11 onwards collapsed into CLOSED, which hid the Post Close trading
    # session (16:11:00-16:21:30, matching at the CSPA price) entirely.

    def test_phase_post_close(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 15, 0)), AsxMarketPhase.POST_CLOSE)

    def test_phase_boundary_post_close_start(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 11, 0)), AsxMarketPhase.POST_CLOSE)

    def test_phase_boundary_post_close_end(self):
        # 16:21:29 is the last Post Close second; 16:21:30 begins Adjust.
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 21, 29)), AsxMarketPhase.POST_CLOSE)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 21, 30)), AsxMarketPhase.ADJUST)

    def test_phase_adjust_covers_adjust_and_adjust_on(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 30, 0)), AsxMarketPhase.ADJUST)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(17, 0, 0)), AsxMarketPhase.ADJUST)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(18, 49, 59)), AsxMarketPhase.ADJUST)

    def test_phase_closed_after_adjust(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(18, 50, 0)), AsxMarketPhase.CLOSED)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(21, 0, 0)), AsxMarketPhase.CLOSED)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(3, 0, 0)), AsxMarketPhase.CLOSED)

    def test_phase_boundary_pre_open_start(self):
        # 07:00:00 exactly is the start of PRE_OPEN
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(7, 0, 0)), AsxMarketPhase.PRE_OPEN)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(6, 59, 59)), AsxMarketPhase.CLOSED)

    def test_phase_boundary_normal_start(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(9, 59, 45)), AsxMarketPhase.NORMAL)

    def test_tz_aware_datetime_converted_to_sydney_wallclock(self):
        # 2024-03-04 is AEDT (UTC+11). 23:00 UTC == 10:00 next-day Sydney == NORMAL.
        dt_utc = datetime(2024, 3, 4, 23, 0, 0, tzinfo=timezone.utc)
        # Build a naive datetime that represents the equivalent Sydney wall-clock.
        sydney_naive = datetime(2024, 3, 5, 10, 0, 0)
        self.assertEqual(
            AsxSessionSchedule.phase_at(dt_utc),
            AsxSessionSchedule.phase_at(sydney_naive),
        )
        self.assertEqual(AsxSessionSchedule.phase_at(dt_utc), AsxMarketPhase.NORMAL)

    def test_daylight_saving_boundaries_use_local_wallclock(self):
        # The Sydney UTC offset differs across DST but the local session times do
        # not: 00:00 UTC is 11:00 Sydney in AEDT (January) and 10:00 Sydney in AEST
        # (July). Both are NORMAL, and the AEST->AEDT shift must not move the open.
        aedt = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)   # 11:00 Sydney
        aest = datetime(2024, 7, 15, 0, 0, 0, tzinfo=timezone.utc)   # 10:00 Sydney
        self.assertEqual(AsxSessionSchedule.phase_at(aedt), AsxMarketPhase.NORMAL)
        self.assertEqual(AsxSessionSchedule.phase_at(aest), AsxMarketPhase.NORMAL)
        # 23:00 UTC in AEDT is 10:00 next-day Sydney (NORMAL); in AEST it is 09:00
        # Sydney, which is still PRE_OPEN. A fixed-offset implementation gets one
        # of these two wrong.
        self.assertEqual(
            AsxSessionSchedule.phase_at(datetime(2024, 1, 14, 23, 0, tzinfo=timezone.utc)),
            AsxMarketPhase.NORMAL,
        )
        self.assertEqual(
            AsxSessionSchedule.phase_at(datetime(2024, 7, 14, 23, 0, tzinfo=timezone.utc)),
            AsxMarketPhase.PRE_OPEN,
        )

    def test_order_entry_window(self):
        self.assertTrue(AsxSessionSchedule.is_order_entry_window(_sydney_naive(8, 0)))
        self.assertTrue(AsxSessionSchedule.is_order_entry_window(_sydney_naive(12, 0)))
        self.assertFalse(AsxSessionSchedule.is_order_entry_window(_sydney_naive(17, 0)))

    def test_post_close_accepts_new_orders(self):
        # Regression: Post Close is a real trading session (matching at the CSPA
        # price), not part of CLOSED. Gating order entry off it drops live liquidity.
        self.assertTrue(AsxSessionSchedule.is_order_entry_window(_sydney_naive(16, 15)))

    def test_adjust_blocks_new_orders_but_allows_amend_cancel(self):
        # ASX accepts no new orders and executes no trades during Adjust, but
        # participants may still cancel/amend. A kill-switch must not conclude that
        # its resting orders are untouchable.
        self.assertFalse(AsxSessionSchedule.is_order_entry_window(_sydney_naive(17, 0)))
        self.assertTrue(AsxSessionSchedule.is_amend_cancel_window(_sydney_naive(17, 0)))

    def test_amend_cancel_window_false_when_closed(self):
        self.assertFalse(AsxSessionSchedule.is_amend_cancel_window(_sydney_naive(19, 30)))
        self.assertFalse(AsxSessionSchedule.is_amend_cancel_window(_sydney_naive(3, 0)))

    def test_amend_cancel_window_is_superset_of_order_entry(self):
        for h, m in ((8, 0), (9, 59), (12, 0), (16, 5), (16, 10), (16, 15), (17, 0), (20, 0)):
            dt = _sydney_naive(h, m)
            if AsxSessionSchedule.is_order_entry_window(dt):
                self.assertTrue(
                    AsxSessionSchedule.is_amend_cancel_window(dt),
                    msg=f"amend/cancel must be permitted whenever order entry is, at {h}:{m}",
                )

    def test_connect_outside_order_entry_warns_but_succeeds(self):
        engine = AsxIntegrationEngine(self._fix_config())
        self.assertTrue(engine.connect(at=_sydney_naive(20, 0)))
        self.assertEqual(engine.state, AsxConnectionState.CONNECTED)
        self.assertEqual(engine.market_phase(at=_sydney_naive(12, 0)), AsxMarketPhase.NORMAL)

    def test_market_phase_default_now_is_timezone_aware(self):
        # Regression: the default used to be `datetime.now()`, whose naive host-local
        # wall-clock the schedule reads as Sydney time. On a UTC host that is a 10-11
        # hour misclassification. Pin the behaviour by running the default path with
        # a UTC "now" that is unambiguously NORMAL in Sydney and CLOSED read naively.
        engine = AsxIntegrationEngine(self._fix_config())
        fake_utc_now = datetime(2024, 7, 15, 3, 0, 0, tzinfo=timezone.utc)  # 13:00 Sydney
        real_datetime = datetime

        class _FrozenDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    # naive host-local clock of a UTC server
                    return fake_utc_now.replace(tzinfo=None)
                return fake_utc_now.astimezone(tz)

        with mock.patch(
            "australian_securities_exchange_asx_api.datetime", _FrozenDatetime,
        ):
            self.assertEqual(engine.market_phase(), AsxMarketPhase.NORMAL)
        # Sanity: the naive reading of the same instant is NOT the correct phase,
        # so the assertion above genuinely discriminates between the two.
        self.assertEqual(
            AsxSessionSchedule.phase_at(fake_utc_now.replace(tzinfo=None)),
            AsxMarketPhase.CLOSED,
        )

    def test_market_phase_returns_a_phase_for_real_now(self):
        # Smoke test: default argument (now) must not raise and must return a member.
        phase = AsxIntegrationEngine(self._fix_config()).market_phase()
        self.assertIsInstance(phase, AsxMarketPhase)

    def test_every_minute_of_the_day_maps_to_a_phase(self):
        # Exhaustive sweep: the phase table must be total and must never leave a
        # hole between the SR15 phases.
        seen = set()
        base = datetime(2024, 3, 4, 0, 0, 0)
        for minute in range(24 * 60):
            phase = AsxSessionSchedule.phase_at(base + timedelta(minutes=minute))
            self.assertIsInstance(phase, AsxMarketPhase)
            seen.add(phase)
        self.assertEqual(seen, set(AsxMarketPhase))


if __name__ == "__main__":
    unittest.main()
