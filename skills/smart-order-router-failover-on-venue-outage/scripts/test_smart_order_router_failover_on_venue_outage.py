import threading
import time
import unittest

from smart_order_router_failover_on_venue_outage import (
    Config, Engine,
    NoEligibleVenueError,
    SmartOrderRouterFailoverEngine, TradingVenue, VenueHealthState,
    SORRoutingResult
)


class TestEngineLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = Engine(Config(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())


class TestSmartOrderRouterFailover(unittest.TestCase):

    def setUp(self):
        # Asks: NASDAQ 100.05 < BATS 100.08 < NYSE 100.10
        # Bids: BATS 100.02 > NASDAQ 100.00 > NYSE  99.95
        # The best BUY venue and the best SELL venue are deliberately different,
        # so a side bug cannot pass both directional tests.
        self.v1 = TradingVenue("NASDAQ", "Nasdaq Stock Market", ask_price=100.05, bid_price=100.00, available_qty=1000.0, max_error_threshold=3)
        self.v2 = TradingVenue("NYSE", "New York Stock Exchange", ask_price=100.10, bid_price=99.95, available_qty=2000.0, max_error_threshold=3)
        self.v3 = TradingVenue("BATS", "Cboe BATS Exchange", ask_price=100.08, bid_price=100.02, available_qty=1500.0, max_error_threshold=3)

        self.sor = SmartOrderRouterFailoverEngine(venues=[self.v1, self.v2, self.v3])

    def _trip(self, venue_id, times=3):
        for i in range(times):
            self.sor.report_venue_error(venue_id, f"FIX Timeout {i + 1}")

    # ------------------------------------------------------------- baseline

    def test_normal_best_execution_routing(self):
        # Buy 500 shares -> Should select NASDAQ (lowest ask price 100.05)
        res = self.sor.route_order("ORD_001", "BUY", 500.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertEqual(res.routed_price, 100.05)
        self.assertFalse(res.is_failover_triggered)
        self.assertEqual(res.unrouted_quantity, 0.0)
        self.assertEqual(res.excluded_venues, {})
        self.assertEqual(res.price_improvement_forgone, 0.0)

    def test_sell_side_selects_highest_bid(self):
        # Highest bid is BATS at 100.02, independently of the best BUY venue.
        res = self.sor.route_order("ORD_SELL", "SELL", 500.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertEqual(res.routed_price, 100.02)
        self.assertFalse(res.is_failover_triggered)

    def test_circuit_breaker_and_automatic_failover(self):
        # Simulate 3 errors on preferred NASDAQ venue -> Circuit breaker trips!
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 1")
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 2")
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 3")

        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)

        # Attempt to route to preferred NASDAQ -> Automatically fails over to BATS (ask 100.08 < NYSE 100.10)
        res = self.sor.route_order("ORD_002", "BUY", 500.0, preferred_venue_id="NASDAQ")
        self.assertTrue(res.is_failover_triggered)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertIn("NASDAQ", res.fallback_venues_used)
        self.assertEqual(res.routed_price, 100.08)

    def test_all_venues_outage_raises_runtime_error(self):
        # Trip circuit breakers on all 3 venues
        for _ in range(3):
            self.sor.report_venue_error("NASDAQ", "Down")
            self.sor.report_venue_error("NYSE", "Down")
            self.sor.report_venue_error("BATS", "Down")

        with self.assertRaises(RuntimeError) as ctx:
            self.sor.route_order("ORD_003", "BUY", 100.0)
        self.assertIn("All venues are in CIRCUIT_BROKEN_OUTAGE state", str(ctx.exception))

    def test_all_venues_down_error_carries_reasons_and_is_runtime_error(self):
        self._trip("NASDAQ")
        self._trip("NYSE")
        self._trip("BATS")
        with self.assertRaises(NoEligibleVenueError) as ctx:
            self.sor.route_order("ORD_004", "BUY", 100.0)
        self.assertTrue(issubclass(NoEligibleVenueError, RuntimeError))
        self.assertEqual(
            set(ctx.exception.excluded_venues),
            {"NASDAQ", "NYSE", "BATS"},
        )
        self.assertTrue(ctx.exception.suspected_local_fault)

    # ------------------------------------- G1: invalid / unquoted venue price

    def test_unquoted_venue_never_wins_buy_selection(self):
        """Regression: an ask of 0.0 used to win min(ask) and route at $0.00."""
        dark = TradingVenue("EDGX", "Cboe EDGX", available_qty=5000.0)  # ask/bid default 0.0
        self.sor.add_venue(dark)

        res = self.sor.route_order("ORD_005", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertEqual(res.routed_price, 100.05)
        self.assertIn("EDGX", res.excluded_venues)
        self.assertIn("INVALID_QUOTE", res.excluded_venues["EDGX"])

    def test_nan_price_is_excluded(self):
        self.v1.ask_price = float("nan")
        res = self.sor.route_order("ORD_006", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertIn("INVALID_QUOTE", res.excluded_venues["NASDAQ"])

    def test_zero_liquidity_venue_is_excluded(self):
        self.v1.available_qty = 0.0
        res = self.sor.route_order("ORD_007", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertIn("NO_LIQUIDITY", res.excluded_venues["NASDAQ"])

    # -------------------------------------------------- G2: quote staleness

    def test_stale_quote_excludes_venue_from_price_leadership(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3], max_quote_age_seconds=0.5
        )
        now = time.monotonic()
        # NASDAQ's quote is 5s old; the other two are current.
        sor.update_quote("NASDAQ", 100.00, 100.05, 1000.0, monotonic_ts=now - 5.0)
        sor.update_quote("NYSE", 99.95, 100.10, 2000.0, monotonic_ts=now)
        sor.update_quote("BATS", 100.02, 100.08, 1500.0, monotonic_ts=now)

        res = sor.route_order("ORD_008", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertIn("STALE_QUOTE", res.excluded_venues["NASDAQ"])
        # A stale venue that was quoting better is a bypassed venue for audit.
        self.assertIn("NASDAQ", res.fallback_venues_used)
        self.assertTrue(res.is_failover_triggered)

    def test_fresh_quote_is_not_stale(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3], max_quote_age_seconds=5.0
        )
        sor.update_quote("NASDAQ", 100.00, 100.05, 1000.0)
        res = sor.route_order("ORD_009", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertEqual(res.excluded_venues, {})

    def test_missing_timestamp_is_surfaced_not_silently_trusted(self):
        res = self.sor.route_order("ORD_010", "BUY", 100.0)
        self.assertEqual(
            set(res.stale_quote_check_skipped), {"NASDAQ", "NYSE", "BATS"}
        )

    def test_require_quote_timestamp_excludes_undated_venues(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3], require_quote_timestamp=True
        )
        sor.update_quote("NYSE", 99.95, 100.10, 2000.0)
        res = sor.route_order("ORD_011", "BUY", 100.0)
        # Only NYSE carries a timestamp, so only NYSE is routable.
        self.assertEqual(res.target_venue_id, "NYSE")
        self.assertIn("QUOTE_TIMESTAMP_MISSING", res.excluded_venues["NASDAQ"])
        self.assertIn("QUOTE_TIMESTAMP_MISSING", res.excluded_venues["BATS"])

    # ------------------------------------------ G3: breaker recovery lifecycle

    def test_success_does_not_reopen_a_tripped_venue(self):
        """Regression: a late ack used to clear the breaker instantly."""
        self._trip("NASDAQ")
        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)

        self.sor.report_venue_success("NASDAQ")

        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)
        res = self.sor.route_order("ORD_012", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")

    def test_success_resets_a_degraded_venue(self):
        self.sor.report_venue_error("NASDAQ", "one timeout")
        self.assertEqual(self.v1.state, VenueHealthState.DEGRADED)
        self.sor.report_venue_success("NASDAQ")
        self.assertEqual(self.v1.state, VenueHealthState.HEALTHY)
        self.assertEqual(self.v1.consecutive_error_count, 0)

    def test_cooldown_elapses_to_recovery_probe_then_closes_on_success(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3], cooldown_seconds=0.01
        )
        for i in range(3):
            sor.report_venue_error("NASDAQ", f"err {i}")
        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)

        time.sleep(0.02)
        sor.refresh_venue_states()
        self.assertEqual(self.v1.state, VenueHealthState.RECOVERY_PROBE)

        sor.report_venue_success("NASDAQ")
        self.assertEqual(self.v1.state, VenueHealthState.HEALTHY)
        self.assertEqual(self.v1.consecutive_trips, 0)

    def test_failed_probe_reopens_immediately_with_escalated_cooldown(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3],
            cooldown_seconds=0.01,
            backoff_multiplier=2.0,
        )
        for i in range(3):
            sor.report_venue_error("NASDAQ", f"err {i}")
        self.assertEqual(self.v1.consecutive_trips, 1)

        time.sleep(0.02)
        sor.refresh_venue_states()
        self.assertEqual(self.v1.state, VenueHealthState.RECOVERY_PROBE)

        # A single failure during the probe re-opens, without needing 3 more.
        sor.report_venue_error("NASDAQ", "probe failed")
        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)
        self.assertEqual(self.v1.consecutive_trips, 2)
        self.assertAlmostEqual(sor._cooldown_for(self.v1), 0.02)

    def test_cooldown_backoff_is_capped(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1],
            cooldown_seconds=10.0,
            backoff_multiplier=10.0,
            max_cooldown_seconds=50.0,
        )
        self.v1.consecutive_trips = 6
        self.assertEqual(sor._cooldown_for(self.v1), 50.0)

    def test_recovery_probe_venue_ranks_last_despite_best_price(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1, self.v2, self.v3], cooldown_seconds=0.01
        )
        for i in range(3):
            sor.report_venue_error("NASDAQ", f"err {i}")
        time.sleep(0.02)
        sor.refresh_venue_states()
        self.assertEqual(self.v1.state, VenueHealthState.RECOVERY_PROBE)

        # NASDAQ still shows the best ask (100.05) but must not be chosen.
        res = sor.route_order("ORD_013", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")

    # ---------------------------------------------- G4: failover audit record

    def test_failover_recorded_without_a_preferred_venue(self):
        """Regression: bypassing the price leader used to report failover=False."""
        self._trip("NASDAQ")
        res = self.sor.route_order("ORD_014", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertTrue(res.is_failover_triggered)
        self.assertEqual(res.fallback_venues_used, ["NASDAQ"])

    def test_bypassed_list_excludes_worse_priced_dead_venues(self):
        # NYSE (ask 100.10) is dead but was already worse than the BATS fill.
        self._trip("NYSE")
        self._trip("NASDAQ")
        res = self.sor.route_order("ORD_015", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertEqual(res.fallback_venues_used, ["NASDAQ"])
        self.assertIn("NYSE", res.excluded_venues)

    def test_unavailable_preferred_venue_is_recorded_even_when_worse_priced(self):
        # NYSE (ask 100.10) is the worst venue, so it is not "price competitive"
        # against the NASDAQ fill -- but the caller asked for it and did not get
        # it, so this is still a failover and must be recorded.
        self._trip("NYSE")
        res = self.sor.route_order("ORD_029", "BUY", 100.0, preferred_venue_id="NYSE")
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertTrue(res.is_failover_triggered)
        self.assertIn("NYSE", res.fallback_venues_used)

    def test_quote_timestamp_from_wrong_clock_is_rejected(self):
        # time.time() is orders of magnitude larger than time.monotonic() on a
        # long-lived host, giving a hugely negative age that would otherwise read
        # as "permanently fresh" and disable staleness checking entirely.
        sor = SmartOrderRouterFailoverEngine(venues=[self.v1, self.v2, self.v3])
        sor.update_quote("NASDAQ", 100.00, 100.05, 1000.0, monotonic_ts=time.monotonic() + 3600.0)
        res = sor.route_order("ORD_030", "BUY", 100.0)
        self.assertIn("QUOTE_TIMESTAMP_IN_FUTURE", res.excluded_venues["NASDAQ"])
        self.assertEqual(res.target_venue_id, "BATS")

    def test_extreme_trip_count_saturates_instead_of_overflowing(self):
        sor = SmartOrderRouterFailoverEngine(
            venues=[self.v1], cooldown_seconds=1.0,
            backoff_multiplier=2.0, max_cooldown_seconds=600.0,
        )
        self.v1.consecutive_trips = 100_000
        self.assertEqual(sor._cooldown_for(self.v1), 600.0)

    # ------------------------------------------------- G5: residual quantity

    def test_residual_quantity_is_reported_not_silently_dropped(self):
        """Regression: quantity above venue depth used to vanish from the result."""
        res = self.sor.route_order("ORD_016", "BUY", 2500.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertEqual(res.routed_quantity, 1000.0)
        self.assertEqual(res.unrouted_quantity, 1500.0)
        self.assertIn("unrouted=1500.0", res.audit_notes)

    def test_full_size_order_reports_no_residual(self):
        res = self.sor.route_order("ORD_017", "BUY", 1000.0)
        self.assertEqual(res.routed_quantity, 1000.0)
        self.assertEqual(res.unrouted_quantity, 0.0)

    # -------------------------------------------------- G6: local-fault check

    def test_local_fault_suspected_when_majority_of_venues_trip(self):
        self.assertFalse(self.sor.diagnose_suspected_local_fault())
        self._trip("NASDAQ")
        self.assertFalse(self.sor.diagnose_suspected_local_fault())
        self._trip("NYSE")
        self.assertTrue(self.sor.diagnose_suspected_local_fault())

        res = self.sor.route_order("ORD_018", "BUY", 100.0)
        self.assertTrue(res.suspected_local_fault)
        self.assertEqual(res.target_venue_id, "BATS")

    def test_single_venue_engine_never_suspects_local_fault(self):
        sor = SmartOrderRouterFailoverEngine(venues=[self.v1])
        for i in range(3):
            sor.report_venue_error("NASDAQ", f"err {i}")
        self.assertFalse(sor.diagnose_suspected_local_fault())

    # ------------------------------------------------- G7/G8: input validation

    def test_invalid_side_raises_rather_than_selling(self):
        """Regression: any string other than 'BUY' used to route as a SELL."""
        for bad in ("SEL", "buy_", "", "SHORT"):
            with self.assertRaises(ValueError):
                self.sor.route_order("ORD_019", bad, 100.0)

    def test_lowercase_side_is_accepted(self):
        res = self.sor.route_order("ORD_020", "buy", 100.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")

    def test_invalid_quantity_raises(self):
        for bad in (0.0, -100.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.sor.route_order("ORD_021", "BUY", bad)
        with self.assertRaises(ValueError):
            self.sor.route_order("ORD_021", "BUY", "500")

    def test_unknown_venue_error_report_raises(self):
        """Regression: a typo'd venue id used to be silently discarded."""
        with self.assertRaises(KeyError):
            self.sor.report_venue_error("NASDQ", "typo")
        with self.assertRaises(KeyError):
            self.sor.report_venue_success("NASDQ")
        with self.assertRaises(KeyError):
            self.sor.route_order("ORD_022", "BUY", 100.0, preferred_venue_id="NASDQ")

    def test_duplicate_venue_registration_rejected(self):
        with self.assertRaises(ValueError):
            self.sor.add_venue(TradingVenue("NASDAQ", "duplicate"))

    def test_invalid_venue_and_engine_construction_rejected(self):
        with self.assertRaises(ValueError):
            TradingVenue("", "no id")
        with self.assertRaises(ValueError):
            TradingVenue("X", "bad threshold", max_error_threshold=0)
        with self.assertRaises(ValueError):
            SmartOrderRouterFailoverEngine(cooldown_seconds=0.0)
        with self.assertRaises(ValueError):
            SmartOrderRouterFailoverEngine(local_fault_threshold_ratio=0.0)
        with self.assertRaises(ValueError):
            SmartOrderRouterFailoverEngine(cooldown_seconds=60.0, max_cooldown_seconds=10.0)

    def test_empty_engine_raises_no_eligible_venue(self):
        with self.assertRaises(NoEligibleVenueError):
            SmartOrderRouterFailoverEngine().route_order("ORD_023", "BUY", 100.0)

    # ----------------------------------------- G14: preferred-venue best exec

    def test_preferred_venue_override_records_forgone_price_improvement(self):
        # NYSE ask 100.10 vs best eligible NASDAQ 100.05 -> 0.05/share given up.
        res = self.sor.route_order("ORD_024", "BUY", 100.0, preferred_venue_id="NYSE")
        self.assertEqual(res.target_venue_id, "NYSE")
        self.assertTrue(res.is_failover_triggered)
        self.assertAlmostEqual(res.price_improvement_forgone, 0.05)

    def test_preferred_venue_that_is_already_best_forgoes_nothing(self):
        res = self.sor.route_order("ORD_025", "BUY", 100.0, preferred_venue_id="NASDAQ")
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertFalse(res.is_failover_triggered)
        self.assertEqual(res.price_improvement_forgone, 0.0)

    # ------------------------------------------------------ ranking tie-breaks

    def test_better_priced_degraded_venue_beats_healthy_worse_price(self):
        self.sor.report_venue_error("NASDAQ", "one timeout")
        self.assertEqual(self.v1.state, VenueHealthState.DEGRADED)
        # Skipping NASDAQ at 100.05 for BATS at 100.08 would be a trade-through
        # taken to avoid a venue with a single timeout.
        res = self.sor.route_order("ORD_026", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")

    def test_healthy_venue_wins_tie_against_degraded_at_same_price(self):
        self.v3.ask_price = 100.05
        self.v3.latency_ms = self.v1.latency_ms
        self.sor.report_venue_error("NASDAQ", "one timeout")
        res = self.sor.route_order("ORD_027", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")

    def test_lower_latency_wins_tie_at_same_price_and_health(self):
        self.v3.ask_price = 100.05
        self.v1.latency_ms = 9.0
        self.v3.latency_ms = 1.0
        res = self.sor.route_order("ORD_028", "BUY", 100.0)
        self.assertEqual(res.target_venue_id, "BATS")

    # --------------------------------------------------------- G9: concurrency

    def test_concurrent_health_reports_do_not_corrupt_routing(self):
        errors = []

        def report():
            try:
                for _ in range(200):
                    self.sor.report_venue_error("NYSE", "churn")
                    self.sor.report_venue_success("NYSE")
            except Exception as exc:            # pragma: no cover - failure path
                errors.append(exc)

        def route():
            try:
                for _ in range(200):
                    self.sor.route_order("ORD_C", "BUY", 10.0)
            except Exception as exc:            # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=report), threading.Thread(target=route)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_result_is_dataclass_with_backward_compatible_positional_fields(self):
        res = SORRoutingResult("id", "NASDAQ", 1.0, 100.0, False, [], "notes")
        self.assertEqual(res.unrouted_quantity, 0.0)
        self.assertEqual(res.excluded_venues, {})
        self.assertFalse(res.suspected_local_fault)


if __name__ == '__main__':
    unittest.main()
