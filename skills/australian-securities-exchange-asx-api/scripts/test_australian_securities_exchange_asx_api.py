import unittest
from datetime import datetime, timezone

from australian_securities_exchange_asx_api import (
    AsxConnectionConfig,
    AsxConnectionState,
    AsxIntegrationEngine,
    AsxMarketPhase,
    AsxProtocol,
    AsxSequenceTracker,
    AsxSessionSchedule,
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

    def test_phase_closed_after_cspa(self):
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(16, 30, 0)), AsxMarketPhase.CLOSED)
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(3, 0, 0)), AsxMarketPhase.CLOSED)

    def test_phase_boundary_pre_open_start(self):
        # 07:00:00 exactly is the start of PRE_OPEN
        self.assertEqual(AsxSessionSchedule.phase_at(_sydney_naive(7, 0, 0)), AsxMarketPhase.PRE_OPEN)

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

    def test_order_entry_window(self):
        self.assertTrue(AsxSessionSchedule.is_order_entry_window(_sydney_naive(8, 0)))
        self.assertTrue(AsxSessionSchedule.is_order_entry_window(_sydney_naive(12, 0)))
        self.assertFalse(AsxSessionSchedule.is_order_entry_window(_sydney_naive(17, 0)))

    def test_connect_during_closed_warns_but_succeeds(self):
        engine = AsxIntegrationEngine(self._fix_config())
        self.assertTrue(engine.connect(at=_sydney_naive(17, 0)))
        self.assertEqual(engine.state, AsxConnectionState.CONNECTED)
        self.assertEqual(engine.market_phase(at=_sydney_naive(12, 0)), AsxMarketPhase.NORMAL)

    def test_market_phase_returns_normal_for_default_now(self):
        # Smoke test: default argument (now) must not raise and must return a member.
        phase = AsxIntegrationEngine(self._fix_config()).market_phase()
        self.assertIsInstance(phase, AsxMarketPhase)


if __name__ == "__main__":
    unittest.main()
