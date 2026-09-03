import unittest

from idx_indonesia_stock_exchange_api import (
    IdxOrderPayload,
    IdxStockExchangeApiEngine,
)


class TestFraksiHarga(unittest.TestCase):
    """Tick size is selected from the reference (previous close) price band."""

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_tick_size_per_band(self):
        self.assertEqual(self.engine.get_idx_fraksi_harga(150.0), 1)
        self.assertEqual(self.engine.get_idx_fraksi_harga(350.0), 2)
        self.assertEqual(self.engine.get_idx_fraksi_harga(1500.0), 5)
        self.assertEqual(self.engine.get_idx_fraksi_harga(3500.0), 10)
        self.assertEqual(self.engine.get_idx_fraksi_harga(10000.0), 25)

    def test_tick_size_band_boundaries_are_lower_inclusive(self):
        # Each band is [lower, upper); the boundary price belongs to the band above.
        self.assertEqual(self.engine.get_idx_fraksi_harga(199.0), 1)
        self.assertEqual(self.engine.get_idx_fraksi_harga(200.0), 2)
        self.assertEqual(self.engine.get_idx_fraksi_harga(499.0), 2)
        self.assertEqual(self.engine.get_idx_fraksi_harga(500.0), 5)
        self.assertEqual(self.engine.get_idx_fraksi_harga(1999.0), 5)
        self.assertEqual(self.engine.get_idx_fraksi_harga(2000.0), 10)
        self.assertEqual(self.engine.get_idx_fraksi_harga(4999.0), 10)
        self.assertEqual(self.engine.get_idx_fraksi_harga(5000.0), 25)

    def test_non_positive_reference_price_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.get_idx_fraksi_harga(0.0)
        with self.assertRaises(ValueError):
            self.engine.get_idx_fraksi_harga(-100.0)


class TestTickSizeIsAnchoredToReferencePrice(unittest.TestCase):
    """Regression: IDX fixes the tick from the previous close for the whole day.

    Deriving it from the live order price mis-sizes the tick on any day the
    price crosses a band boundary, producing both false rejects and false
    accepts. Both directions are covered here.
    """

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_price_crossing_up_keeps_previous_close_tick(self):
        # Previous close Rp 1,990 -> tick Rp 5 all day. Rp 2,005 is on-tick even
        # though its own band (Rp 2,000-5,000) would imply a Rp 10 tick.
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 2005.0, 500, 1990.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.applicable_fraksi_harga, 5)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")

    def test_price_crossing_down_keeps_previous_close_tick(self):
        # Previous close Rp 2,010 -> tick Rp 10 all day. Rp 1,995 is off-tick
        # even though its own band (Rp 500-2,000) would imply a Rp 5 tick.
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 1995.0, 500, 2010.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.applicable_fraksi_harga, 10)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)


class TestOrderRouting(unittest.TestCase):

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_valid_order(self):
        # BBCA @ Rp 10,000 (tick Rp 25), 500 shares (5 lots), ref Rp 10,000.
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 500, 10000.0)
        report = self.engine.validate_and_route_order(payload)

        self.assertEqual(report.ticker, "BBCA")
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertEqual(report.lots_count, 5)
        self.assertEqual(report.applicable_fraksi_harga, 25)
        self.assertEqual(report.max_price_step, 250)
        self.assertTrue(report.is_price_tick_valid)
        self.assertTrue(report.is_board_lot_valid)
        self.assertTrue(report.is_auto_rejection_valid)

    def test_odd_lot_rejected_on_regular_market(self):
        payload = IdxOrderPayload("TLKM", "RG", "BUY", 4000.0, 150, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_BOARD_LOT")
        self.assertFalse(report.is_board_lot_valid)

    def test_odd_lot_rejected_on_cash_market(self):
        payload = IdxOrderPayload("TLKM", "TN", "BUY", 4000.0, 150, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_BOARD_LOT")

    def test_off_tick_price_rejected(self):
        # Rp 10,005 is not a multiple of the Rp 25 tick.
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10005.0, 500, 10000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertFalse(report.is_price_tick_valid)

    def test_fractional_rupiah_price_rejected(self):
        # IDX quotes whole Rupiah; Rp 150.5 is off-tick even at a Rp 1 tick.
        payload = IdxOrderPayload("GOTO", "RG", "BUY", 150.5, 500, 150.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

    def test_lots_count_is_zero_for_odd_share_counts(self):
        # A 150-share negotiated order is 1.5 lots; reporting "1 Lot" would
        # understate it, so lots_count is 0 and quantity_shares carries the size.
        payload = IdxOrderPayload("TLKM", "NG", "BUY", 4000.0, 150, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertEqual(report.lots_count, 0)
        self.assertEqual(report.quantity_shares, 150)


class TestNegotiatedMarketExemptions(unittest.TestCase):
    """Pasar Negosiasi is bilaterally negotiated: no round lot, tick, volume
    cap or Auto Rejection."""

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_odd_lot_allowed(self):
        payload = IdxOrderPayload("TLKM", "NG", "SELL", 4000.0, 137, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertTrue(report.is_board_lot_valid)

    def test_price_far_outside_auto_rejection_band_allowed(self):
        # +200% vs the reference price would be rejected on RG, not on NG.
        payload = IdxOrderPayload("TLKM", "NG", "BUY", 12000.0, 100, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertTrue(report.is_auto_rejection_valid)
        self.assertIsNone(report.auto_rejection_lower_price)
        self.assertIsNone(report.auto_rejection_upper_price)

    def test_same_price_rejected_on_regular_market(self):
        payload = IdxOrderPayload("TLKM", "RG", "BUY", 12000.0, 100, 4000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "AUTO_REJECTION_EXCEEDED")

    def test_price_below_ordinary_minimum_allowed(self):
        payload = IdxOrderPayload("TLKM", "NG", "BUY", 25.0, 100, 100.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")


class TestAutoRejectionBands(unittest.TestCase):
    """ARA is tiered (35/25/20%); ARB has been a flat 15% since 8 April 2025."""

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_bands_are_asymmetric(self):
        lower, upper = self.engine.get_auto_rejection_bounds(1000.0)
        self.assertAlmostEqual(lower, 850.0)   # -15%
        self.assertAlmostEqual(upper, 1250.0)  # +25%

    def test_ara_tier_for_cheap_stocks(self):
        _, upper = self.engine.get_auto_rejection_bounds(100.0)
        self.assertAlmostEqual(upper, 135.0)   # +35%

    def test_ara_tier_for_expensive_stocks(self):
        _, upper = self.engine.get_auto_rejection_bounds(10000.0)
        self.assertAlmostEqual(upper, 12000.0)  # +20%

    def test_ara_tier_boundary_is_upper_inclusive(self):
        # Rp 200 sits in the 35% band; Rp 201 in the 25% band.
        self.assertAlmostEqual(self.engine.get_auto_rejection_bounds(200.0)[1], 270.0)
        self.assertAlmostEqual(self.engine.get_auto_rejection_bounds(201.0)[1], 251.25)
        # Rp 5,000 sits in the 25% band; Rp 5,001 in the 20% band.
        self.assertAlmostEqual(self.engine.get_auto_rejection_bounds(5000.0)[1], 6250.0)
        self.assertAlmostEqual(self.engine.get_auto_rejection_bounds(5001.0)[1], 6001.2)

    def test_lower_band_clamped_to_minimum_price(self):
        # 15% below Rp 50 is Rp 42.50, but Rp 50 is the ordinary-board floor.
        lower, _ = self.engine.get_auto_rejection_bounds(50.0)
        self.assertAlmostEqual(lower, 50.0)

    def test_drop_beyond_arb_rejected(self):
        # -20% from Rp 1,000 breaches the 15% ARB, though it would have passed
        # under an earlier symmetric 25% rule.
        payload = IdxOrderPayload("BBCA", "RG", "SELL", 800.0, 100, 1000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "AUTO_REJECTION_EXCEEDED")
        self.assertFalse(report.is_auto_rejection_valid)

    def test_rise_within_ara_accepted(self):
        # +20% from Rp 1,000 is inside the 25% ARA but outside the 15% ARB, so
        # the band must not be applied symmetrically.
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 1200.0, 100, 1000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")

    def test_exact_band_edges_are_inclusive(self):
        for price, expected in ((1250.0, "IDX_ORDER_VALIDATED"), (1255.0, "AUTO_REJECTION_EXCEEDED")):
            report = self.engine.validate_and_route_order(
                IdxOrderPayload("BBCA", "RG", "BUY", price, 100, 1000.0)
            )
            self.assertEqual(report.status, expected, msg=f"price={price}")

    def test_house_cap_tightens_but_never_widens_the_band(self):
        strict = IdxStockExchangeApiEngine(max_auto_rejection_pct=10.0)
        lower, upper = strict.get_auto_rejection_bounds(1000.0)
        self.assertAlmostEqual(lower, 900.0)
        self.assertAlmostEqual(upper, 1100.0)

        # A 30% house cap cannot widen the 15% ARB or the 25% ARA.
        loose = IdxStockExchangeApiEngine(max_auto_rejection_pct=30.0)
        lower, upper = loose.get_auto_rejection_bounds(1000.0)
        self.assertAlmostEqual(lower, 850.0)
        self.assertAlmostEqual(upper, 1250.0)

    def test_invalid_house_cap_rejected(self):
        with self.assertRaises(ValueError):
            IdxStockExchangeApiEngine(max_auto_rejection_pct=0.0)


class TestRestrictedListingBoards(unittest.TestCase):
    """Acceleration / Watchlist boards: +/- Rp 1 up to Rp 10, +/- 10% above."""

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_absolute_rupiah_band_for_very_cheap_stocks(self):
        lower, upper = self.engine.get_auto_rejection_bounds(5.0, "WATCHLIST")
        self.assertAlmostEqual(lower, 4.0)
        self.assertAlmostEqual(upper, 6.0)

    def test_percentage_band_above_ten_rupiah(self):
        lower, upper = self.engine.get_auto_rejection_bounds(100.0, "ACCELERATION")
        self.assertAlmostEqual(lower, 90.0)
        self.assertAlmostEqual(upper, 110.0)

    def test_watchlist_stock_may_trade_below_fifty_rupiah(self):
        payload = IdxOrderPayload("DADA", "RG", "BUY", 9.0, 100, 10.0, listing_board="WATCHLIST")
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "IDX_ORDER_VALIDATED")
        self.assertTrue(report.is_minimum_price_valid)

    def test_same_order_on_main_board_breaches_the_price_floor(self):
        payload = IdxOrderPayload("DADA", "RG", "BUY", 9.0, 100, 10.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "PRICE_BELOW_MINIMUM")
        self.assertFalse(report.is_minimum_price_valid)

    def test_main_board_floor_is_configurable(self):
        # Anticipates the announced Rp 50 -> Rp 1 minimum price change.
        engine = IdxStockExchangeApiEngine(minimum_price_ordinary_boards=1)
        payload = IdxOrderPayload("DADA", "RG", "BUY", 9.0, 100, 10.0)
        self.assertEqual(
            engine.validate_and_route_order(payload).status, "IDX_ORDER_VALIDATED"
        )

    def test_unknown_listing_board_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.get_auto_rejection_bounds(100.0, "PAPAN_UTAMA")


class TestMinimumPriceFloor(unittest.TestCase):

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_price_at_floor_accepted(self):
        payload = IdxOrderPayload("BUMI", "RG", "BUY", 50.0, 100, 50.0)
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "IDX_ORDER_VALIDATED"
        )

    def test_price_one_rupiah_below_floor_rejected(self):
        payload = IdxOrderPayload("BUMI", "RG", "BUY", 49.0, 100, 50.0)
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "PRICE_BELOW_MINIMUM"
        )


class TestOrderVolumeCap(unittest.TestCase):
    """Volume Auto Rejection: 50,000 lots or 5% of listed shares, whichever is
    smaller."""

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_lot_cap_without_listed_share_count(self):
        self.assertEqual(self.engine.max_order_shares(), 5_000_000)

    def test_listed_share_fraction_binds_when_smaller(self):
        # 5% of 20,000,000 shares = 1,000,000 shares < the 5,000,000-share cap.
        self.assertEqual(self.engine.max_order_shares(20_000_000), 1_000_000)

    def test_lot_cap_binds_when_listed_share_fraction_is_larger(self):
        self.assertEqual(self.engine.max_order_shares(10_000_000_000), 5_000_000)

    def test_order_at_the_cap_accepted(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 5_000_000, 10000.0)
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "IDX_ORDER_VALIDATED"
        )

    def test_order_above_the_lot_cap_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 5_000_100, 10000.0)
        report = self.engine.validate_and_route_order(payload)
        self.assertEqual(report.status, "INVALID_ORDER_VOLUME")
        self.assertFalse(report.is_order_volume_valid)

    def test_order_above_the_listed_share_fraction_rejected(self):
        payload = IdxOrderPayload(
            "BBCA", "RG", "BUY", 10000.0, 1_000_100, 10000.0, listed_shares=20_000_000
        )
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "INVALID_ORDER_VOLUME"
        )

    def test_volume_cap_not_applied_to_negotiated_market(self):
        payload = IdxOrderPayload("BBCA", "NG", "BUY", 10000.0, 50_000_000, 10000.0)
        self.assertEqual(
            self.engine.validate_and_route_order(payload).status, "IDX_ORDER_VALIDATED"
        )

    def test_invalid_listed_share_count_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.max_order_shares(0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = IdxStockExchangeApiEngine()

    def test_ticker_normalisation(self):
        self.assertEqual(self.engine.validate_ticker("  bbca "), "BBCA")

    def test_malformed_tickers_rejected(self):
        for bad in ("BBC", "BBCAA", "BB1A", "TLKM-R", "TLKM-W", "", "BBC A"):
            with self.assertRaises(ValueError, msg=f"ticker={bad!r}"):
                self.engine.validate_ticker(bad)

    def test_non_string_ticker_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.validate_ticker(1234)

    def test_invalid_market_segment_rejected(self):
        payload = IdxOrderPayload("BBCA", "XX", "BUY", 10000.0, 100, 10000.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)

    def test_invalid_side_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "LONG", 10000.0, 100, 10000.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)

    def test_side_is_normalised(self):
        payload = IdxOrderPayload("BBCA", "RG", " sell ", 10000.0, 100, 10000.0)
        self.assertEqual(self.engine.validate_and_route_order(payload).side, "SELL")

    def test_zero_reference_price_raises_instead_of_dividing_by_zero(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 100, 0.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)

    def test_negative_reference_price_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 100, -10000.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)

    def test_non_finite_price_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", float("nan"), 100, 10000.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)
        payload = IdxOrderPayload("BBCA", "RG", "BUY", float("inf"), 100, 10000.0)
        with self.assertRaises(ValueError):
            self.engine.validate_and_route_order(payload)

    def test_non_positive_quantity_rejected(self):
        for bad_qty in (0, -100):
            payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, bad_qty, 10000.0)
            with self.assertRaises(ValueError, msg=f"qty={bad_qty}"):
                self.engine.validate_and_route_order(payload)

    def test_non_integer_quantity_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, 100.0, 10000.0)
        with self.assertRaises(TypeError):
            self.engine.validate_and_route_order(payload)

    def test_boolean_quantity_rejected(self):
        payload = IdxOrderPayload("BBCA", "RG", "BUY", 10000.0, True, 10000.0)
        with self.assertRaises(TypeError):
            self.engine.validate_and_route_order(payload)


if __name__ == "__main__":
    unittest.main()
