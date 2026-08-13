import unittest
from datetime import datetime, time, timedelta, timezone

from auction_only_order_types_for_illiquid_names import (
    CLOSING_AUCTION_CUTOFF_ET,
    IlliquidAuctionExecutionEngine,
    IlliquidExecutionConfig,
    OrderType,
    is_past_closing_auction_cutoff,
    validate_submission_window,
)


# A fixed US/Eastern offset for deterministic testing. Using a fixed offset
# avoids depending on zoneinfo tzdata availability across platforms.
_ET = timezone(timedelta(hours=-5))


class TestIlliquidAuctionExecutionEngine(unittest.TestCase):
    def setUp(self):
        self.engine = IlliquidAuctionExecutionEngine()

    def test_severe_illiquidity_100pct_loc(self):
        # Order is 10% of ADV (> 5% threshold)
        plan = self.engine.generate_routing_plan("MICROCAP", total_qty=10000, average_daily_volume=100000)

        self.assertEqual(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_qty, 10000)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Severe", plan.reason)

    def test_moderate_illiquidity_hybrid(self):
        # Order is 2.5% of ADV (between 1% and 5%)
        plan = self.engine.generate_routing_plan("MIDCAP", total_qty=25000, average_daily_volume=1000000)

        self.assertEqual(plan.continuous_qty, 12500)  # 50%
        self.assertEqual(plan.auction_qty, 12500)  # 50%
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)
        self.assertIn("Moderate", plan.reason)

    def test_liquid_100pct_continuous(self):
        # Order is 0.1% of ADV (< 1% threshold)
        plan = self.engine.generate_routing_plan("MEGA", total_qty=10000, average_daily_volume=10000000)

        self.assertEqual(plan.continuous_qty, 10000)
        self.assertEqual(plan.auction_qty, 0)
        # No auction order placed for liquid names; type reflects continuous strategy.
        self.assertEqual(plan.auction_order_type, OrderType.CONTINUOUS_VWAP)
        self.assertIsNone(plan.suggested_limit_price)
        self.assertIn("Liquid", plan.reason)

    def test_zero_adv_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000, average_daily_volume=0)

    def test_negative_adv_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("ERR", total_qty=1000, average_daily_volume=-5)

    def test_non_positive_qty_raises_error(self):
        for bad_qty in (0, -1):
            with self.subTest(qty=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.generate_routing_plan("ERR", total_qty=bad_qty, average_daily_volume=100000)

    def test_empty_symbol_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan("", total_qty=1000, average_daily_volume=100000)

    def test_hybrid_split_sums_to_total(self):
        # Odd quantity must still conserve shares across continuous + auction.
        total = 25001
        plan = self.engine.generate_routing_plan("ODD", total_qty=total, average_daily_volume=1000000)
        self.assertEqual(plan.continuous_qty + plan.auction_qty, total)

    def test_severe_threshold_boundary(self):
        # Exactly at the 5% severe boundary -> severe (>=).
        plan = self.engine.generate_routing_plan("EDGE", total_qty=5000, average_daily_volume=100000)
        self.assertEqual(plan.auction_qty, 5000)
        self.assertEqual(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)

    def test_moderate_threshold_boundary(self):
        # Exactly at the 1% moderate boundary -> hybrid (>=).
        plan = self.engine.generate_routing_plan("EDGE", total_qty=1000, average_daily_volume=100000)
        self.assertGreater(plan.auction_qty, 0)
        self.assertGreater(plan.continuous_qty, 0)
        self.assertEqual(plan.auction_order_type, OrderType.LIMIT_ON_CLOSE)

    # --- LOC limit-price derivation (LOC requires a limit price) ---

    def test_buy_limit_price_from_reference(self):
        # 50 bps default tolerance on a $20.00 reference -> 20.00 * 1.005 = 20.10
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=20.00, side="BUY",
        )
        self.assertIsNotNone(plan.suggested_limit_price)
        self.assertAlmostEqual(plan.suggested_limit_price, 20.10, places=4)

    def test_sell_limit_price_from_reference(self):
        # Sell: 20.00 * (1 - 0.005) = 19.90
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=20.00, side="SELL",
        )
        self.assertAlmostEqual(plan.suggested_limit_price, 19.90, places=4)

    def test_explicit_slippage_tolerance(self):
        # 100 bps on $100.00 buy -> 101.00
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
            reference_price=100.00, slippage_tolerance_bps=100.0, side="BUY",
        )
        self.assertAlmostEqual(plan.suggested_limit_price, 101.00, places=4)

    def test_no_reference_price_yields_none_limit(self):
        plan = self.engine.generate_routing_plan(
            "MICROCAP", total_qty=10000, average_daily_volume=100000,
        )
        # LOC order type but no suggested price; caller must set it before submit.
        self.assertIsNone(plan.suggested_limit_price)

    def test_invalid_reference_price_raises(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan(
                "MICROCAP", total_qty=10000, average_daily_volume=100000,
                reference_price=0.0,
            )

    def test_non_finite_reference_price_raises(self):
        import math
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(ref=bad):
                with self.assertRaises(ValueError):
                    self.engine.generate_routing_plan(
                        "MICROCAP", total_qty=10000, average_daily_volume=100000,
                        reference_price=bad,
                    )

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            self.engine.generate_routing_plan(
                "MICROCAP", total_qty=10000, average_daily_volume=100000,
                reference_price=20.0, side="HOLD",
            )

    # --- Config validation ---

    def test_invalid_config_threshold_order_raises(self):
        bad = IlliquidExecutionConfig(
            severe_illiquidity_threshold_pct=0.01,
            moderate_illiquidity_threshold_pct=0.05,
        )
        with self.assertRaises(ValueError):
            IlliquidAuctionExecutionEngine(bad)

    def test_invalid_config_allocation_raises(self):
        bad = IlliquidExecutionConfig(hybrid_auction_allocation_pct=1.5)
        with self.assertRaises(ValueError):
            IlliquidAuctionExecutionEngine(bad)

    # --- Cutoff enforcement ---

    def test_cutoff_before_deadline_ok(self):
        ts = datetime(2024, 3, 1, 15, 49, 59, tzinfo=_ET)
        self.assertFalse(is_past_closing_auction_cutoff(ts))
        validate_submission_window(ts)  # must not raise

    def test_cutoff_at_deadline_rejected(self):
        ts = datetime(2024, 3, 1, 15, 50, 0, tzinfo=_ET)
        self.assertTrue(is_past_closing_auction_cutoff(ts))
        with self.assertRaises(ValueError):
            validate_submission_window(ts)

    def test_cutoff_after_deadline_rejected(self):
        ts = datetime(2024, 3, 1, 15, 55, 0, tzinfo=_ET)
        self.assertTrue(is_past_closing_auction_cutoff(ts))

    def test_cutoff_naive_datetime_raises(self):
        ts = datetime(2024, 3, 1, 15, 49, 59)
        with self.assertRaises(ValueError):
            is_past_closing_auction_cutoff(ts)

    def test_default_cutoff_value(self):
        self.assertEqual(CLOSING_AUCTION_CUTOFF_ET, time(15, 50))


if __name__ == "__main__":
    unittest.main()
