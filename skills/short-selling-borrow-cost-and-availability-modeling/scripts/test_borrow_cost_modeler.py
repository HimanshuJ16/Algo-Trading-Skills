import logging
import math
import unittest

from borrow_cost_modeler import (
    DAY_COUNT_ACT_360,
    DAY_COUNT_ACT_365,
    RATE_SOURCE_HEURISTIC_GC,
    RATE_SOURCE_HEURISTIC_HTB,
    RATE_SOURCE_OBSERVED,
    BorrowCostModeler,
    BorrowStatus,
    ShortTrade,
    UnknownBorrowStatusError,
)

logging.getLogger("borrow_cost_modeler").setLevel(logging.CRITICAL)


class TestBorrowStatusValidation(unittest.TestCase):
    def test_valid_status_sets_htb_flag(self):
        self.assertFalse(BorrowStatus("AAPL", 0.10, 100_000).is_hard_to_borrow)
        self.assertFalse(BorrowStatus("EDGE", 0.80, 100_000).is_hard_to_borrow)
        self.assertTrue(BorrowStatus("GME", 0.9001, 5_000).is_hard_to_borrow)

    def test_utilization_outside_unit_interval_rejected(self):
        # Utilization is on-loan / lendable; a value above 1.0 is corrupt data, not a
        # "very hard to borrow" signal.
        with self.assertRaises(ValueError):
            BorrowStatus("BAD", 1.5, 1_000)
        with self.assertRaises(ValueError):
            BorrowStatus("BAD", -0.1, 1_000)

    def test_non_finite_utilization_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                BorrowStatus("BAD", bad, 1_000)

    def test_available_shares_must_be_non_negative_int(self):
        with self.assertRaises(ValueError):
            BorrowStatus("BAD", 0.5, -1)
        with self.assertRaises(TypeError):
            BorrowStatus("BAD", 0.5, 1_000.5)
        with self.assertRaises(TypeError):
            BorrowStatus("BAD", 0.5, True)

    def test_empty_ticker_rejected(self):
        with self.assertRaises(ValueError):
            BorrowStatus("", 0.5, 1_000)

    def test_negative_observed_rate_allowed(self):
        # MSLA Sec. 5.1 explicitly contemplates a Loan Fee below zero.
        self.assertEqual(
            BorrowStatus("NEG", 0.5, 1_000, observed_borrow_rate=-0.004).observed_borrow_rate,
            -0.004,
        )


class TestShortTradeValidation(unittest.TestCase):
    def test_valid_trade(self):
        trade = ShortTrade("AAPL", 100, 150.0, 30)
        self.assertEqual(trade.days_held, 30)

    def test_non_positive_shares_rejected(self):
        # A short is passed as an absolute size; a negative here would silently flip
        # the sign of the cost and turn a fee into a credit.
        for bad in (0, -100):
            with self.assertRaises(ValueError):
                ShortTrade("AAPL", bad, 150.0, 30)

    def test_non_positive_price_rejected(self):
        for bad in (0.0, -1.0):
            with self.assertRaises(ValueError):
                ShortTrade("AAPL", 100, bad, 30)

    def test_nan_price_rejected(self):
        with self.assertRaises(ValueError):
            ShortTrade("AAPL", 100, float("nan"), 30)

    def test_negative_days_held_rejected(self):
        with self.assertRaises(ValueError):
            ShortTrade("AAPL", 100, 150.0, -1)

    def test_zero_days_held_allowed(self):
        self.assertEqual(ShortTrade("AAPL", 100, 150.0, 0).days_held, 0)


class TestModelerConstruction(unittest.TestCase):
    def test_max_below_base_rejected(self):
        with self.assertRaises(ValueError):
            BorrowCostModeler(htb_base_rate=0.50, max_htb_rate=0.10)

    def test_threshold_of_one_rejected(self):
        # A threshold of 1.0 leaves a zero-width ramp; guard against the division.
        with self.assertRaises(ValueError):
            BorrowCostModeler(htb_utilization_threshold=1.0)

    def test_collateral_margin_below_one_rejected(self):
        with self.assertRaises(ValueError):
            BorrowCostModeler(collateral_margin_pct=0.98)

    def test_non_positive_day_count_rejected(self):
        with self.assertRaises(ValueError):
            BorrowCostModeler(day_count_basis=0)


class TestAvailability(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler(gc_rate=0.0025, htb_base_rate=0.05, max_htb_rate=0.30)
        self.modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        self.modeler.update_status(BorrowStatus("GME", 0.90, 5_000))
        self.modeler.update_status(BorrowStatus("MEME", 1.00, 0))
        self.modeler.update_status(BorrowStatus("CNTR", 1.00, 5_000))

    def test_general_collateral_available(self):
        result = self.modeler.check_availability("AAPL", 1_000)
        self.assertTrue(result.is_available)
        self.assertEqual(result.reason, "AVAILABLE")

    def test_hard_to_borrow_available_within_inventory(self):
        self.assertTrue(self.modeler.can_short("GME", 5_000))

    def test_request_above_inventory_rejected(self):
        result = self.modeler.check_availability("GME", 5_001)
        self.assertFalse(result.is_available)
        self.assertEqual(result.reason, "INSUFFICIENT_INVENTORY")

    def test_zero_inventory_rejected(self):
        self.assertEqual(
            self.modeler.check_availability("MEME", 100).reason, "NO_INVENTORY")

    def test_full_utilization_with_reported_inventory_rejected(self):
        # Contradictory data (inventory offered while the lendable pool is fully lent)
        # must fail closed rather than trust the more permissive field.
        result = self.modeler.check_availability("CNTR", 100)
        self.assertFalse(result.is_available)
        self.assertEqual(result.reason, "FULLY_UTILIZED")

    def test_unknown_ticker_fails_closed(self):
        # Regression: the previous implementation returned True here, waving through a
        # short on a security whose borrow nobody had checked.
        result = self.modeler.check_availability("UNKNOWN", 10_000)
        self.assertFalse(result.is_available)
        self.assertEqual(result.reason, "NO_BORROW_STATUS")
        self.assertFalse(self.modeler.can_short("UNKNOWN", 10_000))

    def test_non_positive_request_rejected(self):
        with self.assertRaises(ValueError):
            self.modeler.can_short("AAPL", 0)
        with self.assertRaises(ValueError):
            self.modeler.can_short("AAPL", -100)


class TestRateResolution(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler(gc_rate=0.0025, htb_base_rate=0.05, max_htb_rate=0.30)
        self.modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        self.modeler.update_status(BorrowStatus("GME", 0.90, 5_000))
        self.modeler.update_status(BorrowStatus("MEME", 1.00, 0))

    def test_general_collateral_rate(self):
        rate, source = self.modeler.resolve_rate("AAPL")
        self.assertEqual(rate, 0.0025)
        self.assertEqual(source, RATE_SOURCE_HEURISTIC_GC)

    def test_threshold_boundary_is_general_collateral(self):
        self.modeler.update_status(BorrowStatus("EDGE", 0.80, 1_000))
        self.assertEqual(self.modeler.calculate_annualized_rate("EDGE"), 0.0025)

    def test_htb_ramp_midpoint(self):
        # Half way along the 0.80 -> 1.00 ramp: 0.05 + 0.5 * (0.30 - 0.05) = 0.175
        rate, source = self.modeler.resolve_rate("GME")
        self.assertAlmostEqual(rate, 0.175)
        self.assertEqual(source, RATE_SOURCE_HEURISTIC_HTB)

    def test_htb_ramp_top_is_max_rate(self):
        self.assertAlmostEqual(self.modeler.calculate_annualized_rate("MEME"), 0.30)

    def test_observed_rate_overrides_heuristic(self):
        # A quoted rate is evidence; the utilization ramp is a fallback guess. The
        # heuristic would have priced this name at 0.175.
        self.modeler.update_status(
            BorrowStatus("GME", 0.90, 5_000, observed_borrow_rate=0.87))
        rate, source = self.modeler.resolve_rate("GME")
        self.assertEqual(rate, 0.87)
        self.assertEqual(source, RATE_SOURCE_OBSERVED)

    def test_unknown_ticker_raises_rather_than_pricing_as_general_collateral(self):
        # Regression: the previous implementation returned gc_rate here, pricing an
        # unknown special at a few basis points and inflating every short backtest.
        with self.assertRaises(UnknownBorrowStatusError):
            self.modeler.calculate_annualized_rate("UNKNOWN")
        # Still catchable by callers written against the stdlib hierarchy.
        with self.assertRaises(LookupError):
            self.modeler.calculate_annualized_rate("UNKNOWN")


class TestCollateralValue(unittest.TestCase):
    def test_margin_percentage_applied(self):
        modeler = BorrowCostModeler()
        # 100 shares at $150.00 -> 102% of market value = 100 * $153.00
        self.assertAlmostEqual(modeler.collateral_value(100, 150.0), 15_300.0)

    def test_bare_market_value_when_margin_is_one(self):
        modeler = BorrowCostModeler(collateral_margin_pct=1.0)
        self.assertAlmostEqual(modeler.collateral_value(100, 150.0), 15_000.0)

    def test_ibkr_round_up_to_whole_dollar(self):
        # IBKR: 102% of the prior settlement price, rounded up to the nearest whole
        # dollar, times shares. $20.00 -> $20.40 -> $21.00 -> 100 * 21 = $2,100.
        modeler = BorrowCostModeler(round_collateral_price_up=True)
        self.assertAlmostEqual(modeler.collateral_value(100, 20.0), 2_100.0)
        self.assertAlmostEqual(
            BorrowCostModeler().collateral_value(100, 20.0), 2_040.0)


class TestBorrowCost(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler(gc_rate=0.0025, htb_base_rate=0.05, max_htb_rate=0.30)
        self.modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        self.modeler.update_status(BorrowStatus("GME", 0.90, 5_000))

    def test_general_collateral_cost_uses_360_and_102_percent(self):
        # 100 sh x $150 -> collateral $15,300; $15,300 x 0.0025 = $38.25 per year;
        # / 360 = $0.10625 per day; x 30 days = $3.1875.
        trade = ShortTrade("AAPL", 100, 150.0, 30)
        self.assertAlmostEqual(self.modeler.calculate_borrow_cost(trade), 3.1875)

    def test_365_basis_understates_the_360_convention(self):
        # Regression against the previous 365-day, bare-notional formula, which
        # returned $3.0822 for the same trade.
        trade = ShortTrade("AAPL", 100, 150.0, 30)
        legacy = BorrowCostModeler(
            gc_rate=0.0025, day_count_basis=DAY_COUNT_ACT_365, collateral_margin_pct=1.0)
        legacy.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        self.assertAlmostEqual(legacy.calculate_borrow_cost(trade), 15_000 * 0.0025 / 365 * 30)
        self.assertGreater(
            self.modeler.calculate_borrow_cost(trade), legacy.calculate_borrow_cost(trade))

    def test_gbp_basis_is_selectable(self):
        modeler = BorrowCostModeler(gc_rate=0.0025, day_count_basis=DAY_COUNT_ACT_365)
        modeler.update_status(BorrowStatus("VOD", 0.10, 100_000))
        trade = ShortTrade("VOD", 100, 150.0, 30)
        self.assertAlmostEqual(modeler.calculate_borrow_cost(trade), 15_300 * 0.0025 / 365 * 30)

    def test_hard_to_borrow_cost(self):
        # 100 sh x $20 -> collateral $2,040 at 17.5%; $2,040 x 0.175 / 360 x 10 = $9.9167
        trade = ShortTrade("GME", 100, 20.0, 10)
        self.assertAlmostEqual(self.modeler.calculate_borrow_cost(trade), 9.916666666666666)

    def test_zero_days_held_costs_nothing(self):
        self.assertEqual(
            self.modeler.calculate_borrow_cost(ShortTrade("AAPL", 100, 150.0, 0)), 0.0)

    def test_detail_reports_basis_and_source(self):
        result = self.modeler.calculate_borrow_cost_detail(ShortTrade("GME", 100, 20.0, 10))
        self.assertEqual(result.day_count_basis, DAY_COUNT_ACT_360)
        self.assertEqual(result.rate_source, RATE_SOURCE_HEURISTIC_HTB)
        self.assertTrue(result.is_hard_to_borrow)
        self.assertAlmostEqual(result.average_collateral_value_usd, 2_040.0)
        self.assertEqual(result.short_proceeds_credit_usd, 0.0)
        self.assertAlmostEqual(result.net_financing_cost_usd, result.gross_borrow_cost_usd)

    def test_unknown_ticker_cost_raises(self):
        with self.assertRaises(UnknownBorrowStatusError):
            self.modeler.calculate_borrow_cost(ShortTrade("UNKNOWN", 100, 10.0, 5))


class TestBorrowCostSchedule(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler(
            gc_rate=0.10, htb_base_rate=0.05, max_htb_rate=0.30)
        self.modeler.update_status(BorrowStatus("RISE", 0.10, 100_000))

    def test_accrues_on_each_days_mark(self):
        # Collateral 102% of $10 / $20 / $30 on 100 shares -> $1,020 + $2,040 + $3,060
        # = $6,120 collateral-days; x 0.10 / 360 = $1.70.
        result = self.modeler.calculate_borrow_cost_schedule("RISE", 100, [10.0, 20.0, 30.0])
        self.assertAlmostEqual(result.gross_borrow_cost_usd, 1.70)
        self.assertEqual(result.accrual_days, 3)
        self.assertAlmostEqual(result.average_collateral_value_usd, 2_040.0)

    def test_flat_entry_price_understates_a_short_that_moves_against_you(self):
        # The single-price approximation prices all three days at the $10 entry.
        flat = self.modeler.calculate_borrow_cost(ShortTrade("RISE", 100, 10.0, 3))
        marked = self.modeler.calculate_borrow_cost_schedule(
            "RISE", 100, [10.0, 20.0, 30.0]).gross_borrow_cost_usd
        self.assertAlmostEqual(flat, 0.85)
        self.assertAlmostEqual(marked, 1.70)
        self.assertGreater(marked, flat)

    def test_per_day_rates_are_applied(self):
        # $1,020 x 0.10 + $2,040 x 0.20 + $3,060 x 0.30 = 102 + 408 + 918 = 1,428;
        # / 360 = $3.9666...
        result = self.modeler.calculate_borrow_cost_schedule(
            "RISE", 100, [10.0, 20.0, 30.0], daily_rates=[0.10, 0.20, 0.30])
        self.assertAlmostEqual(result.gross_borrow_cost_usd, 1_428.0 / 360.0)
        self.assertAlmostEqual(result.annualized_borrow_rate, 0.20)
        self.assertEqual(result.rate_source, RATE_SOURCE_OBSERVED)

    def test_rate_length_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.modeler.calculate_borrow_cost_schedule(
                "RISE", 100, [10.0, 20.0], daily_rates=[0.10])

    def test_empty_schedule_rejected(self):
        with self.assertRaises(ValueError):
            self.modeler.calculate_borrow_cost_schedule("RISE", 100, [])

    def test_non_finite_mark_rejected(self):
        with self.assertRaises(ValueError):
            self.modeler.calculate_borrow_cost_schedule("RISE", 100, [10.0, float("nan")])

    def test_non_positive_mark_rejected(self):
        with self.assertRaises(ValueError):
            self.modeler.calculate_borrow_cost_schedule("RISE", 100, [10.0, 0.0])


class TestShortProceedsCredit(unittest.TestCase):
    def test_credit_can_turn_financing_positive(self):
        # 100 sh x $100 x 36 days = $360,000 proceeds-days; x 0.05 / 360 = $50.00 credit.
        # Gross fee: collateral $10,200 x 0.003 / 360 x 36 = $3.06.
        modeler = BorrowCostModeler(gc_rate=0.003, short_proceeds_credit_rate=0.05)
        modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        result = modeler.calculate_borrow_cost_detail(ShortTrade("AAPL", 100, 100.0, 36))
        self.assertAlmostEqual(result.gross_borrow_cost_usd, 3.06)
        self.assertAlmostEqual(result.short_proceeds_credit_usd, 50.0)
        self.assertAlmostEqual(result.net_financing_cost_usd, -46.94)

    def test_no_credit_modelled_by_default(self):
        modeler = BorrowCostModeler(gc_rate=0.003)
        modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        result = modeler.calculate_borrow_cost_detail(ShortTrade("AAPL", 100, 100.0, 36))
        self.assertEqual(result.short_proceeds_credit_usd, 0.0)


class TestRecallRisk(unittest.TestCase):
    def setUp(self):
        self.modeler = BorrowCostModeler()
        self.modeler.update_status(BorrowStatus("AAPL", 0.10, 100_000))
        self.modeler.update_status(BorrowStatus("GME", 0.92, 5_000))
        self.modeler.update_status(BorrowStatus("MEME", 1.00, 0))
        self.modeler.update_status(BorrowStatus("DRY", 0.20, 0))

    def test_low_tier(self):
        self.assertEqual(self.modeler.assess_recall_risk("AAPL").tier, "LOW")

    def test_elevated_tier_at_watch_level(self):
        self.assertEqual(self.modeler.assess_recall_risk("GME").tier, "ELEVATED")

    def test_high_tier_when_fully_lent(self):
        self.assertEqual(self.modeler.assess_recall_risk("MEME").tier, "HIGH")

    def test_high_tier_when_no_inventory_offered_despite_low_utilization(self):
        self.assertEqual(self.modeler.assess_recall_risk("DRY").tier, "HIGH")

    def test_unknown_ticker_raises(self):
        with self.assertRaises(UnknownBorrowStatusError):
            self.modeler.assess_recall_risk("UNKNOWN")


class TestNumericalSanity(unittest.TestCase):
    def test_cost_is_finite_for_extreme_but_valid_inputs(self):
        modeler = BorrowCostModeler(htb_base_rate=0.05, max_htb_rate=5.00)
        modeler.update_status(BorrowStatus("SQUEEZE", 1.0, 10))
        cost = modeler.calculate_borrow_cost(ShortTrade("SQUEEZE", 10, 1_000.0, 365))
        self.assertTrue(math.isfinite(cost))
        # 10 sh x $1,000 x 1.02 = $10,200 at 500% for 365 days on a 360 basis.
        self.assertAlmostEqual(cost, 10_200 * 5.0 / 360 * 365)


if __name__ == "__main__":
    unittest.main()
