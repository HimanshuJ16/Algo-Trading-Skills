"""
Unit tests for broker-order-type-capability-matrix skill.

The assertions that matter most here are the ones about *quantity conservation*
and *price geometry*: a plan that over-executes the parent quantity by one slice,
or that registers exit legs already through their triggers, is accepted by every
type checker and loses money on the first live order.
"""
import json
import unittest
from decimal import Decimal

from capability_matrix import (
    DEFAULT_CAPABILITIES,
    BrokerCapabilities,
    BrokerOrderCapabilityMatrix,
    OrderType,
)


class TestBrokerOrderCapabilityMatrix(unittest.TestCase):

    def setUp(self):
        self.matrix = BrokerOrderCapabilityMatrix()

    # --- Registry ---

    def test_native_capability_checks(self):
        # IBKR: bracket + OCA groups, iceberg via displaySize, TWAP/VWAP IBALGOs.
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.BRACKET))
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.ICEBERG))
        self.assertTrue(self.matrix.supports_native("ibkr", OrderType.TWAP))

        # Alpaca: bracket/OCO order classes, but no iceberg and no execution algos.
        self.assertTrue(self.matrix.supports_native("alpaca", OrderType.BRACKET))
        self.assertFalse(self.matrix.supports_native("alpaca", OrderType.ICEBERG))
        self.assertFalse(self.matrix.supports_native("alpaca", OrderType.TWAP))

        # Zerodha: native iceberg variety, but bracket orders were withdrawn.
        self.assertTrue(self.matrix.supports_native("zerodha", OrderType.ICEBERG))
        self.assertFalse(self.matrix.supports_native("zerodha", OrderType.BRACKET))

        # Binance spot: OTOCO order lists are a native bracket; VWAP is not offered.
        self.assertTrue(self.matrix.supports_native("binance", OrderType.BRACKET))
        self.assertTrue(self.matrix.supports_native("binance", OrderType.OCO))
        self.assertFalse(self.matrix.supports_native("binance", OrderType.VWAP))

    def test_broker_name_lookup_is_case_and_space_insensitive(self):
        self.assertTrue(self.matrix.supports_native("  IBKR ", OrderType.BRACKET))

    def test_custom_broker_registration(self):
        custom_broker = BrokerCapabilities(
            broker_name="custom_broker",
            native_order_types={OrderType.MARKET, OrderType.LIMIT},
            supports_fractional=False,
        )
        self.matrix.register_broker(custom_broker)
        self.assertTrue(self.matrix.supports_native("custom_broker", OrderType.MARKET))
        self.assertFalse(self.matrix.supports_native("custom_broker", OrderType.BRACKET))

    def test_capability_flags_contradicting_order_types_are_rejected(self):
        # Claiming native OCO while omitting OrderType.OCO reads as supported
        # everywhere a human looks, yet still routes through the emulation path.
        with self.assertRaises(ValueError):
            BrokerCapabilities(
                broker_name="inconsistent",
                native_order_types={OrderType.MARKET},
                supports_oco=True,
            )
        with self.assertRaises(ValueError):
            BrokerCapabilities(
                broker_name="inconsistent",
                native_order_types={OrderType.ICEBERG},
                supports_iceberg=False,
            )

    def test_empty_custom_matrix_is_honoured(self):
        # `custom_matrix or DEFAULT_CAPABILITIES` would restore every default broker
        # for a caller who passed {} precisely so that none would resolve.
        empty = BrokerOrderCapabilityMatrix(custom_matrix={})
        self.assertEqual(empty.matrix, {})
        self.assertFalse(empty.supports_native("ibkr", OrderType.MARKET))
        with self.assertRaises(ValueError):
            empty.plan_order_execution("ibkr", OrderType.MARKET, "AAPL", "BUY", 1)

    def test_registry_is_isolated_from_module_defaults(self):
        other = BrokerOrderCapabilityMatrix()
        self.matrix.matrix["ibkr"].native_order_types.discard(OrderType.BRACKET)
        self.assertTrue(other.supports_native("ibkr", OrderType.BRACKET))
        self.assertIn(OrderType.BRACKET, DEFAULT_CAPABILITIES["ibkr"].native_order_types)

    def test_supports_native_rejects_a_raw_string_order_type(self):
        # A string silently compares unequal to every enum member, reporting the
        # broker as supporting nothing.
        with self.assertRaises(TypeError):
            self.matrix.supports_native("ibkr", "BRACKET")

    def test_supports_native_is_false_for_unknown_broker(self):
        self.assertFalse(self.matrix.supports_native("nope", OrderType.MARKET))

    # --- Native routing ---

    def test_native_order_plan(self):
        plan = self.matrix.plan_order_execution(
            "ibkr", OrderType.BRACKET, "AAPL", "BUY", 100,
            price=190.0, stop_loss_price=185.0, take_profit_price=200.0,
        )
        self.assertTrue(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.BRACKET)
        self.assertEqual(plan.primary_quantity, Decimal("100"))
        self.assertEqual(plan.symbol, "AAPL")
        self.assertEqual(len(plan.emulated_legs), 0)
        self.assertTrue(plan.has_primary_order)

    def test_native_bracket_without_exits_is_rejected(self):
        # A bracket with neither exit leg is not a bracket, and the venue accepting
        # it natively does not make it one.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "ibkr", OrderType.BRACKET, "AAPL", "BUY", 100
            )

    def test_native_path_validates_price_geometry(self):
        # Regression: geometry used to be checked only inside the synthesizer, so an
        # inverted bracket sailed through whenever the broker supported it natively.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "ibkr", OrderType.BRACKET, "AAPL", "BUY", 100,
                price=190.0, stop_loss_price=200.0, take_profit_price=185.0,
            )

    # --- Emulated BRACKET ---

    def test_synthesized_bracket_order_plan(self):
        # Zerodha withdrew bracket orders; the exits must be managed locally.
        plan = self.matrix.plan_order_execution(
            broker_name="zerodha",
            requested_order_type=OrderType.BRACKET,
            symbol="INFY",
            action="BUY",
            quantity=100,
            price=1500.0,
            stop_loss_price=1450.0,
            take_profit_price=1600.0,
        )

        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(plan.primary_price, Decimal("1500.0"))
        self.assertEqual(plan.primary_quantity, Decimal("100"))
        self.assertEqual(len(plan.emulated_legs), 2)

        sl_leg = next(l for l in plan.emulated_legs if l.leg_type == "STOP_LOSS")
        tp_leg = next(l for l in plan.emulated_legs if l.leg_type == "TAKE_PROFIT")

        # `action` is the entry side for a bracket; both exits invert it.
        self.assertEqual(sl_leg.action, "SELL")
        self.assertEqual(tp_leg.action, "SELL")
        self.assertEqual(sl_leg.trigger_price, Decimal("1450.0"))
        self.assertEqual(tp_leg.limit_price, Decimal("1600.0"))
        # Exits must not be armed before the entry fills, and only one may run.
        self.assertEqual(sl_leg.metadata["activate_on"], "PRIMARY_FILL")
        self.assertTrue(tp_leg.metadata["mutually_exclusive"])

    def test_synthesized_bracket_short_entry_inverts_geometry(self):
        plan = self.matrix.plan_order_execution(
            "zerodha", OrderType.BRACKET, "INFY", "SELL", 100,
            price=1500.0, stop_loss_price=1550.0, take_profit_price=1400.0,
        )
        self.assertEqual(plan.emulated_legs[0].action, "BUY")

        # The same prices on a long entry are inverted and must be refused.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
                price=1500.0, stop_loss_price=1550.0, take_profit_price=1400.0,
            )

    def test_bracket_stop_on_wrong_side_of_entry_is_rejected(self):
        # Stop above a long entry: the leg is already through its trigger.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
                price=1500.0, stop_loss_price=1510.0,
            )

    def test_bracket_market_entry_still_checks_relative_geometry(self):
        # No reference price, but the two exits still cannot cross each other.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
                stop_loss_price=1600.0, take_profit_price=1450.0,
            )
        plan = self.matrix.plan_order_execution(
            "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
            stop_loss_price=1450.0, take_profit_price=1600.0,
        )
        self.assertEqual(plan.primary_order_type, OrderType.MARKET)
        self.assertIsNone(plan.primary_price)

    # --- Emulated OCO ---

    def test_synthesized_oco_has_no_primary_order(self):
        # Regression: the plan used to name OrderType.OCO as the primary type, which
        # tells the caller to fire a native OCO at the broker that has none.
        plan = self.matrix.plan_order_execution(
            "zerodha", OrderType.OCO, "INFY", "SELL", 100,
            stop_loss_price=1450.0, take_profit_price=1600.0,
        )
        self.assertFalse(plan.is_native)
        self.assertIsNone(plan.primary_order_type)
        self.assertFalse(plan.has_primary_order)
        self.assertEqual(plan.primary_quantity, Decimal("0"))
        self.assertEqual(len(plan.emulated_legs), 2)
        # Native OCO implementations require both legs to share a side; so does this.
        self.assertEqual({l.action for l in plan.emulated_legs}, {"SELL"})

    def test_oco_geometry_is_mirrored_relative_to_bracket(self):
        # A BUY OCO closes a short: the target sits *below* the stop. The same pair
        # of prices that is valid for a BUY bracket entry is invalid here.
        plan = self.matrix.plan_order_execution(
            "zerodha", OrderType.OCO, "INFY", "BUY", 100,
            stop_loss_price=1600.0, take_profit_price=1450.0,
        )
        self.assertEqual({l.action for l in plan.emulated_legs}, {"BUY"})

        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.OCO, "INFY", "BUY", 100,
                stop_loss_price=1450.0, take_profit_price=1600.0,
            )

    def test_oco_requires_both_prices(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.OCO, "INFY", "SELL", 100, stop_loss_price=1450.0
            )

    # --- Emulated ICEBERG ---

    def test_synthesized_iceberg_order_plan(self):
        plan = self.matrix.plan_order_execution(
            "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500,
            price=190.0, iceberg_slices=10,
        )
        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(plan.primary_price, Decimal("190.0"))
        self.assertEqual(len(plan.emulated_legs), 1)

        leg = plan.emulated_legs[0]
        self.assertEqual(leg.leg_type, "SLICE_FEEDER")
        self.assertEqual(leg.slice_qty, Decimal("50"))
        self.assertEqual(leg.limit_price, Decimal("190.0"))
        # The feeder covers what the primary slice did not: 500 - 50.
        self.assertEqual(leg.quantity, Decimal("450"))
        self.assertEqual(leg.metadata["remaining_slices"], 9)

    def test_iceberg_requires_a_limit_price(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500, iceberg_slices=10
            )

    def test_iceberg_rejects_degenerate_slice_counts(self):
        for bad in (0, 1, -3, 2.5, True):
            with self.subTest(slices=bad):
                with self.assertRaises(ValueError):
                    self.matrix.plan_order_execution(
                        "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500,
                        price=190.0, iceberg_slices=bad,
                    )

    def test_iceberg_respects_a_venue_minimum_slice_size(self):
        # 500 shares in 10 slices is 50 a slice; a venue floor of 100 makes every
        # slice a guaranteed rejection, so the plan must not be produced at all.
        with self.assertRaises(ValueError) as ctx:
            self.matrix.plan_order_execution(
                "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500,
                price=190.0, iceberg_slices=10, min_slice_qty=100,
            )
        self.assertIn("iceberg_slices <= 5", str(ctx.exception))

        # At or above the floor the same request plans normally.
        plan = self.matrix.plan_order_execution(
            "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 500,
            price=190.0, iceberg_slices=5, min_slice_qty=100,
        )
        self.assertEqual(plan.primary_quantity, Decimal("100"))

    # --- Emulated TWAP ---

    def test_synthesized_twap_order_plan(self):
        plan = self.matrix.plan_order_execution(
            "alpaca", OrderType.TWAP, "MSFT", "SELL", 1000, twap_duration_minutes=120
        )
        self.assertFalse(plan.is_native)
        self.assertEqual(plan.primary_order_type, OrderType.MARKET)
        self.assertEqual(len(plan.emulated_legs), 1)

        leg = plan.emulated_legs[0]
        self.assertEqual(leg.leg_type, "TWAP_FEEDER")
        self.assertEqual(leg.slice_qty, Decimal("100"))  # 1000 over 10 slices
        self.assertEqual(leg.interval_seconds, 720)      # 120 min = 7200 s, / 10
        self.assertEqual(leg.quantity, Decimal("900"))   # feeder holds 9 of 10 slices
        self.assertEqual(leg.metadata["requested_duration_seconds"], 7200)
        self.assertEqual(leg.metadata["effective_span_seconds"], 6480)  # 9 * 720

    def test_twap_slice_count_is_configurable(self):
        plan = self.matrix.plan_order_execution(
            "alpaca", OrderType.TWAP, "MSFT", "SELL", 1000,
            twap_duration_minutes=60, twap_slices=4,
        )
        leg = plan.emulated_legs[0]
        self.assertEqual(leg.metadata["total_slices"], 4)
        self.assertEqual(leg.interval_seconds, 900)  # 3600 s / 4
        self.assertEqual(leg.slice_qty, Decimal("250"))

    def test_twap_with_price_sends_limit_slices(self):
        plan = self.matrix.plan_order_execution(
            "alpaca", OrderType.TWAP, "MSFT", "SELL", 1000,
            price=400.0, twap_duration_minutes=60,
        )
        self.assertEqual(plan.primary_order_type, OrderType.LIMIT)
        self.assertEqual(plan.primary_price, Decimal("400.0"))
        self.assertEqual(plan.emulated_legs[0].limit_price, Decimal("400.0"))

    def test_twap_rejects_a_sub_second_interval(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "alpaca", OrderType.TWAP, "MSFT", "SELL", 1000,
                twap_duration_minutes=1, twap_slices=120,
            )

    def test_twap_rejects_degenerate_durations(self):
        for bad in (0, -30, 1.5):
            with self.subTest(duration=bad):
                with self.assertRaises(ValueError):
                    self.matrix.plan_order_execution(
                        "alpaca", OrderType.TWAP, "MSFT", "SELL", 1000,
                        twap_duration_minutes=bad,
                    )

    # --- Quantity conservation ---

    def test_sliced_plans_conserve_the_parent_quantity_exactly(self):
        # Regression: the feeder used to carry the *full* parent quantity alongside a
        # primary slice, so an EMS following the documented contract executed
        # quantity + one slice. Indivisible quantities also exposed float residue.
        cases = [
            (OrderType.ICEBERG, Decimal("100"), {"price": 50.0, "iceberg_slices": 3}),
            (OrderType.ICEBERG, Decimal("1.5"), {"price": 50.0, "iceberg_slices": 7}),
            (OrderType.TWAP, Decimal("1000"), {"twap_slices": 7}),
            (OrderType.TWAP, Decimal("0.3"), {"twap_slices": 3}),
        ]
        for order_type, quantity, kwargs in cases:
            with self.subTest(order_type=order_type, quantity=quantity):
                plan = self.matrix.plan_order_execution(
                    "alpaca", order_type, "AAPL", "BUY", quantity, **kwargs
                )
                leg = plan.emulated_legs[0]
                schedule = leg.metadata["slice_schedule"]

                self.assertEqual(plan.primary_quantity + sum(schedule), quantity)
                self.assertEqual(sum(schedule), leg.quantity)
                self.assertEqual(len(schedule) + 1, leg.metadata["total_slices"])
                self.assertTrue(all(part > 0 for part in schedule))

    def test_quantity_too_precise_to_slice_is_refused(self):
        # A schedule that cannot sum back to its parent at the active decimal
        # precision is the wrong order size, not a rounding detail.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "alpaca", OrderType.TWAP, "X", "BUY",
                Decimal("123456789012345678901234567890"), twap_slices=7,
            )

    def test_slice_floor_above_the_parent_quantity_says_so(self):
        # "Use iceberg_slices <= 0" is not actionable advice.
        with self.assertRaises(ValueError) as ctx:
            self.matrix.plan_order_execution(
                "alpaca", OrderType.ICEBERG, "AAPL", "BUY", 5,
                price=10.0, iceberg_slices=2, min_slice_qty=100,
            )
        self.assertIn("cannot be sliced at all", str(ctx.exception))

    # --- Input validation ---

    def test_arguments_the_order_type_ignores_are_refused(self):
        # Silently dropping stop_loss_price leaves the caller believing a position is
        # protected while nothing is watching it.
        with self.assertRaises(ValueError) as ctx:
            self.matrix.plan_order_execution(
                "ibkr", OrderType.MARKET, "AAPL", "BUY", 10, stop_loss_price=180.0
            )
        self.assertIn("stop_loss_price", str(ctx.exception))

        for kwargs in (
            {"iceberg_slices": 3},
            {"twap_slices": 4},
            {"twap_duration_minutes": 30},
            {"min_slice_qty": 1},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    self.matrix.plan_order_execution(
                        "ibkr", OrderType.MARKET, "AAPL", "BUY", 10, **kwargs
                    )

        # A TWAP does not take iceberg_slices, and must not quietly plan 10 anyway.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "alpaca", OrderType.TWAP, "AAPL", "BUY", 100, iceberg_slices=99
            )


    def test_invalid_broker(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "unknown_broker", OrderType.MARKET, "AAPL", "BUY", 100
            )

    def test_bracket_missing_prices(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.BRACKET, "INFY", "BUY", 100
            )

    def test_rejects_non_positive_and_non_finite_quantities(self):
        for bad in (0, -5, float("nan"), float("inf"), "100", None, True):
            with self.subTest(quantity=bad):
                with self.assertRaises(ValueError):
                    self.matrix.plan_order_execution(
                        "ibkr", OrderType.MARKET, "AAPL", "BUY", bad
                    )

    def test_zero_price_is_rejected_not_treated_as_absent(self):
        # `if not stop_loss_price` reads 0 as "not supplied" and plans a bracket with
        # a silently missing leg.
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution(
                "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
                stop_loss_price=0, take_profit_price=1600.0,
            )

    def test_rejects_blank_symbol_and_bad_action(self):
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution("ibkr", OrderType.MARKET, "   ", "BUY", 10)
        with self.assertRaises(ValueError):
            self.matrix.plan_order_execution("ibkr", OrderType.MARKET, "AAPL", "LONG", 10)

    def test_action_is_normalized(self):
        plan = self.matrix.plan_order_execution(
            "ibkr", OrderType.MARKET, "AAPL", " buy ", 10
        )
        self.assertEqual(plan.primary_action, "BUY")

    def test_non_emulatable_types_raise_an_actionable_error(self):
        # VWAP needs a live volume forecast this planner does not have; approximating
        # it as "TWAP with extra steps" would be a silently different algorithm.
        for order_type in (OrderType.VWAP, OrderType.PEGGED, OrderType.TRAILING_STOP):
            with self.subTest(order_type=order_type):
                with self.assertRaises(ValueError) as ctx:
                    self.matrix.plan_order_execution(
                        "zerodha", order_type, "INFY", "BUY", 100
                    )
                self.assertIn("cannot emulate", str(ctx.exception))

    def test_requested_order_type_must_be_an_enum_member(self):
        with self.assertRaises(TypeError):
            self.matrix.plan_order_execution("ibkr", "MARKET", "AAPL", "BUY", 100)

    # --- Persistence ---

    def test_plan_serializes_to_json_for_ems_restart(self):
        # If the EMS loses its emulated legs, the stop losses simply never fire.
        plan = self.matrix.plan_order_execution(
            "zerodha", OrderType.BRACKET, "INFY", "BUY", 100,
            price=1500.0, stop_loss_price=1450.0, take_profit_price=1600.0,
        )
        restored = json.loads(json.dumps(plan.to_dict()))
        self.assertEqual(restored["symbol"], "INFY")
        self.assertEqual(restored["primary_order_type"], "LIMIT")
        self.assertEqual(Decimal(restored["primary_quantity"]), Decimal("100"))
        legs = {leg["leg_type"]: leg for leg in restored["emulated_legs"]}
        self.assertEqual(Decimal(legs["STOP_LOSS"]["trigger_price"]), Decimal("1450.0"))
        self.assertEqual(Decimal(legs["TAKE_PROFIT"]["limit_price"]), Decimal("1600.0"))


if __name__ == "__main__":
    unittest.main()
