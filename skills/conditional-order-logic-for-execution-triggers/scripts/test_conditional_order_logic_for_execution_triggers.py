import threading
import unittest
from datetime import datetime, timedelta, timezone

from conditional_order_logic_for_execution_triggers import (
    TIMESTAMP_FIELD,
    STATUS_CANCELLED,
    STATUS_DORMANT,
    STATUS_TRIGGERED,
    AndCondition,
    ChildOrderPayload,
    ConditionalOrderEngine,
    ConditionalOrderTrigger,
    CrossAssetCondition,
    NotCondition,
    OrCondition,
    PriceCondition,
    TimeCondition,
    VolumeCondition,
)


class TestAtomicConditions(unittest.TestCase):

    def test_price_condition_evaluation(self):
        cond = PriceCondition("AAPL", "last", ">=", 150.00)

        self.assertFalse(cond.evaluate({"AAPL": {"last": 149.99}}))
        self.assertTrue(cond.evaluate({"AAPL": {"last": 150.00}}))
        self.assertTrue(cond.evaluate({"AAPL": {"last": 151.00}}))

    def test_missing_symbol_or_field_is_unknown_not_false(self):
        cond = PriceCondition("AAPL", "last", ">=", 150.00)

        self.assertIsNone(cond.evaluate_tristate({}))
        self.assertIsNone(cond.evaluate_tristate({"AAPL": {"bid": 151.0}}))
        # The fail-safe boolean projection still refuses to fire.
        self.assertFalse(cond.evaluate({}))

    def test_non_numeric_and_nan_values_do_not_fire(self):
        cond = PriceCondition("AAPL", "last", ">=", 150.00)

        self.assertIsNone(cond.evaluate_tristate({"AAPL": {"last": None}}))
        self.assertIsNone(cond.evaluate_tristate({"AAPL": {"last": "150.10"}}))
        self.assertIsNone(cond.evaluate_tristate({"AAPL": {"last": float("nan")}}))

    def test_unsupported_operator_rejected_at_construction(self):
        # Silently returning False for a typo'd operator produces a trigger that
        # can never fire, discovered only when the order never reaches the venue.
        with self.assertRaises(ValueError):
            PriceCondition("AAPL", "last", "=>", 150.00)

    def test_equality_operator_requires_explicit_tolerance(self):
        with self.assertRaises(ValueError):
            PriceCondition("AAPL", "last", "==", 150.00)

        cond = PriceCondition("AAPL", "last", "==", 150.00, tolerance=0.005)
        self.assertTrue(cond.evaluate({"AAPL": {"last": 150.004}}))
        self.assertFalse(cond.evaluate({"AAPL": {"last": 150.02}}))

    def test_invalid_construction_arguments_rejected(self):
        with self.assertRaises(ValueError):
            PriceCondition("", "last", ">=", 150.0)
        with self.assertRaises(ValueError):
            PriceCondition("AAPL", "last", ">=", float("nan"))
        with self.assertRaises(ValueError):
            PriceCondition("AAPL", "last", ">=", 150.0, max_quote_age_seconds=0)
        with self.assertRaises(ValueError):
            VolumeCondition("AAPL", "volume", -1)

    def test_volume_condition_threshold(self):
        cond = VolumeCondition("AAPL", "volume", 1_000_000)
        self.assertFalse(cond.evaluate({"AAPL": {"volume": 999_999}}))
        self.assertTrue(cond.evaluate({"AAPL": {"volume": 1_000_000}}))


class TestStaleness(unittest.TestCase):

    def test_stale_quote_evaluates_unknown(self):
        cond = PriceCondition("SPY", "last", ">=", 500.0, max_quote_age_seconds=5.0)
        now = 1_700_000_000.0

        fresh = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now - 1.0}}
        stale = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now - 30.0}}

        self.assertTrue(cond.evaluate(fresh, now))
        self.assertIsNone(cond.evaluate_tristate(stale, now))
        self.assertFalse(cond.evaluate(stale, now))

    def test_exact_age_boundary_is_still_fresh(self):
        cond = PriceCondition("SPY", "last", ">=", 500.0, max_quote_age_seconds=5.0)
        now = 1_700_000_000.0
        at_limit = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now - 5.0}}
        just_over = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now - 5.001}}

        self.assertTrue(cond.evaluate(at_limit, now))
        self.assertFalse(cond.evaluate(just_over, now))

    def test_implausible_future_timestamp_is_unknown(self):
        # A millisecond timestamp read as seconds is never "old", so it would
        # defeat the staleness check silently.
        cond = PriceCondition("SPY", "last", ">=", 500.0, max_quote_age_seconds=5.0)
        now = 1_700_000_000.0
        millis = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now * 1000.0}}

        self.assertIsNone(cond.evaluate_tristate(millis, now))
        # Sub-limit clock skew is tolerated rather than blocking the trigger.
        skewed = {"SPY": {"last": 501.0, TIMESTAMP_FIELD: now + 1.0}}
        self.assertTrue(cond.evaluate(skewed, now))

    def test_missing_timestamp_when_staleness_enabled_is_unknown(self):
        cond = PriceCondition("SPY", "last", ">=", 500.0, max_quote_age_seconds=5.0)
        self.assertIsNone(
            cond.evaluate_tristate({"SPY": {"last": 501.0}}, 1_700_000_000.0)
        )


class TestCompositeLogic(unittest.TestCase):

    def test_composite_and_condition(self):
        # Trigger when AAPL >= 150.00 AND SPY >= 500.00 (cross-asset gate).
        and_cond = AndCondition([
            PriceCondition("AAPL", "last", ">=", 150.00),
            PriceCondition("SPY", "last", ">=", 500.00),
        ])

        self.assertFalse(and_cond.evaluate(
            {"AAPL": {"last": 152.00}, "SPY": {"last": 499.50}}))
        self.assertTrue(and_cond.evaluate(
            {"AAPL": {"last": 152.00}, "SPY": {"last": 500.25}}))

    def test_or_condition_evaluation(self):
        or_cond = OrCondition([
            PriceCondition("VIX", "last", ">=", 30.0),
            VolumeCondition("AAPL", "volume", 1_000_000),
        ])

        self.assertTrue(or_cond.evaluate(
            {"VIX": {"last": 31.0}, "AAPL": {"volume": 500_000}}))
        self.assertTrue(or_cond.evaluate(
            {"VIX": {"last": 20.0}, "AAPL": {"volume": 2_000_000}}))
        self.assertFalse(or_cond.evaluate(
            {"VIX": {"last": 20.0}, "AAPL": {"volume": 500_000}}))

    def test_kleene_propagation_through_composites(self):
        known_true = PriceCondition("AAPL", "last", ">=", 150.0)
        known_false = PriceCondition("AAPL", "last", ">=", 900.0)
        unknown = PriceCondition("SPY", "last", ">=", 500.0)
        state = {"AAPL": {"last": 152.0}}  # no SPY quote at all

        # A definite FALSE still short-circuits an AND even with data missing.
        self.assertFalse(AndCondition([known_false, unknown]).evaluate_tristate(state))
        # A missing input leaves the AND undecided rather than falsely FALSE.
        self.assertIsNone(AndCondition([known_true, unknown]).evaluate_tristate(state))
        # A definite TRUE still short-circuits an OR.
        self.assertTrue(OrCondition([known_true, unknown]).evaluate_tristate(state))
        self.assertIsNone(OrCondition([known_false, unknown]).evaluate_tristate(state))

    def test_not_condition_does_not_fire_on_missing_data(self):
        # Regression: with two-valued logic a missing quote reads as FALSE, so
        # NOT(missing) becomes TRUE and releases a live order on absent data.
        inner = PriceCondition("SPY", "last", ">=", 500.0)
        not_cond = NotCondition(inner)

        self.assertIsNone(not_cond.evaluate_tristate({}))
        self.assertFalse(not_cond.evaluate({}))
        self.assertTrue(not_cond.evaluate({"SPY": {"last": 499.0}}))
        self.assertFalse(not_cond.evaluate({"SPY": {"last": 501.0}}))

    def test_empty_composite_rejected(self):
        # Regression: all([]) is True, so an empty AND gate would fire the child
        # order on the first tick without any condition being checked.
        with self.assertRaises(ValueError):
            AndCondition([])
        with self.assertRaises(ValueError):
            OrCondition([])

    def test_composite_rejects_non_condition_children(self):
        with self.assertRaises(ValueError):
            AndCondition([PriceCondition("AAPL", "last", ">=", 1.0), "AAPL > 150"])


class TestCrossAssetCondition(unittest.TestCase):

    def test_ratio_and_offset_comparison(self):
        # Fire when ES trades at least 2.00 above 10x the SPY print.
        cond = CrossAssetCondition(
            "ES", "last", ">=", "SPY", "last", ratio=10.0, offset=2.0)

        self.assertTrue(cond.evaluate({"ES": {"last": 5002.5}, "SPY": {"last": 500.0}}))
        self.assertFalse(cond.evaluate({"ES": {"last": 5001.0}, "SPY": {"last": 500.0}}))

    def test_missing_reference_leg_is_unknown(self):
        # Regression: a dropped benchmark feed must not silently turn a
        # relative-value trigger into an outright one.
        cond = CrossAssetCondition("ES", "last", ">=", "SPY", "last", ratio=10.0)
        self.assertIsNone(cond.evaluate_tristate({"ES": {"last": 9999.0}}))

    def test_equality_operator_not_offered_for_cross_asset(self):
        with self.assertRaises(ValueError):
            CrossAssetCondition("ES", "last", "==", "SPY", "last")


class TestTimeCondition(unittest.TestCase):

    def test_naive_datetime_rejected(self):
        with self.assertRaises(ValueError):
            TimeCondition(">=", datetime(2026, 1, 2, 15, 50))

    def test_aware_datetime_compared_against_supplied_clock(self):
        target = datetime(2026, 1, 2, 20, 50, tzinfo=timezone.utc)
        cond = TimeCondition(">=", target)

        before = (target - timedelta(seconds=1)).timestamp()
        after = (target + timedelta(seconds=1)).timestamp()

        self.assertFalse(cond.evaluate({}, before))
        self.assertTrue(cond.evaluate({}, target.timestamp()))
        self.assertTrue(cond.evaluate({}, after))


class TestChildOrderPayload(unittest.TestCase):

    def test_payload_validation(self):
        with self.assertRaises(ValueError):
            ChildOrderPayload("TSLA", "BUY", 0, "LIMIT", 199.5)
        with self.assertRaises(ValueError):
            ChildOrderPayload("TSLA", "buy", 100, "LIMIT", 199.5)
        with self.assertRaises(ValueError):
            ChildOrderPayload("TSLA", "BUY", 100, "LIMIT")
        with self.assertRaises(ValueError):
            ChildOrderPayload("TSLA", "BUY", 100, "STOP", 199.5)

    def test_payload_is_immutable(self):
        payload = ChildOrderPayload("TSLA", "BUY", 100, "LIMIT", 199.5)
        with self.assertRaises(Exception):
            payload.quantity = 10_000


class TestTriggerLifecycle(unittest.TestCase):

    def _trigger(self):
        cond = PriceCondition("TSLA", "last", "<=", 200.00)
        child = ChildOrderPayload("TSLA", "BUY", 100, "LIMIT", 199.50)
        return ConditionalOrderTrigger("TRIG_1", cond, child)

    def test_trigger_single_fire_behavior(self):
        trigger = self._trigger()
        market_state = {"TSLA": {"last": 198.00}}

        order1 = trigger.process_tick(market_state)
        self.assertIsNotNone(order1)
        self.assertEqual(order1.symbol, "TSLA")
        self.assertEqual(trigger.status, STATUS_TRIGGERED)

        self.assertIsNone(trigger.process_tick(market_state))

    def test_cancel_semantics(self):
        trigger = self._trigger()
        self.assertEqual(trigger.status, STATUS_DORMANT)
        self.assertTrue(trigger.cancel())
        self.assertEqual(trigger.status, STATUS_CANCELLED)
        # A cancelled trigger never fires, even on a satisfying tick.
        self.assertIsNone(trigger.process_tick({"TSLA": {"last": 100.0}}))
        # Cancelling twice, or cancelling a fired trigger, reports failure.
        self.assertFalse(trigger.cancel())

        fired = self._trigger()
        fired.process_tick({"TSLA": {"last": 198.0}})
        self.assertFalse(fired.cancel())

    def test_concurrent_ticks_release_exactly_one_order(self):
        # Regression: an unguarded check-then-set lets two feed-handler threads
        # both observe DORMANT and both release the child order.
        trigger = self._trigger()
        market_state = {"TSLA": {"last": 198.00}}
        released = []
        released_lock = threading.Lock()
        start = threading.Barrier(16)

        def worker():
            start.wait()
            payload = trigger.process_tick(market_state)
            if payload is not None:
                with released_lock:
                    released.append(payload)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(released), 1)

    def test_trigger_rejects_malformed_construction(self):
        child = ChildOrderPayload("TSLA", "BUY", 100, "MARKET")
        with self.assertRaises(ValueError):
            ConditionalOrderTrigger("TRIG", "TSLA <= 200", child)
        with self.assertRaises(ValueError):
            ConditionalOrderTrigger(
                "", PriceCondition("TSLA", "last", "<=", 200.0), child)


class TestConditionalOrderEngine(unittest.TestCase):

    def test_documented_verification_scenario(self):
        # SKILL.md Verification: (AAPL.last >= 150) AND (SPY.last >= 500).
        engine = ConditionalOrderEngine()
        tree = AndCondition([
            PriceCondition("AAPL", "last", ">=", 150.00),
            PriceCondition("SPY", "last", ">=", 500.00),
        ])
        trigger = ConditionalOrderTrigger(
            "AAPL_SPY", tree, ChildOrderPayload("AAPL", "BUY", 100, "MARKET"))
        engine.register(trigger)

        released = engine.process_tick(
            {"AAPL": {"last": 150.50}, "SPY": {"last": 499.00}})
        self.assertEqual(released, [])
        self.assertEqual(trigger.status, STATUS_DORMANT)

        released = engine.process_tick(
            {"AAPL": {"last": 150.50}, "SPY": {"last": 500.50}})
        self.assertEqual(len(released), 1)
        self.assertEqual(released[0].symbol, "AAPL")
        self.assertEqual(trigger.status, STATUS_TRIGGERED)

    def test_oco_group_cancels_sibling_on_first_fire(self):
        engine = ConditionalOrderEngine()
        take_profit = ConditionalOrderTrigger(
            "TP",
            PriceCondition("TSLA", "last", ">=", 220.0),
            ChildOrderPayload("TSLA", "SELL", 100, "LIMIT", 220.0),
        )
        stop_loss = ConditionalOrderTrigger(
            "SL",
            PriceCondition("TSLA", "last", "<=", 180.0),
            ChildOrderPayload("TSLA", "SELL", 100, "MARKET"),
        )
        engine.register(take_profit, oco_group="TSLA_BRACKET")
        engine.register(stop_loss, oco_group="TSLA_BRACKET")

        released = engine.process_tick({"TSLA": {"last": 221.0}})
        self.assertEqual(len(released), 1)
        self.assertEqual(take_profit.status, STATUS_TRIGGERED)
        self.assertEqual(stop_loss.status, STATUS_CANCELLED)

        # The cancelled leg stays silent even when its own level trades later.
        self.assertEqual(engine.process_tick({"TSLA": {"last": 150.0}}), [])

    def test_duplicate_trigger_id_rejected(self):
        engine = ConditionalOrderEngine()
        cond = PriceCondition("TSLA", "last", "<=", 200.0)
        child = ChildOrderPayload("TSLA", "BUY", 100, "MARKET")
        engine.register(ConditionalOrderTrigger("DUP", cond, child))
        with self.assertRaises(ValueError):
            engine.register(ConditionalOrderTrigger("DUP", cond, child))

    def test_engine_rejects_non_mapping_market_state(self):
        engine = ConditionalOrderEngine()
        with self.assertRaises(ValueError):
            engine.process_tick([("TSLA", 198.0)])

    def test_engine_pins_one_clock_for_the_whole_tick(self):
        engine = ConditionalOrderEngine()
        target = datetime(2026, 1, 2, 20, 50, tzinfo=timezone.utc)
        trigger = ConditionalOrderTrigger(
            "TIME",
            TimeCondition(">=", target),
            ChildOrderPayload("SPY", "SELL", 10, "MARKET"),
        )
        engine.register(trigger)

        self.assertEqual(engine.process_tick({}, target.timestamp() - 1.0), [])
        self.assertEqual(len(engine.process_tick({}, target.timestamp())), 1)


if __name__ == '__main__':
    unittest.main()
