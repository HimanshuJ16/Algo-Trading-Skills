import random
import unittest

from iceberg_order_native_broker_support_vs_simulation import (
    BrokerVenueConfig,
    DisplayRandomization,
    ExecutionMode,
    IcebergExecutionRouterEngine,
    IcebergOrderRequest,
    IcebergSupport,
)


def nasdaq_like(**overrides):
    """Native venue with a one-round-lot minimum display (Nasdaq Rule 4703(h))."""
    kwargs = dict(
        broker_name="NASDAQ_NATIVE",
        iceberg_support=IcebergSupport.NATIVE_EXCHANGE,
        native_parameter_name="displaySize",
        min_display_quantity=100,
        lot_size=100,
    )
    kwargs.update(overrides)
    return BrokerVenueConfig(**kwargs)


def rest_broker(**overrides):
    """Venue with no iceberg support at all: the client must slice."""
    kwargs = dict(
        broker_name="RETAIL_REST_BROKER",
        iceberg_support=IcebergSupport.UNSUPPORTED,
        min_display_quantity=100,
        lot_size=100,
        client_refill_round_trip_ms=30.0,
    )
    kwargs.update(overrides)
    return BrokerVenueConfig(**kwargs)


class TestNativeRouting(unittest.TestCase):
    def setUp(self):
        self.engine = IcebergExecutionRouterEngine(seed=11)

    def test_native_venue_prepares_single_parent_payload(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), nasdaq_like()
        )
        self.assertIs(report.execution_mode, ExecutionMode.NATIVE_ICEBERG)
        self.assertEqual(report.status, "NATIVE_PAYLOAD_PREPARED")
        self.assertEqual(report.planned_slice_count, 1)
        self.assertEqual(report.planned_child_slices, [])
        self.assertEqual(report.native_order_parameters["displaySize"], 500)
        self.assertEqual(report.native_order_parameters["quantity"], 10_000)

    def test_native_refill_still_loses_time_priority(self):
        """Regression: the engine previously claimed native refills kept queue priority.

        CME Globex places a refreshed Display Quantity order at the end of the queue;
        Nasdaq Rule 4703(h) stamps the replenished portion with a new timestamp; T7
        enters the new peak with a new timestamp behind same-limit orders.
        """
        for support in (IcebergSupport.NATIVE_EXCHANGE, IcebergSupport.BROKER_SIMULATED):
            with self.subTest(support=support):
                report = self.engine.route_iceberg_order(
                    IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00),
                    nasdaq_like(iceberg_support=support),
                )
                self.assertTrue(report.loses_time_priority_on_refill)

    def test_venue_side_refill_latency_is_none_not_zero(self):
        """Regression: 0.0 ms was previously reported and read as 'instant refill'."""
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), nasdaq_like()
        )
        self.assertIsNone(report.client_refill_latency_ms_total)

    def test_broker_simulated_is_distinguished_from_native(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00),
            nasdaq_like(iceberg_support=IcebergSupport.BROKER_SIMULATED),
        )
        self.assertIs(report.execution_mode, ExecutionMode.BROKER_SIMULATED_ICEBERG)
        self.assertEqual(report.status, "BROKER_SIMULATED_PAYLOAD_PREPARED")
        self.assertTrue(any("rests at the broker" in w for w in report.warnings))

    def test_estimated_refills_assume_fixed_peak(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), nasdaq_like()
        )
        self.assertEqual(report.estimated_display_refills, 19)  # ceil(10000/500) - 1

    def test_native_randomization_flagged_only_where_supported(self):
        fixed = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), nasdaq_like()
        )
        self.assertIs(fixed.display_randomization, DisplayRandomization.NONE)
        self.assertTrue(any("is ignored" in w for w in fixed.warnings))

        t7 = self.engine.route_iceberg_order(
            IcebergOrderRequest("SAP", "BUY", 10_000, 500, 150.00),
            nasdaq_like(
                broker_name="XETRA_T7",
                native_parameter_name="MaxShow",
                supports_native_display_randomization=True,
            ),
        )
        self.assertIs(t7.display_randomization, DisplayRandomization.NATIVE_EXCHANGE)


class TestVenueConstraints(unittest.TestCase):
    def setUp(self):
        self.engine = IcebergExecutionRouterEngine(seed=11)

    def test_mixed_lot_display_rounds_down_to_round_lot(self):
        """Nasdaq Rule 4703(h): a mixed-lot displayed size is rounded down."""
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 450, 150.00), nasdaq_like()
        )
        self.assertEqual(report.effective_display_quantity, 400)
        self.assertEqual(report.native_order_parameters["displaySize"], 400)
        self.assertTrue(any("Display quantity adjusted" in w for w in report.warnings))

    def test_sub_minimum_display_is_raised_to_venue_minimum(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 40, 150.00), nasdaq_like()
        )
        self.assertEqual(report.effective_display_quantity, 100)

    def test_gtc_only_iceberg_rejects_other_time_in_force(self):
        """Binance spot: 'Any order with an icebergQty MUST have timeInForce set to GTC'."""
        binance = BrokerVenueConfig(
            "BINANCE_SPOT",
            IcebergSupport.NATIVE_EXCHANGE,
            native_parameter_name="icebergQty",
            requires_gtc_for_iceberg=True,
        )
        with self.assertRaises(ValueError):
            self.engine.route_iceberg_order(
                IcebergOrderRequest("BTCUSDT", "BUY", 100, 10, 50_000.0, time_in_force="IOC"),
                binance,
            )
        ok = self.engine.route_iceberg_order(
            IcebergOrderRequest("BTCUSDT", "BUY", 100, 10, 50_000.0), binance
        )
        self.assertEqual(ok.native_order_parameters["timeInForce"], "GTC")

    def test_native_parameter_name_required_for_supported_venues(self):
        with self.assertRaises(ValueError):
            BrokerVenueConfig("X", IcebergSupport.NATIVE_EXCHANGE)

    def test_display_covering_whole_order_is_not_an_iceberg(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 500, 500, 150.00), nasdaq_like()
        )
        self.assertIs(report.execution_mode, ExecutionMode.STANDARD_LIMIT_ORDER)
        self.assertFalse(report.loses_time_priority_on_refill)
        self.assertEqual(report.estimated_display_refills, 0)


class TestSyntheticPlanning(unittest.TestCase):
    def setUp(self):
        self.engine = IcebergExecutionRouterEngine(seed=11)

    def test_synthetic_plan_conserves_parent_quantity(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00), rest_broker()
        )
        self.assertIs(report.execution_mode, ExecutionMode.SYNTHETIC_SIMULATION)
        self.assertEqual(sum(s.slice_quantity for s in report.planned_child_slices), 2_000)

    def test_tail_below_minimum_display_is_merged_not_emitted(self):
        """Regression: a lone odd-lot residual advertises the parent and its exhaustion.

        Also guards the quantity-conservation bug in the merge itself: reading
        ``quantities[-2]`` before popping and storing after it credits the wrong slot.
        """
        for seed in range(40):
            engine = IcebergExecutionRouterEngine(seed=seed)
            report = engine.route_iceberg_order(
                IcebergOrderRequest("AAPL", "BUY", 2_050, 500, 150.00), rest_broker()
            )
            quantities = [s.slice_quantity for s in report.planned_child_slices]
            with self.subTest(seed=seed):
                self.assertEqual(sum(quantities), 2_050)
                self.assertGreaterEqual(quantities[-1], 100)

    def test_slices_respect_randomization_band_and_lot_size(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00, slice_randomization_pct=0.20),
            rest_broker(),
        )
        interior = [s.slice_quantity for s in report.planned_child_slices[:-1]]
        self.assertTrue(interior)
        for qty in interior:
            self.assertGreaterEqual(qty, 400)
            self.assertLessEqual(qty, 600)
            self.assertEqual(qty % 100, 0)

    def test_child_slices_are_planned_not_filled(self):
        """Regression: slices were previously stamped FILLED before anything was sent."""
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00), rest_broker()
        )
        self.assertEqual({s.status for s in report.planned_child_slices}, {"PLANNED"})
        self.assertEqual(report.status, "SYNTHETIC_PLAN_PREPARED")

    def test_refill_latency_scales_with_refill_count(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00),
            rest_broker(client_refill_round_trip_ms=30.0),
        )
        refills = report.planned_slice_count - 1
        self.assertEqual(report.estimated_display_refills, refills)
        self.assertAlmostEqual(report.client_refill_latency_ms_total, refills * 30.0)

    def test_zero_latency_estimate_is_flagged_as_missing(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00),
            rest_broker(client_refill_round_trip_ms=0.0),
        )
        self.assertEqual(report.client_refill_latency_ms_total, 0.0)
        self.assertTrue(any("missing estimate" in w for w in report.warnings))

    def test_message_count_warning_is_always_raised(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00), rest_broker()
        )
        self.assertTrue(any("order-to-trade-ratio" in w for w in report.warnings))
        self.assertTrue(any("disconnect between slices" in w for w in report.warnings))

    def test_zero_randomization_emits_identical_slices_and_warns(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00, slice_randomization_pct=0.0),
            rest_broker(),
        )
        self.assertIs(report.display_randomization, DisplayRandomization.NONE)
        self.assertEqual([s.slice_quantity for s in report.planned_child_slices], [500] * 4)
        self.assertTrue(any("identical child sizes" in w for w in report.warnings))

    def test_collapsed_randomization_band_is_reported_as_none(self):
        """A non-zero pct still yields identical slices once lot/minimum snapping bites."""
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 1_000, 100, 150.00, slice_randomization_pct=0.20),
            rest_broker(min_display_quantity=100, lot_size=100),
        )
        quantities = [s.slice_quantity for s in report.planned_child_slices]
        self.assertEqual(set(quantities), {100})
        self.assertIs(report.display_randomization, DisplayRandomization.NONE)
        self.assertTrue(any("collapses to a single size" in w for w in report.warnings))

    def test_slice_schedule_is_reproducible_for_a_fixed_seed(self):
        def plan(seed):
            engine = IcebergExecutionRouterEngine(seed=seed)
            report = engine.route_iceberg_order(
                IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), rest_broker()
            )
            return [s.slice_quantity for s in report.planned_child_slices]

        self.assertEqual(plan(42), plan(42))
        self.assertNotEqual(plan(42), plan(43))

    def test_injected_rng_is_accepted(self):
        engine = IcebergExecutionRouterEngine(rng=random.Random(5))
        report = engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 2_000, 500, 150.00), rest_broker()
        )
        self.assertEqual(sum(s.slice_quantity for s in report.planned_child_slices), 2_000)

    def test_slice_count_ceiling_rejects_message_storms(self):
        engine = IcebergExecutionRouterEngine(seed=1, max_child_slices=50)
        with self.assertRaises(ValueError):
            engine.route_iceberg_order(
                IcebergOrderRequest("AAPL", "BUY", 1_000_000, 100, 150.00),
                rest_broker(min_display_quantity=1, lot_size=1),
            )

    def test_slice_ids_and_sequences_are_contiguous(self):
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 5_000, 500, 150.00), rest_broker()
        )
        sequences = [s.sequence for s in report.planned_child_slices]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertEqual(report.planned_child_slices[0].slice_id, "SYNTH_AAPL_001")


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = IcebergExecutionRouterEngine(seed=11)

    def _request(self, **overrides):
        kwargs = dict(
            symbol="AAPL",
            side="BUY",
            total_quantity=10_000,
            target_display_quantity=500,
            limit_price=150.00,
        )
        kwargs.update(overrides)
        return IcebergOrderRequest(**kwargs)

    def test_invalid_side_rejected(self):
        with self.assertRaises(ValueError):
            self._request(side="LONG")

    def test_non_positive_quantities_rejected(self):
        for field_name in ("total_quantity", "target_display_quantity"):
            with self.subTest(field=field_name), self.assertRaises(ValueError):
                self._request(**{field_name: 0})

    def test_non_integer_quantity_rejected(self):
        with self.assertRaises(TypeError):
            self._request(total_quantity=1000.5)

    def test_non_finite_or_non_positive_limit_price_rejected(self):
        for price in (float("nan"), float("inf"), 0.0, -1.0):
            with self.subTest(price=price), self.assertRaises(ValueError):
                self._request(limit_price=price)

    def test_randomization_pct_outside_unit_interval_rejected(self):
        for pct in (-0.01, 1.0, 1.5, float("nan")):
            with self.subTest(pct=pct), self.assertRaises(ValueError):
                self._request(slice_randomization_pct=pct)

    def test_empty_symbol_rejected(self):
        with self.assertRaises(ValueError):
            self._request(symbol="   ")

    def test_seed_and_rng_are_mutually_exclusive(self):
        with self.assertRaises(ValueError):
            IcebergExecutionRouterEngine(seed=1, rng=random.Random(1))

    def test_venue_rejects_invalid_lot_and_latency(self):
        with self.assertRaises(ValueError):
            BrokerVenueConfig("X", IcebergSupport.UNSUPPORTED, lot_size=0)
        with self.assertRaises(ValueError):
            BrokerVenueConfig("X", IcebergSupport.UNSUPPORTED, client_refill_round_trip_ms=-1.0)
        with self.assertRaises(ValueError):
            BrokerVenueConfig("X", IcebergSupport.UNSUPPORTED, min_display_quantity=0)

    def test_valid_support_string_is_coerced_to_enum(self):
        """Routing compares with ``is``; a bare string must not silently miss every branch."""
        venue = BrokerVenueConfig("X", "NATIVE_EXCHANGE", native_parameter_name="displaySize")
        self.assertIs(venue.iceberg_support, IcebergSupport.NATIVE_EXCHANGE)
        report = self.engine.route_iceberg_order(
            IcebergOrderRequest("AAPL", "BUY", 10_000, 500, 150.00), venue
        )
        self.assertIs(report.execution_mode, ExecutionMode.NATIVE_ICEBERG)

    def test_unknown_support_string_rejected(self):
        with self.assertRaises(ValueError):
            BrokerVenueConfig("X", "PARTIAL_SUPPORT", native_parameter_name="displaySize")

    def test_venue_rejects_bare_boolean_support_flag(self):
        """The old API took ``supports_native_iceberg: bool``; True must not silently pass."""
        with self.assertRaises(TypeError):
            BrokerVenueConfig("X", True, native_parameter_name="displaySize")


if __name__ == "__main__":
    unittest.main()
