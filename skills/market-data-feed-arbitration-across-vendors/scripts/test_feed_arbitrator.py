"""Unit tests for market-data-feed-arbitration-across-vendors skill.

Timestamps are supplied explicitly throughout: arbitration is a function of the local
receipt clock, and a test that reads the wall clock cannot pin a staleness boundary.
"""
import math
import unittest

from feed_arbitrator import (
    ArbitrationDecision,
    MarketDataFeedArbitrator,
    VendorStatus,
)

T0 = 1_700_000_000.0


class TestConsensusAndSingleFeed(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator(max_divergence_pct=0.05, max_stale_seconds=2.0)

    def test_consensus_arbitration_within_tolerance(self):
        # Primary 100.00 vs secondary 100.02: divergence = 0.02 / 100.01 = 0.019998% <= 0.05%.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.02, timestamp=T0)

        self.assertTrue(res.is_arbitrated)
        self.assertTrue(res.is_trusted)
        self.assertTrue(res.is_cross_verified)
        self.assertEqual(res.decision, ArbitrationDecision.CONSENSUS)
        self.assertEqual(res.active_vendor, "CONSENSUS_BOTH")
        self.assertAlmostEqual(res.consensus_price, 100.01, places=9)
        self.assertEqual(res.primary_vendor_status, VendorStatus.HEALTHY)
        # Independently derived: 100 * |100.00 - 100.02| / 100.01.
        self.assertAlmostEqual(res.relative_divergence_pct, 0.019998, places=6)
        self.assertEqual(res.feed_age_gap_seconds, 0.0)

    def test_single_feed_is_trusted_but_not_cross_verified(self):
        res = self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)

        self.assertEqual(res.decision, ArbitrationDecision.SINGLE_FEED)
        self.assertTrue(res.is_trusted)
        self.assertFalse(res.is_cross_verified)
        self.assertIsNone(res.relative_divergence_pct)
        self.assertEqual(res.secondary_vendor_status, VendorStatus.NO_DATA)

    def test_divergence_is_none_not_zero_when_no_comparison_happened(self):
        # Regression: reporting 0.0 divergence during a failover reads downstream as
        # "the feeds agreed exactly" when in fact nothing was compared.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.05, timestamp=T0 + 3.0)

        self.assertEqual(res.decision, ArbitrationDecision.FAILOVER)
        self.assertIsNone(res.relative_divergence_pct)

    def test_timestamp_zero_is_honoured_not_replaced_by_wall_clock(self):
        # Regression for `timestamp or time.time()`: 0.0 is falsy but is a valid clock
        # reading, and silently swapping in the wall clock destroys every age computation.
        res = self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=0.0)
        snapshot = self.arbitrator.snapshot()

        self.assertEqual(res.decision, ArbitrationDecision.SINGLE_FEED)
        self.assertEqual(snapshot[0][2], 0.0)


class TestStalenessAndBlackout(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator(max_divergence_pct=0.05, max_stale_seconds=2.0)

    def test_stale_vendor_failover(self):
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.05, timestamp=T0 + 3.0)

        self.assertEqual(res.primary_vendor_status, VendorStatus.STALE_TIMEOUT)
        self.assertEqual(res.active_vendor, "secondary")
        self.assertEqual(res.consensus_price, 100.05)
        self.assertEqual(res.decision, ArbitrationDecision.FAILOVER)
        self.assertTrue(res.is_trusted)
        self.assertFalse(res.is_cross_verified)

    def test_staleness_boundary_is_strictly_greater_than(self):
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        exactly_at_limit = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 2.0)
        self.assertEqual(exactly_at_limit.primary_vendor_status, VendorStatus.HEALTHY)

        just_past = self.arbitrator.evaluate_feed_health("AAPL", now=T0 + 2.0 + 1e-6)
        self.assertEqual(just_past.primary_vendor_status, VendorStatus.STALE_TIMEOUT)

    def test_total_blackout_detected_only_via_heartbeat_evaluation(self):
        # The failure this component exists for: BOTH vendors go silent. No tick arrives,
        # so tick-driven arbitration can never observe it.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0)

        res = self.arbitrator.evaluate_feed_health("AAPL", now=T0 + 10.0)

        self.assertEqual(res.decision, ArbitrationDecision.NO_TRUSTED_FEED)
        self.assertFalse(res.is_trusted)
        self.assertIsNone(res.consensus_price)
        self.assertEqual(res.primary_vendor_status, VendorStatus.STALE_TIMEOUT)
        self.assertEqual(res.secondary_vendor_status, VendorStatus.STALE_TIMEOUT)

    def test_stale_feeds_never_report_healthy_consensus(self):
        # Regression: two stale feeds that happen to agree must not be published as a
        # validated consensus with HEALTHY statuses.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0)

        res = self.arbitrator.evaluate_feed_health("AAPL", now=T0 + 5.0)

        self.assertNotEqual(res.decision, ArbitrationDecision.CONSENSUS)
        self.assertFalse(res.is_cross_verified)

    def test_unknown_symbol_health_check_reports_no_data_without_allocating_state(self):
        res = self.arbitrator.evaluate_feed_health("NOSUCH", now=T0)
        self.assertEqual(res.decision, ArbitrationDecision.NO_TRUSTED_FEED)
        self.assertEqual(res.primary_vendor_status, VendorStatus.NO_DATA)
        self.assertIsNone(res.consensus_price)
        # A monitor sweeping a large watchlist must not grow the state map.
        self.assertEqual(self.arbitrator.snapshot(), [])

    def test_failover_onto_a_quarantined_feed_is_untrusted(self):
        # The survivor of a stale-feed failover may be the feed that was quarantined for
        # disagreeing. It must not be silently promoted to sole price authority.
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05, max_stale_seconds=2.0, divergence_confirmation_seconds=0.0
        )
        arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        quarantined = arb.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0)
        self.assertEqual(quarantined.quarantined_vendor, "secondary")

        res = arb.process_vendor_tick("secondary", "AAPL", 105.10, timestamp=T0 + 3.0)

        self.assertEqual(res.decision, ArbitrationDecision.FAILOVER)
        self.assertEqual(res.active_vendor, "secondary")
        self.assertFalse(res.is_trusted)
        self.assertEqual(res.secondary_vendor_status, VendorStatus.DIVERGENT_OUTLIER)


class TestDivergenceHandling(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator(
            max_divergence_pct=0.05,
            max_stale_seconds=2.0,
            divergence_confirmation_seconds=1.0,
            recovery_consecutive_ticks=3,
        )

    def test_first_divergent_tick_is_untrusted_not_quarantined(self):
        # Regression: the previous behaviour quarantined the secondary vendor on the
        # first disagreeing tick and published the primary price as a normal result.
        # An earnings-gap fast market produces exactly this signature.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0)

        self.assertEqual(res.decision, ArbitrationDecision.DIVERGENCE_UNRESOLVED)
        self.assertFalse(res.is_trusted)
        self.assertFalse(res.is_cross_verified)
        self.assertIsNone(res.quarantined_vendor)
        self.assertEqual(res.secondary_vendor_status, VendorStatus.DIVERGENT_UNCONFIRMED)
        self.assertEqual(res.primary_vendor_status, VendorStatus.DIVERGENT_UNCONFIRMED)
        # Continuity price is still the reference feed's.
        self.assertEqual(res.consensus_price, 100.00)

    def test_transient_divergence_resolves_without_quarantine(self):
        # Primary leads a real 5% move; secondary catches up inside the confirmation
        # window. Nothing may be quarantined.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("primary", "AAPL", 105.00, timestamp=T0 + 0.10)
        mid = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 0.15)
        self.assertEqual(mid.decision, ArbitrationDecision.DIVERGENCE_UNRESOLVED)

        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0 + 0.30)

        self.assertEqual(res.decision, ArbitrationDecision.CONSENSUS)
        self.assertTrue(res.is_cross_verified)
        self.assertIsNone(res.quarantined_vendor)

    def test_persistent_divergence_escalates_to_policy_quarantine(self):
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0)
        # Keep both feeds fresh and disagreeing past the 1.0s confirmation window.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.01, timestamp=T0 + 0.9)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.01, timestamp=T0 + 0.9)
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.02, timestamp=T0 + 1.2)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.02, timestamp=T0 + 1.2)

        self.assertEqual(res.decision, ArbitrationDecision.QUARANTINE_ACTIVE)
        self.assertEqual(res.quarantined_vendor, "secondary")
        self.assertEqual(res.secondary_vendor_status, VendorStatus.DIVERGENT_OUTLIER)
        self.assertEqual(res.active_vendor, "primary")
        self.assertEqual(res.consensus_price, 100.02)
        self.assertTrue(res.is_trusted)
        self.assertFalse(res.is_cross_verified)

    def test_reference_vendor_policy_can_prefer_secondary(self):
        # The fallback is operator policy, not detection: with two feeds the outlier is
        # not identifiable, so the configured reference must win either way.
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05, divergence_confirmation_seconds=0.0, reference_vendor="secondary"
        )
        arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = arb.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0)

        self.assertEqual(res.quarantined_vendor, "primary")
        self.assertEqual(res.consensus_price, 105.00)

    def test_quarantine_releases_only_after_hysteresis(self):
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05, divergence_confirmation_seconds=0.0, recovery_consecutive_ticks=3
        )
        arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        quarantined = arb.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0)
        self.assertEqual(quarantined.quarantined_vendor, "secondary")

        # Every tick that lands while both feeds are fresh is one comparison. With
        # recovery_consecutive_ticks=3, the first two clean comparisons must not release
        # the quarantine and the third must.
        first = arb.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 0.1)
        self.assertEqual(first.decision, ArbitrationDecision.QUARANTINE_ACTIVE)

        second = arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0 + 0.1)
        self.assertEqual(second.decision, ArbitrationDecision.QUARANTINE_ACTIVE)
        self.assertEqual(second.quarantined_vendor, "secondary")

        released = arb.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 0.2)

        self.assertEqual(released.decision, ArbitrationDecision.CONSENSUS)
        self.assertIsNone(released.quarantined_vendor)
        self.assertTrue(released.is_cross_verified)

    def test_frozen_feed_is_quarantined_on_evidence_immediately(self):
        # A feed still delivering ticks but repeating one price while the counterpart
        # moves is attributable without any policy choice.
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05,
            max_stale_seconds=2.0,
            frozen_price_seconds=5.0,
            divergence_confirmation_seconds=30.0,  # policy fallback must not be what fires
        )
        first_quarantine = None
        for step in range(8):
            t = T0 + step * 1.0
            arb.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=t)  # frozen
            res = arb.process_vendor_tick("primary", "AAPL", 100.00 + step * 0.5, timestamp=t)
            if first_quarantine is None and res.quarantined_vendor is not None:
                first_quarantine = res

        self.assertIsNotNone(first_quarantine, "frozen feed was never quarantined")
        self.assertEqual(first_quarantine.decision, ArbitrationDecision.QUARANTINE_ACTIVE)
        self.assertEqual(first_quarantine.quarantined_vendor, "secondary")
        self.assertEqual(first_quarantine.secondary_vendor_status, VendorStatus.FROZEN_PRICE)
        self.assertEqual(first_quarantine.active_vendor, "primary")
        # The attribution must not be relabelled as a policy fallback on later ticks.
        self.assertEqual(res.secondary_vendor_status, VendorStatus.FROZEN_PRICE)

    def test_frozen_quarantine_does_not_release_until_the_feed_moves_again(self):
        # Regression: the counterpart's price wandering back across the stuck level is
        # not recovery. Without this the quarantine flaps on every random-walk crossing.
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.15,
            max_stale_seconds=2.0,
            frozen_price_seconds=5.0,
            recovery_consecutive_ticks=3,
            divergence_confirmation_seconds=30.0,
        )
        t = T0
        # Secondary frozen at 100.00 while primary walks away, for long enough to attribute.
        for step in range(8):
            t = T0 + step * 1.0
            arb.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=t)
            res = arb.process_vendor_tick("primary", "AAPL", 100.00 + step * 0.5, timestamp=t)
        self.assertEqual(res.quarantined_vendor, "secondary")

        # Primary walks back onto the frozen price. The frozen feed has still not moved.
        for step in range(1, 5):
            t += 0.1
            arb.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=t)
            res = arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=t)
            self.assertEqual(
                res.decision, ArbitrationDecision.QUARANTINE_ACTIVE, f"crossing tick {step}"
            )
            self.assertEqual(res.secondary_vendor_status, VendorStatus.FROZEN_PRICE)

        # The secondary starts moving again and now agrees: the quarantine may release.
        for offset in (0.1, 0.2, 0.3):
            t += offset
            arb.process_vendor_tick("secondary", "AAPL", 100.01, timestamp=t)
            res = arb.process_vendor_tick("primary", "AAPL", 100.01, timestamp=t)

        self.assertEqual(res.decision, ArbitrationDecision.CONSENSUS)
        self.assertIsNone(res.quarantined_vendor)

    def test_non_simultaneous_divergence_is_unverified_and_blames_nobody(self):
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05, max_stale_seconds=2.0, max_comparison_age_seconds=0.25
        )
        arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = arb.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0 + 1.0)

        self.assertEqual(res.decision, ArbitrationDecision.LATENCY_SKEW_UNVERIFIED)
        self.assertFalse(res.is_trusted)
        self.assertIsNone(res.quarantined_vendor)
        self.assertEqual(res.consensus_price, 105.00)  # freshest observation
        self.assertAlmostEqual(res.feed_age_gap_seconds, 1.0, places=9)

    def test_agreeing_but_non_simultaneous_feeds_are_not_averaged(self):
        arb = MarketDataFeedArbitrator(max_comparison_age_seconds=0.25, max_stale_seconds=2.0)
        arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = arb.process_vendor_tick("secondary", "AAPL", 100.02, timestamp=T0 + 1.0)

        self.assertEqual(res.decision, ArbitrationDecision.LATENCY_SKEW_UNVERIFIED)
        self.assertEqual(res.consensus_price, 100.02)
        self.assertFalse(res.is_cross_verified)

    def test_divergence_exactly_at_tolerance_is_consensus(self):
        arb = MarketDataFeedArbitrator(max_divergence_pct=0.05)
        # Midpoint 100.00 => a 0.05% divergence is a 0.05 absolute spread.
        arb.process_vendor_tick("primary", "AAPL", 99.975, timestamp=T0)
        res = arb.process_vendor_tick("secondary", "AAPL", 100.025, timestamp=T0)

        self.assertAlmostEqual(res.relative_divergence_pct, 0.05, places=9)
        self.assertEqual(res.decision, ArbitrationDecision.CONSENSUS)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator()

    def test_nan_price_is_rejected_before_entering_state(self):
        # Regression: NaN fails every comparison, so it used to fall through to the
        # divergence branch and be published as a tradeable price.
        with self.assertRaises(ValueError):
            self.arbitrator.process_vendor_tick("primary", "AAPL", float("nan"), timestamp=T0)
        self.assertEqual(self.arbitrator.snapshot(), [])

    def test_infinite_and_non_positive_prices_are_rejected(self):
        for bad in (float("inf"), float("-inf"), 0.0, -1.0):
            with self.assertRaises(ValueError, msg=f"price {bad} should be rejected"):
                self.arbitrator.process_vendor_tick("primary", "AAPL", bad, timestamp=T0)

    def test_zero_sum_prices_cannot_reach_the_divergence_denominator(self):
        # The midpoint denominator vanishes for a +P/-P pair; validation forbids it.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        with self.assertRaises(ValueError):
            self.arbitrator.process_vendor_tick("secondary", "AAPL", -100.00, timestamp=T0)

    def test_unknown_vendor_raises_instead_of_defaulting(self):
        with self.assertRaises(ValueError):
            self.arbitrator.process_vendor_tick("bloomberg", "AAPL", 100.00, timestamp=T0)

    def test_vendor_id_is_case_and_whitespace_insensitive(self):
        res = self.arbitrator.process_vendor_tick("  PRIMARY ", "aapl", 100.00, timestamp=T0)
        self.assertEqual(res.symbol, "AAPL")
        self.assertEqual(res.active_vendor, "primary")

    def test_empty_symbol_raises(self):
        with self.assertRaises(ValueError):
            self.arbitrator.process_vendor_tick("primary", "   ", 100.00, timestamp=T0)

    def test_non_finite_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=float("nan"))

    def test_invalid_configuration_raises(self):
        for kwargs in (
            {"max_divergence_pct": 0.0},
            {"max_stale_seconds": -1.0},
            {"recovery_consecutive_ticks": 0},
            {"reference_vendor": "tertiary"},
            {"frozen_price_seconds": math.inf},
        ):
            with self.assertRaises(ValueError, msg=f"config {kwargs} should be rejected"):
                MarketDataFeedArbitrator(**kwargs)


class TestStateHygiene(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator(max_stale_seconds=2.0)

    def test_out_of_order_tick_does_not_rewind_feed_age(self):
        # A replayed tick must not un-stale a feed that has actually died.
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 10.0)

        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.00, timestamp=T0 + 1.0)

        self.assertEqual(res.decision, ArbitrationDecision.TICK_REJECTED_OUT_OF_ORDER)
        self.assertEqual(self.arbitrator.snapshot()[0][3], T0 + 10.0)

    def test_symbols_are_isolated_from_one_another(self):
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        res = self.arbitrator.process_vendor_tick("primary", "MSFT", 400.00, timestamp=T0)

        self.assertEqual(res.symbol, "MSFT")
        self.assertEqual(res.decision, ArbitrationDecision.SINGLE_FEED)
        self.assertEqual(len(self.arbitrator.snapshot()), 2)

    def test_alternating_decisions_do_not_produce_one_log_line_per_tick(self):
        # A divergence sitting on the tolerance alternates CONSENSUS/UNRESOLVED every
        # tick, and every one of those is a transition. Transition-only logging alone
        # would emit a line per tick in exactly the fast market where logs matter.
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05, max_stale_seconds=2.0, log_throttle_seconds=1.0
        )
        with self.assertLogs("feed_arbitrator", level="INFO") as captured:
            for i in range(200):
                t = T0 + i * 0.01
                arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=t)
                # Alternate between agreeing and breaching the 0.05% tolerance.
                counterpart = 100.00 if i % 2 == 0 else 100.20
                arb.process_vendor_tick("secondary", "AAPL", counterpart, timestamp=t)

        # 400 ticks spanning 2.0s of simulated time must not yield hundreds of records.
        self.assertLess(len(captured.records), 10, captured.output[:5])

    def test_escalation_bypasses_the_log_throttle(self):
        arb = MarketDataFeedArbitrator(
            max_divergence_pct=0.05,
            max_stale_seconds=2.0,
            divergence_confirmation_seconds=0.0,
            log_throttle_seconds=60.0,
        )
        with self.assertLogs("feed_arbitrator", level="INFO") as captured:
            arb.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
            arb.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=T0 + 0.01)

        self.assertTrue(
            any("quarantined" in record.getMessage().lower() for record in captured.records),
            captured.output,
        )

    def test_reset_clears_session_state(self):
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=T0)
        self.arbitrator.reset("AAPL")
        self.assertEqual(self.arbitrator.snapshot(), [])


if __name__ == "__main__":
    unittest.main()
