import threading
import unittest

from canary_releases_for_strategy_code_changes import (
    CanaryConfigError,
    DeploymentPhase,
    OrderSignal,
    RoutingAction,
    StrategyCanaryRouter,
    StrategyRegistration,
)


class TestCanaryReleases(unittest.TestCase):
    """Phase behaviour: what each phase does with a signal."""

    def setUp(self):
        self.router = StrategyCanaryRouter()

    def test_shadow_mode_drops_order(self):
        self.router.register_strategy(
            StrategyRegistration("strat_alpha", DeploymentPhase.SHADOW))

        signal = OrderSignal("strat_alpha", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)

        self.assertIsNone(routed)

    def test_shadow_decision_reports_hypothetical_quantity(self):
        # The workflow tells operators to log shadow signals as hypothetical
        # fills; the decision must therefore carry the size that would have been
        # sent, and must be distinguishable from a rejection.
        self.router.register_strategy(
            StrategyRegistration("strat_alpha", DeploymentPhase.SHADOW))

        decision = self.router.route(
            OrderSignal("strat_alpha", "AAPL", 1000, 150.0, "BUY"))

        self.assertIs(decision.action, RoutingAction.SUPPRESSED)
        self.assertFalse(decision.is_live)
        self.assertEqual(decision.requested_quantity, 1000)
        self.assertEqual(decision.routed_quantity, 0)
        self.assertEqual(decision.notional, 150_000.0)
        self.assertIsNone(decision.signal)

    def test_canary_mode_scales_correctly(self):
        self.router.register_strategy(
            StrategyRegistration("strat_beta", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1))

        signal = OrderSignal("strat_beta", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)

        # 10% of 1000 = 100
        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 100)

    def test_canary_mode_lot_rounding(self):
        self.router.register_strategy(
            StrategyRegistration("strat_gamma", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=100))

        # 5% of 2500 is 125. Floored to a 100-share board lot: 100.
        signal = OrderSignal("strat_gamma", "AAPL", 2500, 150.0, "BUY")
        routed = self.router.route_order(signal)

        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 100)

    def test_canary_mode_lot_drops_if_too_small(self):
        self.router.register_strategy(
            StrategyRegistration("strat_gamma", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=100))

        # 5% of 1000 is 50. Floored to 100-share lots is 0. Must drop.
        signal = OrderSignal("strat_gamma", "AAPL", 1000, 150.0, "BUY")
        decision = self.router.route(signal)

        self.assertIsNone(decision.signal)
        self.assertIs(decision.action, RoutingAction.REJECTED)
        self.assertEqual(decision.binding_constraint, "min_quantity")

    def test_production_mode_full_routing(self):
        self.router.register_strategy(
            StrategyRegistration("strat_delta", DeploymentPhase.PRODUCTION))

        signal = OrderSignal("strat_delta", "AAPL", 1000, 150.0, "BUY")
        routed = self.router.route_order(signal)

        self.assertIsNotNone(routed)
        self.assertEqual(routed.quantity, 1000)

    def test_min_quantity_is_independent_of_lot_step(self):
        # Binance publishes minQty and stepSize separately; a venue can require
        # 500 shares minimum in steps of 100.
        self.router.register_strategy(
            StrategyRegistration("strat_eps", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=100,
                                 min_quantity=500))

        below = self.router.route(OrderSignal("strat_eps", "0700.HK", 4000, 10.0, "BUY"))
        self.assertIs(below.action, RoutingAction.REJECTED)  # 400 < 500

        at_min = self.router.route(OrderSignal("strat_eps", "0700.HK", 5000, 10.0, "BUY"))
        self.assertIs(at_min.action, RoutingAction.SCALED)
        self.assertEqual(at_min.routed_quantity, 500)


class TestNoMutationOfCallerSignal(unittest.TestCase):
    """Regression: v1 rewrote `signal.quantity` in place and returned the same object."""

    def setUp(self):
        self.router = StrategyCanaryRouter()
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=1))

    def test_route_returns_a_new_object(self):
        signal = OrderSignal("s", "AAPL", 1000, 150.0, "BUY")
        decision = self.router.route(signal)

        self.assertIsNot(decision.signal, signal)
        self.assertEqual(signal.quantity, 1000, "caller's signal was mutated")
        self.assertEqual(decision.signal.quantity, 50)

    def test_routing_the_same_signal_twice_is_idempotent(self):
        # A retry path that re-routes the same signal must not scale 5% of 5%.
        signal = OrderSignal("s", "AAPL", 1000, 150.0, "BUY")

        first = self.router.route_order(signal)
        second = self.router.route_order(signal)

        self.assertEqual(first.quantity, 50)
        self.assertEqual(second.quantity, 50)

    def test_client_order_id_is_preserved(self):
        signal = OrderSignal("s", "AAPL", 1000, 150.0, "BUY", client_order_id="cl-1")
        routed = self.router.route_order(signal)
        self.assertEqual(routed.client_order_id, "cl-1")


class TestScalingArithmetic(unittest.TestCase):
    def setUp(self):
        self.router = StrategyCanaryRouter()

    def test_scaling_is_not_subject_to_binary_float_truncation(self):
        # Independently derived: 29% of 100 shares is exactly 29 shares.
        # int(100 * 0.29) is 28 in binary floating point (28.999999999999996),
        # which is what v1 routed.
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.29, min_lot_size=1))

        decision = self.router.route(OrderSignal("s", "AAPL", 100, 10.0, "BUY"))
        self.assertEqual(decision.routed_quantity, 29)

    def test_scaling_floors_rather_than_rounds(self):
        # 7% of 1000 = 70; 7% of 999 = 69.93 -> 69, never 70. Rounding up would
        # exceed the canary risk budget.
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.07, min_lot_size=1))

        self.assertEqual(
            self.router.route(OrderSignal("s", "AAPL", 1000, 10.0, "BUY")).routed_quantity, 70)
        self.assertEqual(
            self.router.route(OrderSignal("s", "AAPL", 999, 10.0, "BUY")).routed_quantity, 69)


class TestConfigurationValidation(unittest.TestCase):
    def setUp(self):
        self.router = StrategyCanaryRouter()

    def test_zero_lot_size_is_refused_at_registration(self):
        # Regression: v1 accepted it and raised ZeroDivisionError inside the
        # live order path on the first canary signal.
        with self.assertRaises(CanaryConfigError):
            self.router.register_strategy(
                StrategyRegistration("s", DeploymentPhase.CANARY, min_lot_size=0))

    def test_negative_lot_size_is_refused(self):
        with self.assertRaises(CanaryConfigError):
            self.router.register_strategy(
                StrategyRegistration("s", DeploymentPhase.CANARY, min_lot_size=-100))

    def test_scale_factor_bounds(self):
        for bad in (0.0, 1.0, 1.5, -0.1, float("nan"), float("inf")):
            with self.subTest(factor=bad):
                with self.assertRaises(CanaryConfigError):
                    self.router.register_strategy(
                        StrategyRegistration("s", DeploymentPhase.CANARY,
                                             canary_scale_factor=bad))

    def test_min_quantity_must_be_reachable_from_the_lot_step(self):
        with self.assertRaises(CanaryConfigError):
            self.router.register_strategy(
                StrategyRegistration("s", DeploymentPhase.CANARY,
                                     min_lot_size=100, min_quantity=150))

    def test_order_cap_below_venue_minimum_notional_is_refused(self):
        with self.assertRaises(CanaryConfigError):
            self.router.register_strategy(
                StrategyRegistration("s", DeploymentPhase.CANARY,
                                     min_notional=1000.0,
                                     max_canary_order_notional=100.0))

    def test_negative_notional_limits_are_refused(self):
        with self.assertRaises(CanaryConfigError):
            self.router.register_strategy(
                StrategyRegistration("s", DeploymentPhase.CANARY,
                                     canary_notional_budget=-1.0))


class TestSignalValidation(unittest.TestCase):
    def setUp(self):
        self.router = StrategyCanaryRouter()
        self.router.register_strategy(
            StrategyRegistration("prod", DeploymentPhase.PRODUCTION))

    def test_unregistered_strategy_is_rejected_not_silently_dropped(self):
        decision = self.router.route(OrderSignal("ghost", "AAPL", 100, 10.0, "BUY"))

        self.assertIs(decision.action, RoutingAction.REJECTED)
        self.assertEqual(decision.binding_constraint, "registration")
        self.assertIsNone(decision.phase)

    def test_invalid_quantities_are_rejected_even_in_production(self):
        for bad in (0, -100, 10.5, True):
            with self.subTest(quantity=bad):
                decision = self.router.route(OrderSignal("prod", "AAPL", bad, 10.0, "BUY"))
                self.assertIs(decision.action, RoutingAction.REJECTED)

    def test_invalid_prices_are_rejected(self):
        for bad in (-1.0, float("nan"), float("inf")):
            with self.subTest(price=bad):
                decision = self.router.route(OrderSignal("prod", "AAPL", 100, bad, "BUY"))
                self.assertIs(decision.action, RoutingAction.REJECTED)

    def test_empty_side_is_rejected(self):
        decision = self.router.route(OrderSignal("prod", "AAPL", 100, 10.0, "  "))
        self.assertIs(decision.action, RoutingAction.REJECTED)

    def test_zero_price_is_allowed_only_when_no_notional_limit_is_configured(self):
        allowed = self.router.route(OrderSignal("prod", "AAPL", 100, 0.0, "BUY"))
        self.assertIs(allowed.action, RoutingAction.ROUTED)

        self.router.register_strategy(
            StrategyRegistration("capped", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10,
                                 canary_notional_budget=10_000.0))
        refused = self.router.route(OrderSignal("capped", "AAPL", 100, 0.0, "BUY"))
        self.assertIs(refused.action, RoutingAction.REJECTED)
        self.assertEqual(refused.binding_constraint, "signal_validation")


class TestAbsoluteExposureLimits(unittest.TestCase):
    def setUp(self):
        self.router = StrategyCanaryRouter()

    def test_per_order_notional_cap_reduces_quantity_further(self):
        # 5% of 1,000,000 shares at $100 is 50,000 shares = $5,000,000 —
        # a percentage alone is not an exposure cap. Cap it at $25,000: 250
        # shares, floored to the 100-share lot step.
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=100,
                                 max_canary_order_notional=25_000.0))

        decision = self.router.route(OrderSignal("s", "AAPL", 1_000_000, 100.0, "BUY"))

        self.assertIs(decision.action, RoutingAction.SCALED)
        self.assertEqual(decision.routed_quantity, 200)
        self.assertEqual(decision.notional, 20_000.0)
        self.assertEqual(decision.binding_constraint, "max_canary_order_notional")

    def test_per_order_cap_does_not_bind_when_scaling_is_smaller(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=1,
                                 max_canary_order_notional=25_000.0))

        decision = self.router.route(OrderSignal("s", "AAPL", 1000, 100.0, "BUY"))
        self.assertEqual(decision.routed_quantity, 50)
        self.assertEqual(decision.binding_constraint, "scale_factor")

    def test_order_below_venue_min_notional_is_rejected(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.05, min_lot_size=1,
                                 min_notional=1000.0))

        decision = self.router.route(OrderSignal("s", "AAPL", 100, 10.0, "BUY"))

        self.assertIs(decision.action, RoutingAction.REJECTED)
        self.assertEqual(decision.binding_constraint, "min_notional")

    def test_cumulative_budget_stops_routing_once_exhausted(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1,
                                 canary_notional_budget=300.0))
        signal = OrderSignal("s", "AAPL", 10, 100.0, "BUY")  # 1 share = $100

        actions = [self.router.route(signal).action for _ in range(4)]

        self.assertEqual(actions[:3], [RoutingAction.SCALED] * 3)
        self.assertIs(actions[3], RoutingAction.REJECTED)
        self.assertEqual(self.router.consumed_notional("s"), 300.0)

    def test_rejected_orders_do_not_consume_budget(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=100,
                                 canary_notional_budget=300.0))

        self.router.route(OrderSignal("s", "AAPL", 10, 100.0, "BUY"))  # below lot
        self.assertEqual(self.router.consumed_notional("s"), 0.0)

    def test_release_notional_credits_back_a_venue_rejection(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1,
                                 canary_notional_budget=300.0))
        decision = self.router.route(OrderSignal("s", "AAPL", 10, 100.0, "BUY"))

        self.router.release_notional("s", decision.notional)

        self.assertEqual(self.router.consumed_notional("s"), 0.0)

    def test_release_notional_never_goes_negative(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY))
        self.router.release_notional("s", 5000.0)
        self.assertEqual(self.router.consumed_notional("s"), 0.0)

    def test_budget_reset_requires_an_authoriser_and_is_recorded(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1,
                                 canary_notional_budget=300.0))
        self.router.route(OrderSignal("s", "AAPL", 10, 100.0, "BUY"))

        with self.assertRaises(CanaryConfigError):
            self.router.reset_canary_budget("s", authorised_by="  ")

        self.router.reset_canary_budget("s", authorised_by="risk.officer")

        self.assertEqual(self.router.consumed_notional("s"), 0.0)
        self.assertEqual(self.router.phase_history[-1]["action"], "reset_budget")
        self.assertEqual(self.router.phase_history[-1]["authorised_by"], "risk.officer")

    def test_budget_is_never_exceeded_under_concurrent_routing(self):
        self.router.register_strategy(
            StrategyRegistration("s", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1,
                                 canary_notional_budget=1000.0))
        accepted = []
        lock = threading.Lock()

        def worker():
            for _ in range(10):
                decision = self.router.route(OrderSignal("s", "AAPL", 10, 100.0, "BUY"))
                if decision.is_live:
                    with lock:
                        accepted.append(decision.notional)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(accepted), 1000.0)
        self.assertEqual(len(accepted), 10)
        self.assertEqual(self.router.consumed_notional("s"), 1000.0)


class TestPhaseTransitions(unittest.TestCase):
    def setUp(self):
        self.router = StrategyCanaryRouter()
        self.router.register_strategy(StrategyRegistration("s", DeploymentPhase.SHADOW))

    def test_promotion_advances_one_phase_at_a_time(self):
        self.router.set_phase("s", DeploymentPhase.CANARY, authorised_by="head.of.trading")
        self.assertIs(self.router.get_phase("s"), DeploymentPhase.CANARY)

        self.router.set_phase("s", DeploymentPhase.PRODUCTION, authorised_by="head.of.trading")
        self.assertIs(self.router.get_phase("s"), DeploymentPhase.PRODUCTION)

    def test_shadow_to_production_jump_is_refused_and_recorded(self):
        with self.assertRaises(CanaryConfigError):
            self.router.set_phase("s", DeploymentPhase.PRODUCTION, authorised_by="someone")

        self.assertIs(self.router.get_phase("s"), DeploymentPhase.SHADOW)
        refusal = self.router.phase_history[-1]
        self.assertEqual(refusal["action"], "set_phase_refused")
        self.assertEqual(refusal["authorised_by"], "someone")

    def test_forced_jump_is_permitted_but_flagged(self):
        self.router.set_phase("s", DeploymentPhase.PRODUCTION,
                              authorised_by="cto", force=True)

        self.assertIs(self.router.get_phase("s"), DeploymentPhase.PRODUCTION)
        self.assertTrue(self.router.phase_history[-1]["forced"])

    def test_demotion_is_always_allowed_without_force(self):
        self.router.set_phase("s", DeploymentPhase.CANARY, authorised_by="ops")
        self.router.set_phase("s", DeploymentPhase.PRODUCTION, authorised_by="ops")

        self.router.set_phase("s", DeploymentPhase.SHADOW, authorised_by="ops")

        self.assertIs(self.router.get_phase("s"), DeploymentPhase.SHADOW)
        self.assertFalse(self.router.phase_history[-1]["forced"])

    def test_phase_change_requires_a_named_authoriser(self):
        for bad in ("", "   "):
            with self.subTest(authorised_by=bad):
                with self.assertRaises(CanaryConfigError):
                    self.router.set_phase("s", DeploymentPhase.CANARY, authorised_by=bad)

    def test_phase_change_on_unregistered_strategy_raises(self):
        with self.assertRaises(KeyError):
            self.router.set_phase("ghost", DeploymentPhase.CANARY, authorised_by="ops")

    def test_demotion_stops_new_orders_immediately(self):
        self.router.register_strategy(
            StrategyRegistration("live", DeploymentPhase.PRODUCTION))
        self.assertIsNotNone(
            self.router.route_order(OrderSignal("live", "AAPL", 100, 10.0, "BUY")))

        self.router.set_phase("live", DeploymentPhase.SHADOW, authorised_by="ops")

        self.assertIsNone(
            self.router.route_order(OrderSignal("live", "AAPL", 100, 10.0, "BUY")))

    def test_promotion_to_canary_starts_a_fresh_budget(self):
        self.router.register_strategy(
            StrategyRegistration("c", DeploymentPhase.CANARY,
                                 canary_scale_factor=0.10, min_lot_size=1,
                                 canary_notional_budget=300.0))
        self.router.route(OrderSignal("c", "AAPL", 10, 100.0, "BUY"))
        self.assertEqual(self.router.consumed_notional("c"), 100.0)

        self.router.set_phase("c", DeploymentPhase.SHADOW, authorised_by="ops")
        self.router.set_phase("c", DeploymentPhase.CANARY, authorised_by="ops")

        self.assertEqual(self.router.consumed_notional("c"), 0.0)

    def test_history_is_a_copy(self):
        history = self.router.phase_history
        history.append({"action": "forged"})
        self.assertEqual(len(self.router.phase_history), 1)


if __name__ == '__main__':
    unittest.main()
