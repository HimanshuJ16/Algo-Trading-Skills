"""Behavioural tests for the broker API version migrator.

Several tests are explicit regressions against defects in the previous revision and
are labelled as such: each one fails against the old behaviour and passes against the
fix. The most important are

  - ``stop_stop_gtc`` was emitted as a Coinbase order_configuration key; no such key
    exists in the CreateOrder schema;
  - ``time_in_force`` was ignored, silently turning an IOC/FOK order into a resting GTC
    order;
  - a missing or zero limit price became the string ``"0"``;
  - canary routing rolled a fresh ``random.random()`` per call, so a retry of the same
    order could land on the other API version;
  - an out-of-range canary percentage was clamped, so ``50`` (meaning 50%) became a
    100% instant cutover;
  - the shadow read joined the thread pool, blocking the live V1 read path on the V2
    shadow call, and measured V2 latency only after awaiting V1.
"""
import logging
import threading
import time
import unittest
from decimal import Decimal

from api_migrator import (
    BrokerAPIVersionMigrator,
    CoinbaseAdvancedTradeTranslator,
    LatencyTracker,
    MigrationPhase,
    OrderPayload,
    RollbackPolicy,
    UnsupportedOrderError,
    format_decimal,
)

# The migrator logs a CRITICAL on rollback by design; keep test output readable.
logging.disable(logging.CRITICAL)


def make_payload(**overrides):
    base = dict(symbol="BTC-USD", action="BUY", quantity=1.5, client_order_id="cid-1")
    base.update(overrides)
    return OrderPayload(**base)


class TestOrderPayloadValidation(unittest.TestCase):
    """An unusable order must be rejected at construction, not half-translated."""

    def test_client_order_id_is_required(self):
        with self.assertRaises(ValueError):
            make_payload(client_order_id="")
        with self.assertRaises(ValueError):
            make_payload(client_order_id="   ")

    def test_quantity_must_be_positive_and_finite(self):
        for bad in (0, -1, "0", float("nan"), float("inf")):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    make_payload(quantity=bad)

    def test_prices_must_be_positive_when_present(self):
        with self.assertRaises(ValueError):
            make_payload(order_type="LIMIT", limit_price=0)
        with self.assertRaises(ValueError):
            make_payload(order_type="LIMIT", limit_price=-5)

    def test_symbol_must_be_non_empty_and_is_normalised(self):
        with self.assertRaises(ValueError):
            make_payload(symbol="  ")
        self.assertEqual(make_payload(symbol=" btc-usd ").symbol, "BTC-USD")

    def test_boolean_quantity_is_rejected(self):
        # bool is a subclass of int, so a naive numeric check accepts True as 1.
        with self.assertRaises(ValueError):
            make_payload(quantity=True)

    def test_quantity_is_normalised_without_binary_float_error(self):
        self.assertEqual(make_payload(quantity=0.1).quantity, Decimal("0.1"))

    def test_payload_is_immutable(self):
        # The migrator binds a version to the order id at routing time; mutating the id
        # afterwards would leave that binding pointing at the wrong version silently.
        payload = make_payload()
        with self.assertRaises(Exception):
            payload.client_order_id = "something-else"
        with self.assertRaises(Exception):
            payload.quantity = -5


class TestDecimalFormatting(unittest.TestCase):
    def test_no_exponent_notation(self):
        # REGRESSION: str(1e-05) is '1e-05', which brokers parsing decimal strings reject.
        self.assertEqual(format_decimal(Decimal("1E-5")), "0.00001")
        self.assertEqual(format_decimal(Decimal("1E+5")), "100000")

    def test_caller_scale_is_preserved(self):
        self.assertEqual(format_decimal(Decimal("65000.0")), "65000.0")
        self.assertEqual(format_decimal(Decimal("0.50")), "0.50")

    def test_binary_float_artefacts_do_not_leak(self):
        # REGRESSION: str(0.1 + 0.2) is '0.30000000000000004'.
        payload = make_payload(quantity="0.3")
        self.assertEqual(format_decimal(payload.quantity), "0.3")


class TestCoinbaseTranslator(unittest.TestCase):
    """Field names and configuration keys must match the published CreateOrder schema."""

    def setUp(self):
        self.translator = CoinbaseAdvancedTradeTranslator()

    def test_market_order_uses_documented_field_names(self):
        # REGRESSION: previously emitted instrument_id / size / client_id, none of which
        # are CreateOrder fields, and a float size where the API requires a string.
        body = self.translator.translate(make_payload(order_type="MARKET"))
        self.assertEqual(
            body,
            {
                "client_order_id": "cid-1",
                "product_id": "BTC-USD",
                "side": "BUY",
                "order_configuration": {"market_market_ioc": {"base_size": "1.5"}},
            },
        )
        self.assertIsInstance(
            body["order_configuration"]["market_market_ioc"]["base_size"], str
        )

    def test_limit_gtc_order(self):
        body = self.translator.translate(
            make_payload(action="SELL", quantity=2.0, order_type="LIMIT", limit_price=65000.0)
        )
        config = body["order_configuration"]["limit_limit_gtc"]
        self.assertEqual(config, {"base_size": "2.0", "limit_price": "65000.0"})

    def test_stop_order_never_emits_the_nonexistent_stop_stop_gtc_key(self):
        # REGRESSION: the previous translator emitted {"stop_stop_gtc": {...}}. That key
        # does not exist in the Coinbase CreateOrder schema; stops are stop-limit orders.
        body = self.translator.translate(
            make_payload(
                order_type="STOP",
                limit_price=64000,
                stop_price=64500,
                stop_direction="STOP_DIRECTION_STOP_DOWN",
            )
        )
        config = body["order_configuration"]
        self.assertNotIn("stop_stop_gtc", config)
        self.assertEqual(
            config["stop_limit_stop_limit_gtc"],
            {
                "base_size": "1.5",
                "limit_price": "64000",
                "stop_price": "64500",
                "stop_direction": "STOP_DIRECTION_STOP_DOWN",
            },
        )

    def test_stop_order_missing_required_fields_is_rejected(self):
        # REGRESSION: previously produced a stop payload with stop_price "0" and no
        # limit price or direction at all.
        with self.assertRaises(UnsupportedOrderError) as ctx:
            self.translator.translate(make_payload(order_type="STOP", stop_price=64500))
        self.assertIn("limit_price", str(ctx.exception))
        self.assertIn("stop_direction", str(ctx.exception))

    def test_invalid_stop_direction_is_rejected(self):
        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(
                make_payload(
                    order_type="STOP",
                    limit_price=1,
                    stop_price=2,
                    stop_direction="DOWNWARDS",
                )
            )

    def test_limit_order_without_price_is_rejected(self):
        # REGRESSION: `str(price) if price else "0"` sent a limit price of zero.
        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(make_payload(order_type="LIMIT"))

    def test_time_in_force_is_never_silently_downgraded(self):
        # REGRESSION: time_in_force was ignored entirely, so a FOK limit order became a
        # resting limit_limit_gtc order and left unintended exposure in the book.
        fok = self.translator.translate(
            make_payload(order_type="LIMIT", limit_price=100, time_in_force="FOK")
        )
        self.assertIn("limit_limit_fok", fok["order_configuration"])

        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(
                make_payload(order_type="LIMIT", limit_price=100, time_in_force="IOC")
            )

    def test_market_fok_is_distinct_from_market_ioc(self):
        body = self.translator.translate(make_payload(order_type="MARKET", time_in_force="FOK"))
        self.assertIn("market_market_fok", body["order_configuration"])

    def test_gtd_is_rejected_rather_than_given_an_invented_expiry(self):
        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(
                make_payload(order_type="LIMIT", limit_price=100, time_in_force="GTD")
            )

    def test_unknown_side_and_order_type_are_rejected(self):
        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(make_payload(action="SHORT"))
        with self.assertRaises(UnsupportedOrderError):
            self.translator.translate(make_payload(order_type="TRAILING_STOP"))


class MigratorTestCase(unittest.TestCase):
    def setUp(self):
        self.migrator = BrokerAPIVersionMigrator()
        self.addCleanup(self.migrator.close)

    def enter_canary(self, pct):
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, pct)


class TestPhaseStateMachine(MigratorTestCase):
    def test_gate_sequence_is_enforced(self):
        # REGRESSION: any phase could be set from any other, so a migration could jump
        # straight to 100% of order flow on a version that had never carried an order.
        with self.assertRaises(ValueError):
            self.migrator.set_phase(MigrationPhase.V2_ONLY)
        with self.assertRaises(ValueError):
            self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 0.01)

        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 0.01)
        self.migrator.set_phase(MigrationPhase.V2_ONLY)
        self.assertIs(self.migrator.get_phase(), MigrationPhase.V2_ONLY)

    def test_out_of_range_canary_is_rejected_not_clamped(self):
        # REGRESSION: max(0.0, min(1.0, 50)) == 1.0. An operator typing "50" for 50%
        # silently moved every order onto the untested version at once.
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        with self.assertRaises(ValueError):
            self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 50)
        with self.assertRaises(ValueError):
            self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, -0.1)
        self.assertIs(self.migrator.get_phase(), MigrationPhase.SHADOW_MODE)

    def test_omitting_the_percentage_does_not_reset_the_ramp(self):
        self.enter_canary(0.25)
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER)
        self.assertEqual(self.migrator.canary_v2_percentage, 0.25)

    def test_rollback_is_reachable_from_every_phase(self):
        self.enter_canary(0.5)
        self.migrator.set_phase(MigrationPhase.ROLLBACK_V1)
        self.assertIs(self.migrator.get_phase(), MigrationPhase.ROLLBACK_V1)
        self.assertEqual(self.migrator.canary_v2_percentage, 0.0)

    def test_rollback_latches_until_explicitly_cleared(self):
        # REGRESSION: rollback was an ordinary phase, so an automated ramp scheduler
        # could re-promote the version that had just failed.
        self.enter_canary(0.5)
        self.migrator.trigger_rollback("V2 rejecting orders")
        for phase in (MigrationPhase.CANARY_CUTOVER, MigrationPhase.V2_ONLY, MigrationPhase.V1_ONLY):
            with self.subTest(phase=phase):
                with self.assertRaises(ValueError):
                    self.migrator.set_phase(phase, 0.5)
        self.assertEqual(self.migrator.rollback_reason, "V2 rejecting orders")

        self.assertIs(self.migrator.clear_rollback("desk-ops", "root cause fixed"), MigrationPhase.V1_ONLY)
        self.assertIsNone(self.migrator.rollback_reason)

    def test_clear_rollback_requires_an_operator_and_reason(self):
        self.migrator.trigger_rollback("boom")
        with self.assertRaises(ValueError):
            self.migrator.clear_rollback("", "reason")
        with self.assertRaises(ValueError):
            self.migrator.clear_rollback("ops", "")

    def test_clear_rollback_outside_rollback_is_an_error(self):
        with self.assertRaises(ValueError):
            self.migrator.clear_rollback("ops", "nothing to clear")

    def test_trigger_rollback_is_idempotent(self):
        self.migrator.trigger_rollback("first")
        self.migrator.trigger_rollback("second")
        self.assertEqual(self.migrator.rollback_reason, "first")


class TestOrderRouting(MigratorTestCase):
    def test_routing_is_deterministic_per_client_order_id(self):
        # REGRESSION: with random.random() per call, retrying an order that timed out
        # could send it to the other API version, where the broker's de-duplication may
        # not recognise the first attempt — one intent, two live orders.
        self.enter_canary(0.5)
        payload = make_payload(client_order_id="retry-me")
        decisions = {self.migrator.route_order_version(payload) for _ in range(200)}
        self.assertEqual(len(decisions), 1)

    def test_routing_agrees_across_independent_instances(self):
        # Replicas and restarts must reach the same assignment; Python's built-in hash()
        # of a string is salted per process and would not.
        other = BrokerAPIVersionMigrator()
        self.addCleanup(other.close)
        self.enter_canary(0.3)
        other.set_phase(MigrationPhase.SHADOW_MODE)
        other.set_phase(MigrationPhase.CANARY_CUTOVER, 0.3)

        for i in range(500):
            payload = make_payload(client_order_id="ord-%d" % i)
            self.assertEqual(
                self.migrator.route_order_version(payload),
                other.route_order_version(payload),
            )

    def test_canary_endpoints_are_absolute(self):
        self.enter_canary(0.0)
        for i in range(100):
            self.assertEqual(self.migrator.route_order_version(make_payload(client_order_id="a%d" % i)), "V1")

        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 1.0)
        for i in range(100):
            self.assertEqual(self.migrator.route_order_version(make_payload(client_order_id="b%d" % i)), "V2")

    def test_canary_split_approximates_the_configured_fraction(self):
        self.enter_canary(0.5)
        v2 = sum(
            self.migrator.route_order_version(make_payload(client_order_id="split-%d" % i)) == "V2"
            for i in range(4000)
        )
        # Binomial(4000, 0.5) has sd ~31.6; +/-200 is over six standard deviations.
        self.assertTrue(1800 <= v2 <= 2200, "unbalanced hash bucketing: %d" % v2)

    def test_shadow_mode_keeps_all_writes_on_v1(self):
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        for i in range(50):
            self.assertEqual(self.migrator.route_order_version(make_payload(client_order_id="s%d" % i)), "V1")

    def test_rollback_routes_writes_back_to_v1(self):
        self.enter_canary(1.0)
        self.assertEqual(self.migrator.route_order_version(make_payload(client_order_id="x")), "V2")
        self.migrator.trigger_rollback("kill switch")
        self.assertEqual(self.migrator.route_order_version(make_payload(client_order_id="x")), "V1")

    def test_request_counters_track_routing(self):
        self.enter_canary(1.0)
        for i in range(10):
            self.migrator.route_order_version(make_payload(client_order_id="c%d" % i))
        stats = self.migrator.stats()
        self.assertEqual(stats["v2_requests"], 10)
        self.assertEqual(stats["v1_requests"], 0)

    def test_concurrent_routing_counts_every_order_exactly_once(self):
        self.enter_canary(0.5)
        barrier = threading.Barrier(8)

        def worker(offset):
            barrier.wait()
            for i in range(100):
                self.migrator.route_order_version(make_payload(client_order_id="t%d-%d" % (offset, i)))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = self.migrator.stats()
        self.assertEqual(stats["v1_requests"] + stats["v2_requests"], 800)


class TestVersionAffinity(MigratorTestCase):
    def test_followup_routes_to_the_version_that_holds_the_order(self):
        self.enter_canary(1.0)
        self.migrator.route_order_version(make_payload(client_order_id="ord-9"))
        self.assertEqual(self.migrator.route_followup_version("ord-9"), "V2")

    def test_affinity_survives_a_canary_ramp(self):
        # Determinism alone is not enough: re-bucketing at a higher percentage would
        # aim a cancel at a version that never saw the order.
        self.enter_canary(0.0)
        payload = make_payload(client_order_id="ramped")
        self.assertEqual(self.migrator.route_order_version(payload), "V1")
        self.migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 1.0)
        self.assertEqual(self.migrator.route_followup_version("ramped"), "V1")

    def test_unknown_order_returns_none_rather_than_a_default(self):
        self.assertIsNone(self.migrator.route_followup_version("never-seen"))
        self.assertIsNone(self.migrator.route_followup_version(""))

    def test_binding_can_be_corrected_after_a_fallback(self):
        self.enter_canary(1.0)
        self.migrator.route_order_version(make_payload(client_order_id="fb"))
        self.migrator.bind_order_version("fb", "v1")
        self.assertEqual(self.migrator.route_followup_version("fb"), "V1")

    def test_bind_rejects_an_unknown_version(self):
        with self.assertRaises(ValueError):
            self.migrator.bind_order_version("fb", "V3")

    def test_affinity_map_is_bounded_and_evicts_oldest_first(self):
        migrator = BrokerAPIVersionMigrator(affinity_size=3)
        self.addCleanup(migrator.close)
        for i in range(5):
            migrator.bind_order_version("o%d" % i, "V1")
        self.assertIsNone(migrator.route_followup_version("o0"))
        self.assertEqual(migrator.route_followup_version("o4"), "V1")
        self.assertEqual(migrator.stats()["affinity_tracked"], 3)


class TestSchemaAudit(MigratorTestCase):
    def test_top_level_drift_is_reported(self):
        v1 = {"status": "ok", "order_id": "123", "price": 100.0, "qty": 10}
        good = dict(v1, v2_extra=True)
        bad = {"status": "ok", "id": "123", "price": "100.0", "qty": 10}

        diff_good = self.migrator.audit_shadow_response("/v1/orders", v1, good, 10.0, 12.0)
        self.assertTrue(diff_good.is_equivalent)
        self.assertEqual(diff_good.latency_diff_ms, 2.0)
        self.assertIn("v2_extra", diff_good.added_in_v2)

        diff_bad = self.migrator.audit_shadow_response("/v1/orders", v1, bad, 10.0, 15.0)
        self.assertFalse(diff_bad.is_equivalent)
        self.assertIn("order_id", diff_bad.missing_in_v2)
        self.assertIn("price", diff_bad.type_mismatches)

    def test_nested_type_drift_is_detected(self):
        # REGRESSION: only top-level keys were compared, so a price that became a string
        # inside a nested fill record passed the shadow gate cleanly.
        v1 = {"order": {"fills": [{"price": 1.0, "qty": 2}]}}
        v2 = {"order": {"fills": [{"price": "1.0", "qty": 2}]}}
        diff = self.migrator.audit_shadow_response("/orders", v1, v2, 1.0, 1.0)
        self.assertFalse(diff.is_equivalent)
        self.assertIn("order.fills.[].price", diff.type_mismatches)

    def test_nested_missing_field_is_detected(self):
        v1 = {"account": {"balance": 1.0, "currency": "USD"}}
        v2 = {"account": {"balance": 1.0}}
        diff = self.migrator.audit_shadow_response("/account", v1, v2, 1.0, 1.0)
        self.assertIn("account.currency", diff.missing_in_v2)
        self.assertFalse(diff.is_equivalent)

    def test_list_responses_are_compared(self):
        # REGRESSION: the audit only ran when both responses were dicts, so a
        # list-returning endpoint reported zero drift for the whole shadow phase.
        diff = self.migrator.audit_shadow_response("/positions", [{"qty": 1}], [{"qty": "1"}], 1.0, 1.0)
        self.assertFalse(diff.is_equivalent)
        self.assertIn("[].qty", diff.type_mismatches)

    def test_null_values_are_unverified_rather_than_mismatched(self):
        diff = self.migrator.audit_shadow_response("/x", {"note": None}, {"note": "hello"}, 1.0, 1.0)
        self.assertTrue(diff.is_equivalent)
        self.assertIn("note", diff.unverified_paths)

    def test_a_list_empty_on_one_side_is_unverified_not_a_silent_pass(self):
        diff = self.migrator.audit_shadow_response("/fills", {"fills": [{"p": 1}]}, {"fills": []}, 1.0, 1.0)
        self.assertIn("fills.[]", diff.unverified_paths)

    def test_bool_is_not_interchangeable_with_int(self):
        diff = self.migrator.audit_shadow_response("/x", {"flag": True}, {"flag": 1}, 1.0, 1.0)
        self.assertIn("flag", diff.type_mismatches)

    def test_audit_log_is_bounded_while_totals_stay_exact(self):
        # REGRESSION: audit_log was an unbounded list, growing by one entry per read for
        # the whole multi-session shadow phase.
        migrator = BrokerAPIVersionMigrator(audit_log_size=5)
        self.addCleanup(migrator.close)
        for i in range(50):
            migrator.audit_shadow_response("/x", {"a": 1}, {"a": "1"}, 1.0, 1.0)
        stats = migrator.stats()
        self.assertEqual(stats["audit_log_retained"], 5)
        self.assertEqual(stats["audit_totals"]["compared"], 50)
        self.assertEqual(stats["audit_totals"]["drifted"], 50)
        self.assertEqual(stats["audit_drift_by_endpoint"]["/x"], 50)

    def test_audit_feeds_the_latency_tracker(self):
        self.migrator.audit_shadow_response("/x", {"a": 1}, {"a": 1}, 10.0, 20.0)
        snapshot = self.migrator.latency_tracker.snapshot()
        self.assertEqual(snapshot["V1"].count, 1)
        self.assertEqual(snapshot["V2"].mean_ms, 20.0)


class TestLatencyTracker(unittest.TestCase):
    def test_percentiles_match_independently_derived_values(self):
        # For the integers 1..100 under the standard linear (type-7) definition the
        # rank is q*(n-1): p95 -> 0.95*99 = 94.05 -> 95*0.95 + 96*0.05 = 95.05.
        tracker = LatencyTracker(reservoir_size=1000, min_samples_for_percentiles=10)
        for i in range(1, 101):
            tracker.record(float(i))
        v1 = tracker.snapshot()["V1"]
        self.assertEqual(v1.count, 100)
        self.assertAlmostEqual(v1.mean_ms, 50.5)
        self.assertAlmostEqual(v1.p50_ms, 50.5)
        self.assertAlmostEqual(v1.p95_ms, 95.05)
        self.assertAlmostEqual(v1.p99_ms, 99.01)
        self.assertEqual(v1.max_ms, 100.0)

    def test_counts_and_max_stay_exact_beyond_the_reservoir_capacity(self):
        tracker = LatencyTracker(reservoir_size=10, min_samples_for_percentiles=1)
        for i in range(1, 1001):
            tracker.record(float(i))
        v1 = tracker.snapshot()["V1"]
        self.assertEqual(v1.count, 1000)
        self.assertEqual(v1.max_ms, 1000.0)
        self.assertAlmostEqual(v1.mean_ms, 500.5)

    def test_comparison_flags_a_regression(self):
        tracker = LatencyTracker(min_samples_for_percentiles=10)
        for _ in range(100):
            tracker.record(10.0, 20.0)
        result = tracker.compare()
        self.assertIs(result.within_tolerance, False)
        self.assertAlmostEqual(result.mean_regression, 1.0)
        self.assertAlmostEqual(result.p99_ratio, 2.0)

    def test_comparison_passes_within_tolerance(self):
        tracker = LatencyTracker(min_samples_for_percentiles=10)
        for _ in range(100):
            tracker.record(10.0, 10.2)
        self.assertIs(tracker.compare().within_tolerance, True)

    def test_insufficient_samples_abstain_rather_than_pass(self):
        # "No evidence of a regression" is not "evidence of no regression"; a gate that
        # returns True on 3 samples promotes on silence.
        tracker = LatencyTracker(min_samples_for_percentiles=1000)
        for _ in range(3):
            tracker.record(10.0, 10.0)
        result = tracker.compare()
        self.assertIsNone(result.within_tolerance)
        self.assertTrue(any("p99 not gated" in r for r in result.reasons))

    def test_empty_tracker_is_undecided(self):
        self.assertIsNone(LatencyTracker().compare().within_tolerance)

    def test_v1_only_samples_are_retained(self):
        tracker = LatencyTracker(min_samples_for_percentiles=1)
        tracker.record(5.0, None)
        snapshot = tracker.snapshot()
        self.assertEqual(snapshot["V1"].count, 1)
        self.assertEqual(snapshot["V2"].count, 0)

    def test_invalid_latencies_are_rejected(self):
        tracker = LatencyTracker()
        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    tracker.record(bad)


class TestReadShadowing(MigratorTestCase):
    def test_v1_returns_without_waiting_for_a_slow_v2(self):
        # REGRESSION: `with ThreadPoolExecutor(...)` joins on exit, so the live read path
        # blocked for the full duration of the shadow call it was meant to ignore.
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)
        started = threading.Event()
        release = threading.Event()

        def slow_v2():
            started.set()
            release.wait(5.0)
            return {"id": 1}

        began = time.perf_counter()
        result = self.migrator.execute_read_shadowing(lambda: {"id": 1}, slow_v2, "/positions")
        elapsed = time.perf_counter() - began

        self.assertEqual(result, {"id": 1})
        self.assertLess(elapsed, 0.5, "production read path blocked on the shadow call")
        self.assertTrue(started.wait(2.0), "shadow call never ran")
        release.set()
        self.migrator.drain_shadows(5.0)

    def test_v2_latency_is_not_inflated_by_a_slow_v1(self):
        # REGRESSION: V2's elapsed time was sampled only after V1's future had been
        # awaited, so a slow V1 inflated V2 by its own duration — biasing the exact
        # number the migration gate reads.
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)

        def slow_v1():
            time.sleep(0.25)
            return {"id": 1}

        self.migrator.execute_read_shadowing(slow_v1, lambda: {"id": 1}, "/positions")
        self.assertEqual(self.migrator.drain_shadows(5.0), 0)

        snapshot = self.migrator.latency_tracker.snapshot()
        self.assertGreater(snapshot["V1"].mean_ms, 200.0)
        self.assertLess(snapshot["V2"].mean_ms, 100.0)

    def test_shadow_failure_is_counted_and_never_propagated(self):
        # REGRESSION: V2 exceptions were logged and discarded, so a wholly broken V2
        # endpoint produced no signal anywhere in the migrator.
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)

        def failing_v2():
            raise ConnectionError("v2 down")

        result = self.migrator.execute_read_shadowing(lambda: {"ok": True}, failing_v2, "/positions")
        self.assertEqual(result, {"ok": True})
        self.migrator.drain_shadows(5.0)
        self.assertEqual(self.migrator.stats()["shadow_errors"], 1)
        self.assertEqual(self.migrator.latency_tracker.snapshot()["V1"].count, 1)

    def test_v1_errors_do_propagate(self):
        self.migrator.set_phase(MigrationPhase.SHADOW_MODE)

        def failing_v1():
            raise ConnectionError("v1 down")

        with self.assertRaises(ConnectionError):
            self.migrator.execute_read_shadowing(failing_v1, lambda: {}, "/positions")

    def test_no_shadow_outside_shadow_and_canary_phases(self):
        calls = []
        result = self.migrator.execute_read_shadowing(
            lambda: {"ok": True}, lambda: calls.append(1), "/positions"
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(self.migrator.drain_shadows(1.0), 0)
        self.assertEqual(calls, [])

    def test_v1_baseline_is_captured_before_the_migration_begins(self):
        # Without this the first shadow comparison is made against an almost empty V1
        # sample, and the p99 gate has nothing to gate on.
        self.assertIs(self.migrator.get_phase(), MigrationPhase.V1_ONLY)
        self.migrator.execute_read_shadowing(lambda: {"ok": True}, lambda: None, "/positions")
        self.assertEqual(self.migrator.latency_tracker.snapshot()["V1"].count, 1)

    def test_shadow_ratio_zero_disables_shadowing_but_keeps_the_baseline(self):
        migrator = BrokerAPIVersionMigrator(shadow_read_ratio=0.0)
        self.addCleanup(migrator.close)
        migrator.set_phase(MigrationPhase.SHADOW_MODE)
        calls = []
        migrator.execute_read_shadowing(lambda: {"ok": True}, lambda: calls.append(1), "/x")
        self.assertEqual(migrator.drain_shadows(1.0), 0)
        self.assertEqual(calls, [])
        self.assertEqual(migrator.latency_tracker.snapshot()["V1"].count, 1)

    def test_shadow_is_shed_when_the_pool_is_saturated(self):
        migrator = BrokerAPIVersionMigrator(max_pending_shadows=1, shadow_workers=1)
        self.addCleanup(migrator.close)
        migrator.set_phase(MigrationPhase.SHADOW_MODE)
        release = threading.Event()
        started = threading.Event()

        def blocking_v2():
            started.set()
            release.wait(5.0)
            return {"id": 1}

        migrator.execute_read_shadowing(lambda: {"id": 1}, blocking_v2, "/x")
        self.assertTrue(started.wait(2.0))
        migrator.execute_read_shadowing(lambda: {"id": 1}, blocking_v2, "/x")

        self.assertEqual(migrator.stats()["shadow_shed"], 1)
        release.set()
        migrator.drain_shadows(5.0)

    def test_invalid_ratios_are_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            BrokerAPIVersionMigrator(shadow_read_ratio=1.5)
        with self.assertRaises(ValueError):
            BrokerAPIVersionMigrator(canary_v2_percentage=50)

    def test_close_is_idempotent(self):
        self.migrator.close()
        self.migrator.close()


class TestRollbackPolicy(MigratorTestCase):
    def test_error_rate_breach_latches_a_rollback(self):
        migrator = BrokerAPIVersionMigrator(
            rollback_policy=RollbackPolicy(min_v2_orders_for_error_rate=10, max_v2_error_rate=0.02)
        )
        self.addCleanup(migrator.close)
        migrator.set_phase(MigrationPhase.SHADOW_MODE)
        migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 0.5)
        for i in range(20):
            migrator.record_order_outcome("V2", accepted=(i % 4 != 0))

        decision = migrator.enforce_rollback_policy()
        self.assertTrue(decision.should_rollback)
        self.assertAlmostEqual(decision.v2_error_rate, 0.25)
        self.assertIs(migrator.get_phase(), MigrationPhase.ROLLBACK_V1)

    def test_below_the_minimum_sample_size_no_rollback_fires(self):
        migrator = BrokerAPIVersionMigrator(
            rollback_policy=RollbackPolicy(min_v2_orders_for_error_rate=50)
        )
        self.addCleanup(migrator.close)
        migrator.set_phase(MigrationPhase.SHADOW_MODE)
        migrator.set_phase(MigrationPhase.CANARY_CUTOVER, 0.5)
        for _ in range(3):
            migrator.record_order_outcome("V2", accepted=False)
        self.assertFalse(migrator.enforce_rollback_policy().should_rollback)
        self.assertIs(migrator.get_phase(), MigrationPhase.CANARY_CUTOVER)

    def test_schema_drift_breach_is_reported(self):
        migrator = BrokerAPIVersionMigrator(
            rollback_policy=RollbackPolicy(min_audits_for_drift_rate=5, max_schema_drift_rate=0.0)
        )
        self.addCleanup(migrator.close)
        for _ in range(10):
            migrator.audit_shadow_response("/x", {"a": 1}, {"a": "1"}, 1.0, 1.0)
        decision = migrator.evaluate_rollback()
        self.assertTrue(decision.should_rollback)
        self.assertAlmostEqual(decision.schema_drift_rate, 1.0)

    def test_enforcement_is_inert_while_v2_carries_no_order_flow(self):
        # A shadow-phase blip must not latch a rollback of writes that never moved.
        migrator = BrokerAPIVersionMigrator(
            rollback_policy=RollbackPolicy(min_audits_for_drift_rate=5, max_schema_drift_rate=0.0)
        )
        self.addCleanup(migrator.close)
        migrator.set_phase(MigrationPhase.SHADOW_MODE)
        for _ in range(10):
            migrator.audit_shadow_response("/x", {"a": 1}, {"a": "1"}, 1.0, 1.0)
        decision = migrator.enforce_rollback_policy()
        self.assertTrue(decision.should_rollback)
        self.assertIs(migrator.get_phase(), MigrationPhase.SHADOW_MODE)

    def test_evaluation_is_pure(self):
        self.enter_canary(0.5)
        for i in range(200):
            self.migrator.record_order_outcome("V2", accepted=False)
        self.migrator.evaluate_rollback()
        self.assertIs(self.migrator.get_phase(), MigrationPhase.CANARY_CUTOVER)

    def test_healthy_canary_does_not_roll_back(self):
        self.enter_canary(0.5)
        for _ in range(500):
            self.migrator.record_order_outcome("V2", accepted=True)
        self.assertFalse(self.migrator.enforce_rollback_policy().should_rollback)

    def test_policy_rejects_nonsensical_thresholds(self):
        with self.assertRaises(ValueError):
            RollbackPolicy(max_v2_error_rate=1.5)
        with self.assertRaises(ValueError):
            RollbackPolicy(max_p99_latency_ratio=0)

    def test_record_order_outcome_rejects_an_unknown_version(self):
        with self.assertRaises(ValueError):
            self.migrator.record_order_outcome("V3", accepted=True)


if __name__ == "__main__":
    unittest.main()
