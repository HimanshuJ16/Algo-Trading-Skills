"""Behavioural tests for the vendor-outage fallback hierarchy engine.

All time-dependent behaviour is driven by an injected fake monotonic clock rather than
``time.sleep``, so staleness and promotion-window boundaries are asserted exactly and
the suite runs in milliseconds.

``TestRegressions`` holds the cases that fail against a naive implementation:

  * a synthetic tick stamped with "now", hiding the age of a cached price;
  * the anti-flap hold pinning routing to a source measured stale, while a healthy
    higher-priority source sat idle;
  * a NaN price accepted, returned and cached, poisoning the fallback for the outage;
  * promotion granted on a single heartbeat after a long outage;
  * a duplicate ``source_id`` silently replacing a registered vendor;
  * a timezone-aware heartbeat raising ``TypeError``.
"""

import datetime
import logging
import threading
import unittest

from fallback_datasource_hierarchy import (
    DataSourceNode,
    DataSourceStatus,
    EngineState,
    FailoverEventKind,
    FallbackEngineError,
    SYNTHETIC_SOURCE_ID,
    VendorFallbackHierarchyEngine,
)

logging.disable(logging.CRITICAL)


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


PRICES = {"FEED-BPIPE": 150.00, "FEED-LSEG": 150.10, "FEED-POLYGON": 150.20}


def quote_for(_symbol: str, source_id: str):
    return PRICES[source_id], 1000.0


class HierarchyTestCase(unittest.TestCase):
    """Three-deep hierarchy, all feeds beating, promotion window of 30s."""

    def setUp(self):
        self.clock = FakeClock()
        self.engine = VendorFallbackHierarchyEngine(
            recovery_cooling_seconds=30.0,
            max_synthetic_age_seconds=60.0,
            clock=self.clock,
        )
        self.primary = DataSourceNode(
            source_id="FEED-BPIPE",
            name="Bloomberg B-PIPE",
            priority=1,
            max_staleness_seconds=2.0,
            max_error_threshold=2,
        )
        self.secondary = DataSourceNode(
            source_id="FEED-LSEG",
            name="LSEG Real-Time",
            priority=2,
            max_staleness_seconds=5.0,
            max_error_threshold=3,
        )
        self.tertiary = DataSourceNode(
            source_id="FEED-POLYGON",
            name="Polygon.io",
            priority=3,
            max_staleness_seconds=10.0,
            max_error_threshold=5,
        )
        for node in (self.primary, self.secondary, self.tertiary):
            self.engine.register_data_source(node)

    def beat_all(self):
        for node in (self.primary, self.secondary, self.tertiary):
            self.engine.record_heartbeat(node.source_id)


class TestRegistrationAndConfiguration(unittest.TestCase):
    def test_registered_source_is_disconnected_until_first_heartbeat(self):
        engine = VendorFallbackHierarchyEngine(clock=FakeClock())
        engine.register_data_source(DataSourceNode("P", "Primary", 1))
        self.assertEqual(engine.data_sources["P"].status, DataSourceStatus.DISCONNECTED)
        self.assertEqual(engine.engine_state, EngineState.ALL_SOURCES_DOWN)
        with self.assertRaises(FallbackEngineError):
            engine.fetch_market_data_tick("AAPL", lambda s, src: (10.0, 1.0))

    def test_first_heartbeat_activates_the_source(self):
        engine = VendorFallbackHierarchyEngine(clock=FakeClock())
        engine.register_data_source(DataSourceNode("P", "Primary", 1))
        engine.record_heartbeat("P")
        self.assertEqual(engine.active_source_id, "P")
        self.assertEqual(engine.engine_state, EngineState.PRIMARY_ACTIVE)

    def test_initial_selection_is_not_recorded_as_a_failover(self):
        engine = VendorFallbackHierarchyEngine(clock=FakeClock())
        engine.register_data_source(DataSourceNode("P", "Primary", 1))
        engine.record_heartbeat("P")
        kinds = [e.kind for e in engine.event_log]
        self.assertIn(FailoverEventKind.INITIAL_SELECTION, kinds)
        self.assertNotIn(FailoverEventKind.FAILOVER, kinds)
        self.assertIsNone(engine.last_failover_utc)

    def test_duplicate_source_id_is_refused(self):
        engine = VendorFallbackHierarchyEngine(clock=FakeClock())
        engine.register_data_source(DataSourceNode("X", "Bloomberg", 1))
        with self.assertRaises(ValueError):
            engine.register_data_source(DataSourceNode("X", "Polygon.io", 3))
        self.assertEqual(engine.data_sources["X"].name, "Bloomberg")

    def test_reserved_synthetic_id_cannot_be_registered(self):
        with self.assertRaises(ValueError):
            DataSourceNode(SYNTHETIC_SOURCE_ID, "Fake", 1)

    def test_invalid_node_configuration_raises(self):
        with self.assertRaises(ValueError):
            DataSourceNode("", "No id", 1)
        with self.assertRaises(ValueError):
            DataSourceNode("A", "Zero priority", 0)
        with self.assertRaises(ValueError):
            DataSourceNode("A", "Bool priority", True)
        with self.assertRaises(ValueError):
            DataSourceNode("A", "Negative staleness", 1, max_staleness_seconds=-1.0)
        with self.assertRaises(ValueError):
            DataSourceNode("A", "NaN staleness", 1, max_staleness_seconds=float("nan"))
        with self.assertRaises(ValueError):
            DataSourceNode("A", "Zero error budget", 1, max_error_threshold=0)

    def test_invalid_engine_configuration_raises(self):
        with self.assertRaises(ValueError):
            VendorFallbackHierarchyEngine(recovery_cooling_seconds=-1.0)
        with self.assertRaises(ValueError):
            VendorFallbackHierarchyEngine(max_synthetic_age_seconds=float("inf"))
        with self.assertRaises(ValueError):
            VendorFallbackHierarchyEngine(max_event_log_entries=0)
        with self.assertRaises(ValueError):
            VendorFallbackHierarchyEngine(clock="not callable")

    def test_deregistering_the_active_source_reroutes(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(recovery_cooling_seconds=0.0, clock=clock)
        engine.register_data_source(DataSourceNode("P", "Primary", 1))
        engine.register_data_source(DataSourceNode("S", "Secondary", 2))
        engine.record_heartbeat("P")
        engine.record_heartbeat("S")
        self.assertEqual(engine.active_source_id, "P")
        engine.deregister_data_source("P")
        self.assertEqual(engine.active_source_id, "S")
        with self.assertRaises(FallbackEngineError):
            engine.deregister_data_source("P")


class TestHealthEvaluation(HierarchyTestCase):
    def test_healthy_primary_is_selected(self):
        self.beat_all()
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertEqual(tick.source_id, "FEED-BPIPE")
        self.assertEqual(tick.price, 150.00)
        self.assertFalse(tick.is_synthetic)
        self.assertEqual(tick.age_seconds, 0.0)
        self.assertEqual(self.engine.engine_state, EngineState.PRIMARY_ACTIVE)

    def test_staleness_boundary_is_exclusive(self):
        self.beat_all()
        # max_staleness_seconds is 2.0 for the primary; the condition is `>`.
        self.clock.advance(2.0)
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.primary.status, DataSourceStatus.HEALTHY)
        self.clock.advance(0.001)
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.primary.status, DataSourceStatus.STALE)

    def test_stale_primary_fails_over_to_secondary(self):
        self.beat_all()
        self.clock.advance(3.0)  # past primary's 2.0s, inside secondary's 5.0s
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertEqual(tick.source_id, "FEED-LSEG")
        self.assertEqual(self.engine.engine_state, EngineState.FAILOVER_ACTIVE)
        self.assertEqual(
            [e.kind for e in self.engine.event_log][-1], FailoverEventKind.FAILOVER
        )

    def test_error_budget_exhaustion_degrades_and_fails_over(self):
        self.beat_all()
        self.engine.record_error("FEED-BPIPE", "connection timeout")
        self.assertEqual(self.primary.status, DataSourceStatus.HEALTHY)
        self.engine.record_error("FEED-BPIPE", "socket closed")
        self.assertEqual(self.primary.status, DataSourceStatus.ERROR)
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertEqual(tick.source_id, "FEED-LSEG")

    def test_explicit_disconnect_is_immediate_and_recoverable(self):
        self.beat_all()
        self.engine.mark_disconnected("FEED-BPIPE", "FIX logout")
        self.assertEqual(self.primary.status, DataSourceStatus.DISCONNECTED)
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")
        # A heartbeat proves connectivity, so DISCONNECTED is not terminal.
        self.engine.record_heartbeat("FEED-BPIPE")
        self.assertEqual(self.primary.status, DataSourceStatus.HEALTHY)

    def test_inactive_source_is_never_selected(self):
        self.beat_all()
        self.primary.is_active = False
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.primary.status, DataSourceStatus.DISCONNECTED)
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

    def test_every_node_status_is_refreshed_each_pass(self):
        self.beat_all()
        # Kill the two lower tiers while the primary stays healthy. A pass that stopped
        # at the first healthy node would leave their status reading HEALTHY.
        self.clock.advance(11.0)
        self.engine.record_heartbeat("FEED-BPIPE")
        self.assertEqual(self.primary.status, DataSourceStatus.HEALTHY)
        self.assertEqual(self.secondary.status, DataSourceStatus.STALE)
        self.assertEqual(self.tertiary.status, DataSourceStatus.STALE)

    def test_priority_ties_are_broken_deterministically(self):
        engine = VendorFallbackHierarchyEngine(clock=FakeClock())
        engine.register_data_source(DataSourceNode("ZZZ", "Later id", 2))
        engine.register_data_source(DataSourceNode("AAA", "Earlier id", 2))
        self.assertEqual(
            [n.source_id for n in engine.get_prioritized_sources()], ["AAA", "ZZZ"]
        )


class TestPromotionWindow(HierarchyTestCase):
    """The window is 30.0s and both feeds must keep beating throughout it.

    The assertions are keyed to ``healthy_since_monotonic`` rather than to a loop count,
    so they pin the boundary itself: not promoted at window - 1s, promoted at exactly
    the window.
    """

    def _beat_both(self, seconds: float = 1.0):
        self.clock.advance(seconds)
        self.engine.record_heartbeat("FEED-BPIPE")
        self.engine.record_heartbeat("FEED-LSEG")

    def _fail_primary_over_to_secondary(self):
        self.beat_all()
        self.clock.advance(3.0)  # past primary's 2.0s, inside secondary's 5.0s
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

    def test_failover_is_immediate_but_promotion_waits(self):
        self._fail_primary_over_to_secondary()

        self._beat_both()  # primary reads healthy again; the window opens here
        opened_at = self.primary.healthy_since_monotonic
        self.assertIsNotNone(opened_at)

        while self.clock.now - opened_at < 29.0:
            self.assertEqual(self.engine.active_source_id, "FEED-LSEG")
            self._beat_both()
        self.assertAlmostEqual(self.clock.now - opened_at, 29.0, places=9)
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

        self._beat_both()  # 30.0s of unbroken health
        self.assertAlmostEqual(self.clock.now - opened_at, 30.0, places=9)
        self.assertEqual(self.engine.active_source_id, "FEED-BPIPE")
        self.assertEqual(self.engine.engine_state, EngineState.PRIMARY_ACTIVE)
        self.assertEqual(
            [e.kind for e in self.engine.event_log][-1], FailoverEventKind.RESTORE
        )

    def test_an_interruption_restarts_the_promotion_window(self):
        self._fail_primary_over_to_secondary()

        self._beat_both()
        first_open = self.primary.healthy_since_monotonic
        while self.clock.now - first_open < 25.0:  # 25s of the 30s window served
            self._beat_both()

        self.clock.advance(3.0)  # primary misses its window and goes stale
        self.engine.record_heartbeat("FEED-LSEG")
        self.assertEqual(self.primary.status, DataSourceStatus.STALE)
        self.assertIsNone(self.primary.healthy_since_monotonic)

        self._beat_both()
        second_open = self.primary.healthy_since_monotonic
        self.assertGreater(second_open, first_open)

        # 29s of the *restarted* window: total elapsed health far exceeds 30s, so a rule
        # keyed on accumulated or elapsed time would promote here. A stability rule must
        # not, because the run was broken.
        while self.clock.now - second_open < 29.0:
            self.assertEqual(self.engine.active_source_id, "FEED-LSEG")
            self._beat_both()
        self.assertGreater(self.clock.now - first_open, 55.0)
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

        self._beat_both()
        self.assertAlmostEqual(self.clock.now - second_open, 30.0, places=9)
        self.assertEqual(self.engine.active_source_id, "FEED-BPIPE")

    def test_zero_window_promotes_immediately(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(recovery_cooling_seconds=0.0, clock=clock)
        engine.register_data_source(
            DataSourceNode("P", "Primary", 1, max_staleness_seconds=2.0)
        )
        engine.register_data_source(
            DataSourceNode("S", "Secondary", 2, max_staleness_seconds=5.0)
        )
        engine.record_heartbeat("P")
        engine.record_heartbeat("S")
        clock.advance(3.0)
        engine.record_heartbeat("S")
        self.assertEqual(engine.active_source_id, "S")
        engine.record_heartbeat("P")
        self.assertEqual(engine.active_source_id, "P")

    def test_equal_priority_peers_do_not_swap_without_serving_the_window(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(recovery_cooling_seconds=30.0, clock=clock)
        engine.register_data_source(
            DataSourceNode("AAA", "Peer A", 2, max_staleness_seconds=5.0)
        )
        engine.register_data_source(
            DataSourceNode("BBB", "Peer B", 2, max_staleness_seconds=5.0)
        )
        engine.record_heartbeat("BBB")
        self.assertEqual(engine.active_source_id, "BBB")
        # Peer A comes up; it sorts first but must not take routing from a healthy peer.
        engine.record_heartbeat("AAA")
        self.assertEqual(engine.active_source_id, "BBB")
        for _ in range(30):
            clock.advance(1.0)
            engine.record_heartbeat("AAA")
            engine.record_heartbeat("BBB")
        self.assertEqual(engine.active_source_id, "AAA")


class TestSyntheticCache(HierarchyTestCase):
    def test_all_sources_down_serves_the_cache(self):
        self.beat_all()
        self.engine.fetch_market_data_tick("AAPL", quote_for)  # seed the cache
        self.clock.advance(11.0)  # past every tier's staleness limit
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertEqual(tick.source_id, SYNTHETIC_SOURCE_ID)
        self.assertTrue(tick.is_synthetic)
        self.assertEqual(tick.price, 150.00)
        self.assertEqual(self.engine.engine_state, EngineState.SYNTHETIC_CACHE_ACTIVE)

    def test_synthetic_tick_reports_the_true_age_of_the_price(self):
        self.beat_all()
        self.engine.fetch_market_data_tick("AAPL", quote_for)
        seeded_at = self.engine.synthetic_cache["AAPL"].observed_at_utc
        self.clock.advance(45.0)
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertTrue(tick.is_synthetic)
        self.assertAlmostEqual(tick.age_seconds, 45.0, places=6)
        # `timestamp` is the observation time, not the time the object was built.
        self.assertEqual(tick.timestamp, seeded_at)

    def test_cache_older_than_the_limit_is_refused(self):
        self.beat_all()
        self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.clock.advance(60.0)  # exactly at max_synthetic_age_seconds
        self.assertTrue(self.engine.fetch_market_data_tick("AAPL", quote_for).is_synthetic)
        self.clock.advance(0.001)
        with self.assertRaises(FallbackEngineError) as ctx:
            self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.assertIn("Refusing to serve an obsolete price", str(ctx.exception))

    def test_allow_synthetic_false_refuses_a_cached_price(self):
        self.beat_all()
        self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.clock.advance(11.0)
        with self.assertRaises(FallbackEngineError) as ctx:
            self.engine.fetch_market_data_tick(
                "AAPL", quote_for, allow_synthetic=False
            )
        self.assertIn("allow_synthetic=False", str(ctx.exception))

    def test_uncached_symbol_during_an_outage_raises(self):
        self.beat_all()
        self.clock.advance(11.0)
        with self.assertRaises(FallbackEngineError):
            self.engine.fetch_market_data_tick("MSFT", quote_for)

    def test_cache_is_only_seeded_from_valid_live_quotes(self):
        self.beat_all()
        with self.assertRaises(FallbackEngineError):
            self.engine.fetch_market_data_tick(
                "AAPL", lambda s, src: (float("nan"), 1.0)
            )
        self.assertNotIn("AAPL", self.engine.synthetic_cache)


class TestQuoteValidation(HierarchyTestCase):
    def _fetch_all_returning(self, value):
        self.beat_all()
        return self.engine.fetch_market_data_tick("AAPL", lambda s, src: value)

    def test_nan_price_is_rejected_from_every_source(self):
        with self.assertRaises(FallbackEngineError):
            self._fetch_all_returning((float("nan"), 1.0))
        for node in (self.primary, self.secondary, self.tertiary):
            self.assertGreater(node.error_count, 0)

    def test_infinite_and_negative_values_are_rejected(self):
        for bad in [
            (float("inf"), 1.0),
            (float("-inf"), 1.0),
            (150.0, float("nan")),
            (150.0, float("inf")),
            (150.0, -1.0),
            (0.0, 1.0),
            (-150.0, 1.0),
            ("150.0", 1.0),
            (150.0, "1000"),
            (True, 1.0),
            (150.0, True),
            (None, 1.0),
        ]:
            with self.subTest(quote=bad):
                self.setUp()
                with self.assertRaises(FallbackEngineError):
                    self._fetch_all_returning(bad)

    def test_malformed_return_shape_is_rejected(self):
        for bad in [None, 150.0, (150.0,), (150.0, 1.0, 2.0), "150", "ab",
                    {"price": 150.0, "volume": 1.0}, {150.0, 1000.0}]:
            with self.subTest(returned=bad):
                self.setUp()
                with self.assertRaises(FallbackEngineError):
                    self._fetch_all_returning(bad)

    def test_list_and_iterable_pairs_are_accepted(self):
        # The container is not the point; the shape and the values are. A vendor adapter
        # returning a list must not be rejected as malformed.
        for good in [[150.0, 1000.0], (150.0, 1000.0)]:
            with self.subTest(returned=good):
                self.setUp()
                tick = self._fetch_all_returning(good)
                self.assertEqual(tick.price, 150.0)
                self.assertEqual(tick.volume, 1000.0)
                self.assertFalse(tick.is_synthetic)

    def test_integer_quotes_are_accepted(self):
        tick = self._fetch_all_returning((150, 1000))
        self.assertEqual(tick.price, 150.0)
        self.assertIsInstance(tick.price, float)

    def test_non_positive_prices_allowed_when_configured(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(
            allow_non_positive_prices=True, clock=clock
        )
        engine.register_data_source(DataSourceNode("CL", "CME Globex", 1))
        engine.record_heartbeat("CL")
        # WTI May-2020 settled at -37.63/bbl; a hard price>0 filter would blank the feed.
        tick = engine.fetch_market_data_tick("CLK0", lambda s, src: (-37.63, 100.0))
        self.assertEqual(tick.price, -37.63)

    def test_a_raising_vendor_falls_through_to_the_next_tier(self):
        self.beat_all()
        calls = []

        def flaky(symbol, source_id):
            calls.append(source_id)
            if source_id == "FEED-BPIPE":
                raise ConnectionResetError("peer reset")
            return quote_for(symbol, source_id)

        tick = self.engine.fetch_market_data_tick("AAPL", flaky)
        self.assertEqual(tick.source_id, "FEED-LSEG")
        self.assertEqual(calls, ["FEED-BPIPE", "FEED-LSEG"])
        self.assertEqual(self.primary.error_count, 1)

    def test_each_source_is_attempted_at_most_once_per_call(self):
        # A large error budget must not turn the retry walk into an unbounded loop.
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(clock=clock)
        for i in range(3):
            engine.register_data_source(
                DataSourceNode(f"S{i}", f"Vendor {i}", i + 1, max_error_threshold=10_000)
            )
            engine.record_heartbeat(f"S{i}")
        calls = []

        def always_fails(symbol, source_id):
            calls.append(source_id)
            raise TimeoutError("gateway timeout")

        with self.assertRaises(FallbackEngineError):
            engine.fetch_market_data_tick("AAPL", always_fails)
        self.assertEqual(sorted(calls), ["S0", "S1", "S2"])

    def test_engine_errors_from_fetch_func_are_not_charged_to_the_vendor(self):
        self.beat_all()

        def raises_engine_error(symbol, source_id):
            raise FallbackEngineError("caller's own guard tripped")

        with self.assertRaises(FallbackEngineError):
            self.engine.fetch_market_data_tick("AAPL", raises_engine_error)
        self.assertEqual(self.primary.error_count, 0)

    def test_invalid_fetch_arguments_raise_value_error(self):
        self.beat_all()
        with self.assertRaises(ValueError):
            self.engine.fetch_market_data_tick("", quote_for)
        with self.assertRaises(ValueError):
            self.engine.fetch_market_data_tick("AAPL", "not callable")


class TestTimestampsAndAudit(HierarchyTestCase):
    def test_timezone_aware_heartbeat_is_accepted(self):
        aware = datetime.datetime.now(datetime.timezone.utc)
        self.engine.record_heartbeat("FEED-BPIPE", aware)
        self.assertEqual(self.primary.last_heartbeat_utc, aware)

    def test_naive_heartbeat_is_read_as_utc(self):
        naive = datetime.datetime(2026, 1, 2, 3, 4, 5)
        self.engine.record_heartbeat("FEED-BPIPE", naive)
        stored = self.primary.last_heartbeat_utc
        self.assertEqual(stored.tzinfo, datetime.timezone.utc)
        self.assertEqual(stored.replace(tzinfo=None), naive)

    def test_vendor_timestamp_does_not_drive_staleness(self):
        # A vendor stamp an hour behind local time must not make a live feed look stale;
        # that difference is clock skew between two machines, not data age.
        stale_stamp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=1
        )
        self.engine.record_heartbeat("FEED-BPIPE", stale_stamp)
        self.assertEqual(self.primary.status, DataSourceStatus.HEALTHY)

    def test_event_ids_are_unique_within_the_same_second(self):
        # Deliberately flapping (no promotion window) so many transitions land inside a
        # single wall-clock second, which a second-resolution event id cannot separate.
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(recovery_cooling_seconds=0.0, clock=clock)
        engine.register_data_source(
            DataSourceNode("P", "Primary", 1, max_staleness_seconds=2.0)
        )
        engine.register_data_source(
            DataSourceNode("S", "Secondary", 2, max_staleness_seconds=1e6)
        )
        engine.record_heartbeat("S")
        for _ in range(10):
            engine.record_heartbeat("P")  # promote to primary
            clock.advance(3.0)
            engine.evaluate_health_and_failover()  # primary stale -> back to secondary
        ids = [e.event_id for e in engine.event_log]
        self.assertGreater(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))

    def test_event_timestamps_are_timezone_aware(self):
        self.beat_all()
        for event in self.engine.event_log:
            self.assertIsNotNone(event.timestamp.tzinfo)

    def test_event_log_is_bounded(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(
            recovery_cooling_seconds=0.0, max_event_log_entries=5, clock=clock
        )
        engine.register_data_source(
            DataSourceNode("P", "Primary", 1, max_staleness_seconds=2.0)
        )
        engine.register_data_source(
            DataSourceNode("S", "Secondary", 2, max_staleness_seconds=90.0)
        )
        engine.record_heartbeat("S")
        for _ in range(20):
            engine.record_heartbeat("P")
            clock.advance(3.0)
            engine.evaluate_health_and_failover()
        self.assertLessEqual(len(engine.event_log), 5)

    def test_health_snapshot_reports_measured_state(self):
        self.beat_all()
        self.clock.advance(3.0)
        self.engine.evaluate_health_and_failover()
        snap = self.engine.health_snapshot()
        self.assertEqual(snap["active_source_id"], "FEED-LSEG")
        self.assertEqual(snap["engine_state"], EngineState.FAILOVER_ACTIVE.value)
        by_id = {s["source_id"]: s for s in snap["sources"]}
        self.assertEqual(by_id["FEED-BPIPE"]["status"], DataSourceStatus.STALE.value)
        self.assertAlmostEqual(by_id["FEED-BPIPE"]["staleness_seconds"], 3.0, places=3)
        self.assertIsNone(by_id["FEED-BPIPE"]["healthy_for_seconds"])


class TestConcurrency(unittest.TestCase):
    def test_concurrent_heartbeats_and_fetches_keep_state_consistent(self):
        clock = FakeClock()
        engine = VendorFallbackHierarchyEngine(
            recovery_cooling_seconds=0.0, clock=clock
        )
        for i in range(3):
            engine.register_data_source(
                DataSourceNode(f"S{i}", f"Vendor {i}", i + 1, max_staleness_seconds=1e6)
            )
            engine.record_heartbeat(f"S{i}")

        errors = []

        def worker(index: int):
            try:
                for _ in range(200):
                    engine.record_heartbeat(f"S{index}")
                    engine.fetch_market_data_tick("AAPL", lambda s, src: (100.0, 1.0))
                    engine.evaluate_health_and_failover()
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(engine.active_source_id, "S0")
        ids = [e.event_id for e in engine.event_log]
        self.assertEqual(len(ids), len(set(ids)))


class TestRegressions(HierarchyTestCase):
    """Each of these fails against a naive implementation."""

    def test_synthetic_tick_does_not_claim_to_be_current(self):
        self.beat_all()
        self.engine.fetch_market_data_tick("AAPL", quote_for)
        self.clock.advance(50.0)
        tick = self.engine.fetch_market_data_tick("AAPL", quote_for)
        # Previously `timestamp` was set to "now", so a downstream age check on the tick
        # read 0s after an arbitrarily long outage.
        self.assertGreater(tick.age_seconds, 0.0)
        now = datetime.datetime.now(datetime.timezone.utc)
        self.assertGreater((now - tick.timestamp).total_seconds(), -1.0)
        self.assertTrue(tick.is_synthetic)

    def test_hold_never_routes_to_a_source_measured_stale(self):
        self.beat_all()
        self.clock.advance(3.0)  # primary stale -> failover to secondary
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

        # Primary recovers and the secondary dies, inside the promotion window.
        self.clock.advance(6.0)
        self.engine.record_heartbeat("FEED-BPIPE")
        self.assertEqual(self.secondary.status, DataSourceStatus.STALE)

        hits = []

        def record(symbol, source_id):
            hits.append(source_id)
            return quote_for(symbol, source_id)

        tick = self.engine.fetch_market_data_tick("AAPL", record)
        self.assertEqual(hits, ["FEED-BPIPE"])
        self.assertEqual(tick.source_id, "FEED-BPIPE")

    def test_nan_price_is_never_returned_or_cached(self):
        self.beat_all()
        with self.assertRaises(FallbackEngineError):
            self.engine.fetch_market_data_tick(
                "AAPL", lambda s, src: (float("nan"), -5.0)
            )
        self.assertNotIn("AAPL", self.engine.synthetic_cache)

    def test_single_heartbeat_after_a_long_outage_does_not_promote(self):
        self.beat_all()
        self.clock.advance(3.0)
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

        # Ten minutes of primary outage, then exactly one heartbeat. A rule keyed on
        # time since the last failover would promote here.
        for _ in range(200):
            self.clock.advance(3.0)
            self.engine.record_heartbeat("FEED-LSEG")
        self.engine.record_heartbeat("FEED-BPIPE")
        self.assertEqual(self.engine.active_source_id, "FEED-LSEG")

    def test_duplicate_registration_cannot_shrink_the_hierarchy(self):
        with self.assertRaises(ValueError):
            self.engine.register_data_source(
                DataSourceNode("FEED-BPIPE", "Impostor", 3)
            )
        self.assertEqual(len(self.engine.data_sources), 3)

    def test_aware_timestamp_does_not_raise_type_error(self):
        try:
            self.engine.record_heartbeat(
                "FEED-BPIPE", datetime.datetime.now(datetime.timezone.utc)
            )
        except TypeError as exc:  # pragma: no cover - regression guard
            self.fail(f"aware timestamp raised TypeError: {exc}")


if __name__ == "__main__":
    unittest.main()
