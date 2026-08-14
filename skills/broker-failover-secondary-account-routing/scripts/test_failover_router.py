"""Behavioural tests for the broker failover router.

Tests marked REGRESSION fail against the previous revision and pass against the fix.
The defects they pin down:

  - any primary exception (including a timeout, where the order may already be working)
    caused the *same order* to be re-sent to the secondary account — 200 shares filled
    for an intended 100;
  - a terminal business rejection ("insufficient buying power") was failed over, so an
    order the primary's pre-trade controls refused got filled at the secondary;
  - a position-reducing order failed over into an account with no position, opening new
    exposure instead of closing any;
  - HALF_OPEN admitted unbounded concurrent probes — ten live orders to a broker
    believed down, against documentation promising "a single test order";
  - one slow in-flight success returning after the circuit tripped reset it to CLOSED;
  - the recovery timeout used the wall clock, so an NTP step moved it.
"""
import logging
import socket
import threading
import time
import unittest
from decimal import Decimal
from unittest.mock import patch

from failover_router import (
    AllBrokersUnavailableError,
    AmbiguousOrderStateError,
    BrokerError,
    BrokerFailoverRouter,
    BrokerHealthStatus,
    FailureClass,
    MockBrokerAdapter,
    OrderRequest,
    OrderResult,
    PositionAffinityError,
    PositionEffect,
    SymbolMappingError,
    classify_exception,
)

logging.disable(logging.CRITICAL)


def order(**overrides):
    base = dict(symbol="AAPL", action="BUY", quantity=100, client_order_id="cid-1")
    base.update(overrides)
    return OrderRequest(**base)


class AcceptsThenLosesResponse(MockBrokerAdapter):
    """The broker accepted the order; the client never saw the response."""

    def place_order(self, request):
        self.executed_orders.append("accepted-but-unacknowledged")
        raise TimeoutError("read timed out")


class GatedBroker(MockBrokerAdapter):
    """Blocks in ``place_order`` until released, then applies ``outcome``."""

    def __init__(self, name, account_id, gate, outcome="fail"):
        super().__init__(name, account_id)
        self.gate = gate
        self.outcome = outcome
        self.calls = []

    def place_order(self, request):
        self.calls.append(request.client_order_id)
        self.gate.wait(5.0)
        if self.outcome == "fail":
            raise BrokerError("503", FailureClass.UNAVAILABLE, status_code=503)
        return MockBrokerAdapter.place_order(self, request)


class TestOrderRequestValidation(unittest.TestCase):
    def test_client_order_id_is_required(self):
        for bad in ("", "   "):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    order(client_order_id=bad)

    def test_quantity_must_be_positive_and_finite(self):
        for bad in (0, -1, "0", float("nan"), float("inf")):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    order(quantity=bad)

    def test_boolean_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            order(quantity=True)

    def test_action_must_be_buy_or_sell(self):
        with self.assertRaises(ValueError):
            order(action="SHORT")
        self.assertEqual(order(action=" buy ").action, "BUY")

    def test_limit_price_must_be_positive(self):
        with self.assertRaises(ValueError):
            order(limit_price=0)

    def test_quantity_avoids_binary_float_error(self):
        self.assertEqual(order(quantity=0.1).quantity, Decimal("0.1"))

    def test_signed_quantity_follows_direction(self):
        self.assertEqual(order(action="BUY", quantity=5).signed_quantity, Decimal(5))
        self.assertEqual(order(action="SELL", quantity=5).signed_quantity, Decimal(-5))

    def test_payload_is_immutable(self):
        request = order()
        with self.assertRaises(Exception):
            request.quantity = 999


class TestExceptionClassification(unittest.TestCase):
    def test_broker_error_carries_its_own_class(self):
        exc = BrokerError("throttled", FailureClass.RATE_LIMITED, 429, retry_after_s=2.0)
        self.assertIs(classify_exception(exc), FailureClass.RATE_LIMITED)

    def test_connection_refused_and_dns_failure_are_safe(self):
        # Nothing was sent, so failing over cannot duplicate anything.
        self.assertIs(classify_exception(ConnectionRefusedError()), FailureClass.UNAVAILABLE)
        self.assertIs(classify_exception(socket.gaierror()), FailureClass.UNAVAILABLE)

    def test_transport_failures_after_send_are_ambiguous(self):
        for exc in (TimeoutError(), ConnectionResetError(), BrokenPipeError()):
            with self.subTest(exc=type(exc).__name__):
                self.assertIs(classify_exception(exc), FailureClass.AMBIGUOUS)

    def test_unknown_exceptions_default_to_ambiguous(self):
        # REGRESSION: the previous router treated every exception as a clean failure and
        # failed the order over. The asymmetry is the point — a wrong "ambiguous" costs
        # a status query, a wrong "failed" costs a duplicate order in another account.
        self.assertIs(classify_exception(RuntimeError("???")), FailureClass.AMBIGUOUS)


class RouterTestCase(unittest.TestCase):
    def setUp(self):
        self.primary = MockBrokerAdapter("primary_broker", "P123")
        self.secondary = MockBrokerAdapter("secondary_broker", "S456")
        self.router = BrokerFailoverRouter(
            primary_broker=self.primary,
            secondary_broker=self.secondary,
            max_consecutive_failures=3,
            recovery_timeout_seconds=0.1,
        )
        self.router.register_symbol_map("AAPL", "AAPL.P", "AAPL.S")


class TestAmbiguousOutcomes(RouterTestCase):
    def test_timeout_never_reaches_the_secondary_account(self):
        # REGRESSION: this filled 100 at the primary and another 100 at the secondary.
        self.router.primary_broker = self.primary = AcceptsThenLosesResponse("primary_broker", "P123")
        router = BrokerFailoverRouter(self.primary, self.secondary, recovery_timeout_seconds=0.1)

        with self.assertRaises(AmbiguousOrderStateError) as ctx:
            router.submit_order(order())

        self.assertEqual(ctx.exception.client_order_id, "cid-1")
        self.assertEqual(ctx.exception.broker_name, "primary_broker")
        self.assertEqual(len(self.secondary.executed_orders), 0)
        self.assertEqual(router.stats().ambiguous_outcomes, 1)

    def test_resolver_that_finds_the_order_prevents_the_failover(self):
        primary = AcceptsThenLosesResponse("primary_broker", "P123")
        found = OrderResult("P_9", "primary_broker", "P123", "AAPL.P", "BUY", Decimal(100), "FILLED", time.time(), "cid-1")
        router = BrokerFailoverRouter(
            primary, self.secondary, order_status_resolver=lambda broker, coid: found
        )

        result = router.submit_order(order())
        self.assertEqual(result.order_id, "P_9")
        self.assertEqual(len(self.secondary.executed_orders), 0)
        self.assertEqual(router.get_position("primary_broker", "AAPL"), Decimal(100))

    def test_resolver_returning_none_still_raises(self):
        primary = AcceptsThenLosesResponse("primary_broker", "P123")
        router = BrokerFailoverRouter(primary, self.secondary, order_status_resolver=lambda b, c: None)
        with self.assertRaises(AmbiguousOrderStateError):
            router.submit_order(order())

    def test_a_failing_resolver_is_not_read_as_order_not_found(self):
        # "The status query itself failed" is not evidence the order is absent.
        def broken_resolver(broker, coid):
            raise ConnectionError("status endpoint down too")

        primary = AcceptsThenLosesResponse("primary_broker", "P123")
        router = BrokerFailoverRouter(primary, self.secondary, order_status_resolver=broken_resolver)
        with self.assertRaises(AmbiguousOrderStateError):
            router.submit_order(order())
        self.assertEqual(len(self.secondary.executed_orders), 0)

    def test_ambiguity_on_the_secondary_also_raises(self):
        primary = MockBrokerAdapter("primary_broker", "P123", should_fail=True)
        secondary = AcceptsThenLosesResponse("secondary_broker", "S456")
        router = BrokerFailoverRouter(primary, secondary)
        with self.assertRaises(AmbiguousOrderStateError) as ctx:
            router.submit_order(order())
        self.assertEqual(ctx.exception.broker_name, "secondary_broker")


class TestRejectionHandling(RouterTestCase):
    def setUp(self):
        super().setUp()
        self.primary.failure = BrokerError(
            "insufficient buying power", FailureClass.REJECTED, status_code=400
        )

    def test_terminal_rejection_is_not_shopped_to_the_secondary(self):
        # REGRESSION: an order the primary's pre-trade controls refused was filled at
        # the secondary account instead.
        with self.assertRaises(BrokerError) as ctx:
            self.router.submit_order(order(quantity=10**9))
        self.assertIs(ctx.exception.failure_class, FailureClass.REJECTED)
        self.assertEqual(len(self.secondary.executed_orders), 0)

    def test_rejections_do_not_count_against_broker_health(self):
        # REGRESSION: bad orders from the strategy tripped the breaker and pushed all
        # flow to the secondary, though the primary was perfectly healthy.
        for i in range(10):
            with self.assertRaises(BrokerError):
                self.router.submit_order(order(client_order_id="r%d" % i))
        stats = self.router.stats()
        self.assertEqual(stats.circuit_state, "CLOSED")
        self.assertEqual(stats.primary_failures, 0)
        self.assertEqual(stats.terminal_rejections, 10)


class TestSafeFailover(RouterTestCase):
    def test_unavailable_primary_fails_over_with_symbol_translation(self):
        self.primary.should_fail = True
        result = self.router.submit_order(order())
        self.assertEqual(result.broker_name, "secondary_broker")
        self.assertEqual(result.symbol, "AAPL.S")
        self.assertEqual(self.router.stats().failovers, 1)

    def test_both_brokers_down_raises_a_typed_error(self):
        # REGRESSION: this propagated a raw RuntimeError from the secondary while the
        # circuit still reported CLOSED.
        self.primary.should_fail = True
        self.secondary.should_fail = True
        with self.assertRaises(AllBrokersUnavailableError) as ctx:
            self.router.submit_order(order())
        self.assertEqual(ctx.exception.client_order_id, "cid-1")

    def test_rate_limit_sets_a_backoff_that_skips_the_primary(self):
        self.primary.failure = BrokerError("429", FailureClass.RATE_LIMITED, 429, retry_after_s=30.0)
        self.router.submit_order(order(client_order_id="a"))

        self.primary.failure = None  # primary would now succeed, but is inside backoff
        result = self.router.submit_order(order(client_order_id="b"))
        self.assertEqual(result.broker_name, "secondary_broker")
        self.assertEqual(len(self.primary.executed_orders), 0)

    def test_manual_reset_clears_the_backoff(self):
        self.primary.failure = BrokerError("429", FailureClass.RATE_LIMITED, 429, retry_after_s=30.0)
        self.router.submit_order(order(client_order_id="a"))
        self.primary.failure = None
        self.router.manual_reset()
        self.assertEqual(self.router.submit_order(order(client_order_id="b")).broker_name, "primary_broker")


class TestCircuitBreaker(RouterTestCase):
    def test_trips_after_the_configured_consecutive_failures(self):
        self.primary.should_fail = True
        for i in range(2):
            self.router.submit_order(order(client_order_id="f%d" % i))
        self.assertEqual(self.router.stats().circuit_state, "CLOSED")

        self.router.submit_order(order(client_order_id="f2"))
        self.assertEqual(self.router.stats().circuit_state, "OPEN")

        result = self.router.submit_order(order(client_order_id="f3"))
        self.assertEqual(result.broker_name, "secondary_broker")
        self.assertEqual(len(self.secondary.executed_orders), 4)

    def test_open_circuit_does_not_touch_the_primary(self):
        self.primary.should_fail = True
        for i in range(3):
            self.router.submit_order(order(client_order_id="f%d" % i))
        self.primary.should_fail = False
        self.router.submit_order(order(client_order_id="after"))
        self.assertEqual(len(self.primary.executed_orders), 0)

    def test_half_open_probe_success_closes_the_circuit(self):
        self.primary.should_fail = True
        for i in range(3):
            self.router.submit_order(order(client_order_id="f%d" % i))
        self.assertEqual(self.router.stats().circuit_state, "OPEN")

        time.sleep(0.15)
        self.router.submit_order(order(client_order_id="probe-fail"))
        self.assertEqual(self.router.stats().circuit_state, "OPEN")

        time.sleep(0.15)
        self.primary.should_fail = False
        result = self.router.submit_order(order(client_order_id="probe-ok"))
        self.assertEqual(result.broker_name, "primary_broker")
        self.assertEqual(self.router.stats().circuit_state, "CLOSED")

    def test_half_open_probes_are_bounded(self):
        # REGRESSION: ten concurrent callers each sent a live order to a broker believed
        # down, while the documentation promised a single test order.
        gate = threading.Event()
        primary = GatedBroker("primary_broker", "P123", gate, outcome="fail")
        router = BrokerFailoverRouter(
            primary, self.secondary, max_consecutive_failures=1,
            recovery_timeout_seconds=0.05, half_open_max_probes=1,
        )
        gate.set()
        router.submit_order(order(client_order_id="trip"))
        self.assertEqual(router.stats().circuit_state, "OPEN")

        primary.calls.clear()
        gate.clear()
        time.sleep(0.1)  # circuit becomes probe-eligible

        threads = [
            threading.Thread(target=router.submit_order, args=(order(client_order_id="p%d" % i),))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        time.sleep(0.1)
        gate.set()
        for t in threads:
            t.join(5.0)

        self.assertEqual(len(primary.calls), 1, "half-open admitted more than one probe")

    def test_multiple_successes_can_be_required_before_closing(self):
        primary = MockBrokerAdapter("primary_broker", "P123", should_fail=True)
        router = BrokerFailoverRouter(
            primary, self.secondary, max_consecutive_failures=1,
            recovery_timeout_seconds=0.05, half_open_successes_to_close=2,
        )
        router.submit_order(order(client_order_id="trip"))
        primary.should_fail = False

        time.sleep(0.1)
        router.submit_order(order(client_order_id="p1"))
        self.assertEqual(router.stats().circuit_state, "HALF_OPEN")
        router.submit_order(order(client_order_id="p2"))
        self.assertEqual(router.stats().circuit_state, "CLOSED")

    def test_a_stale_success_cannot_close_a_tripped_circuit(self):
        # REGRESSION: one slow in-flight call returning after the breaker tripped reset
        # it to CLOSED and zeroed the failure counter.
        gate = threading.Event()

        class SlowSuccessThenFailures(MockBrokerAdapter):
            def place_order(self, request):
                if request.client_order_id == "slow":
                    gate.wait(5.0)
                    return MockBrokerAdapter.place_order(self, request)
                raise BrokerError("503", FailureClass.UNAVAILABLE, 503)

        primary = SlowSuccessThenFailures("primary_broker", "P123")
        router = BrokerFailoverRouter(primary, self.secondary, max_consecutive_failures=2)

        slow = threading.Thread(target=router.submit_order, args=(order(client_order_id="slow"),))
        slow.start()
        time.sleep(0.05)
        for i in range(3):
            router.submit_order(order(client_order_id="f%d" % i))
        self.assertEqual(router.stats().circuit_state, "OPEN")

        gate.set()
        slow.join(5.0)
        self.assertEqual(router.stats().circuit_state, "OPEN")

    def test_recovery_timeout_is_immune_to_wall_clock_jumps(self):
        # REGRESSION: the timeout compared time.time(), so an NTP step backwards held
        # the circuit OPEN indefinitely.
        self.primary.should_fail = True
        for i in range(3):
            self.router.submit_order(order(client_order_id="f%d" % i))
        self.assertEqual(self.router.stats().circuit_state, "OPEN")

        self.primary.should_fail = False
        with patch("failover_router.time.time", return_value=0.0):
            time.sleep(0.15)
            result = self.router.submit_order(order(client_order_id="after-jump"))
        self.assertEqual(result.broker_name, "primary_broker")

    def test_manual_open_survives_the_recovery_timeout(self):
        self.router.manual_open("desk investigating primary")
        self.assertEqual(self.router.stats().circuit_state, "OPEN")
        time.sleep(0.15)
        result = self.router.submit_order(order(client_order_id="held"))
        self.assertEqual(result.broker_name, "secondary_broker")
        self.assertEqual(len(self.primary.executed_orders), 0)

        self.router.manual_reset()
        self.assertEqual(self.router.submit_order(order(client_order_id="freed")).broker_name, "primary_broker")

    def test_health_reflects_circuit_state(self):
        self.assertIs(self.router.primary_health(), BrokerHealthStatus.HEALTHY)
        self.primary.should_fail = True
        self.router.submit_order(order(client_order_id="f0"))
        self.assertIs(self.router.primary_health(), BrokerHealthStatus.DEGRADED)
        for i in range(2):
            self.router.submit_order(order(client_order_id="g%d" % i))
        self.assertIs(self.router.primary_health(), BrokerHealthStatus.DOWN)


class TestPositionAffinity(RouterTestCase):
    def test_reduce_order_is_never_failed_over_into_a_new_position(self):
        # REGRESSION: the closing sell was routed to the secondary account, which held
        # nothing — leaving long 100 in one account and short 100 in the other.
        self.router.set_position("primary_broker", "AAPL", 100)
        self.primary.should_fail = True

        with self.assertRaises(PositionAffinityError):
            self.router.submit_order(
                order(action="SELL", client_order_id="close", position_effect=PositionEffect.REDUCE)
            )
        self.assertEqual(len(self.secondary.executed_orders), 0)

    def test_reduce_order_routes_to_the_holding_account(self):
        self.router.set_position("secondary_broker", "AAPL", 100)
        result = self.router.submit_order(
            order(action="SELL", client_order_id="close", position_effect=PositionEffect.REDUCE)
        )
        self.assertEqual(result.broker_name, "secondary_broker")
        self.assertEqual(self.router.get_position("secondary_broker", "AAPL"), Decimal(0))

    def test_reduce_without_any_holding_account_is_refused(self):
        with self.assertRaises(PositionAffinityError):
            self.router.submit_order(
                order(action="SELL", client_order_id="close", position_effect=PositionEffect.REDUCE)
            )

    def test_reduce_requires_sufficient_size_not_merely_a_position(self):
        self.router.set_position("primary_broker", "AAPL", 50)
        with self.assertRaises(PositionAffinityError):
            self.router.submit_order(
                order(action="SELL", quantity=100, client_order_id="close",
                      position_effect=PositionEffect.REDUCE)
            )

    def test_reduce_covers_a_short_position(self):
        self.router.set_position("primary_broker", "AAPL", -100)
        result = self.router.submit_order(
            order(action="BUY", client_order_id="cover", position_effect=PositionEffect.REDUCE)
        )
        self.assertEqual(result.broker_name, "primary_broker")
        self.assertEqual(self.router.get_position("primary_broker", "AAPL"), Decimal(0))

    def test_open_orders_update_the_position_cache(self):
        self.router.submit_order(order(action="BUY", quantity=30, client_order_id="o1"))
        self.router.submit_order(order(action="BUY", quantity=20, client_order_id="o2"))
        self.assertEqual(self.router.get_position("primary_broker", "AAPL"), Decimal(50))

    def test_positions_are_tracked_per_account_and_do_not_net(self):
        self.router.set_position("primary_broker", "AAPL", 100)
        self.router.set_position("secondary_broker", "AAPL", -100)
        self.assertEqual(self.router.get_position("primary_broker", "AAPL"), Decimal(100))
        self.assertEqual(self.router.get_position("secondary_broker", "AAPL"), Decimal(-100))

    def test_unknown_broker_is_rejected(self):
        with self.assertRaises(ValueError):
            self.router.set_position("third_broker", "AAPL", 1)


class TestSymbolMapping(RouterTestCase):
    def test_symbols_are_translated_per_broker(self):
        self.assertEqual(self.router.submit_order(order()).symbol, "AAPL.P")
        self.primary.should_fail = True
        self.assertEqual(self.router.submit_order(order(client_order_id="b")).symbol, "AAPL.S")

    def test_permissive_mode_passes_the_canonical_ticker_through(self):
        result = self.router.submit_order(order(symbol="MSFT", client_order_id="m"))
        self.assertEqual(result.symbol, "MSFT")

    def test_strict_mode_refuses_an_unmapped_symbol(self):
        router = BrokerFailoverRouter(self.primary, self.secondary, strict_symbol_mapping=True)
        with self.assertRaises(SymbolMappingError):
            router.submit_order(order(symbol="MSFT", client_order_id="m"))

    def test_a_mapping_fault_is_not_misread_as_an_ambiguous_broker_outcome(self):
        # Nothing was sent, so it is neither ambiguous nor a reason to fail over.
        router = BrokerFailoverRouter(self.primary, self.secondary, strict_symbol_mapping=True)
        with self.assertRaises(SymbolMappingError):
            router.submit_order(order(symbol="MSFT", client_order_id="m"))
        self.assertEqual(len(self.secondary.executed_orders), 0)
        self.assertEqual(router.stats().ambiguous_outcomes, 0)

    def test_registration_validates_its_inputs(self):
        with self.assertRaises(ValueError):
            self.router.register_symbol_map("", "A", "B")
        with self.assertRaises(ValueError):
            self.router.register_symbol_map("AAPL", "", "B")


class TestConstructionAndTelemetry(RouterTestCase):
    def test_distinct_broker_names_are_required(self):
        duplicate = MockBrokerAdapter("primary_broker", "X")
        with self.assertRaises(ValueError):
            BrokerFailoverRouter(self.primary, duplicate)

    def test_constructor_validates_thresholds(self):
        for kwargs in (
            {"max_consecutive_failures": 0},
            {"recovery_timeout_seconds": -1},
            {"half_open_max_probes": 0},
            {"half_open_successes_to_close": 0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    BrokerFailoverRouter(self.primary, self.secondary, **kwargs)

    def test_submit_order_rejects_a_non_order(self):
        with self.assertRaises(TypeError):
            self.router.submit_order({"symbol": "AAPL"})

    def test_stats_counts_routing_outcomes(self):
        self.router.submit_order(order(client_order_id="a"))
        self.primary.should_fail = True
        self.router.submit_order(order(client_order_id="b"))
        stats = self.router.stats()
        self.assertEqual(stats.routed_primary, 1)
        self.assertEqual(stats.routed_secondary, 1)
        self.assertEqual(stats.failovers, 1)

    def test_seconds_until_probe_is_reported_while_open(self):
        self.primary.should_fail = True
        for i in range(3):
            self.router.submit_order(order(client_order_id="f%d" % i))
        remaining = self.router.stats().seconds_until_probe
        self.assertIsNotNone(remaining)
        self.assertGreaterEqual(remaining, 0.0)
        self.assertLessEqual(remaining, 0.1)

    def test_concurrent_submissions_are_all_accounted_for(self):
        barrier = threading.Barrier(8)

        def worker(offset):
            barrier.wait()
            for i in range(25):
                self.router.submit_order(order(client_order_id="t%d-%d" % (offset, i)))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = self.router.stats()
        self.assertEqual(stats.routed_primary + stats.routed_secondary, 200)
        self.assertEqual(self.router.get_position("primary_broker", "AAPL"), Decimal(200 * 100))


if __name__ == "__main__":
    unittest.main()
