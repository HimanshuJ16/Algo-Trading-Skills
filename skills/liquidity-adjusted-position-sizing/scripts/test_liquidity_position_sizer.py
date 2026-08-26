"""Unit tests for liquidity-adjusted-position-sizing."""
import math
import unittest

from liquidity_position_sizer import (
    ADV_WINDOW_TRADING_DAYS,
    CONSTRAINT_ADV_DTL,
    CONSTRAINT_BOOK_DEPTH,
    CONSTRAINT_NONE,
    LiquidityPositionSizer,
    MAR_BUYBACK_MAX_PARTICIPATION_PCT,
)

NON_FINITE = (float("nan"), float("inf"), float("-inf"))


class TestReferenceConstants(unittest.TestCase):
    """Pinned to Commission Delegated Regulation (EU) 2016/1052, Article 3(3)."""

    def test_buyback_safe_harbour_constants(self):
        # 25% of ADV, averaged over the 20 trading days preceding the purchase.
        self.assertEqual(MAR_BUYBACK_MAX_PARTICIPATION_PCT, 25.0)
        self.assertEqual(ADV_WINDOW_TRADING_DAYS, 20)


class TestLiquidityCap(unittest.TestCase):
    """
    Worked by hand for a 10% participation cap over 1 day:
        daily_capacity = 0.10 * 100,000 = 10,000 shares
        max_shares     = 10,000 * 1.0   = 10,000 shares  ($100,000 at $10)
    """

    def setUp(self):
        self.sizer = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)

    def test_illiquid_long_is_capped(self):
        # $500,000 target = 50,000 shares at $10; five times the 10,000-share cap.
        res = self.sizer.calculate_size("ILLIQ", 500_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertTrue(res.is_liquidity_constrained)
        self.assertEqual(res.binding_constraint, CONSTRAINT_ADV_DTL)
        self.assertEqual(res.liquidity_capped_shares, 10_000.0)
        self.assertEqual(res.liquidity_capped_capital_usd, 100_000.0)
        self.assertEqual(res.scaling_factor, 0.2)          # 10,000 / 50,000

    def test_short_is_capped_symmetrically_with_the_mirror_long(self):
        """
        Regression: a negative target used to skip the cap entirely, because
        ``-50,000 > 10,000`` is False. It returned the full 50,000-share short and
        reported is_liquidity_constrained=False with a scaling factor of 1.0.
        Covering a short in a name that cannot absorb the flow is at least as hard as
        selling the long, so the magnitude must be capped and the sign preserved.
        """
        short = self.sizer.calculate_size("ILLIQ", -500_000.0, price=10.0, adv_shares_20d=100_000.0)
        long = self.sizer.calculate_size("ILLIQ", 500_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertTrue(short.is_liquidity_constrained)
        self.assertEqual(short.liquidity_capped_shares, -10_000.0)
        self.assertEqual(short.liquidity_capped_capital_usd, -100_000.0)
        self.assertEqual(short.scaling_factor, long.scaling_factor)
        self.assertEqual(abs(short.liquidity_capped_shares), long.liquidity_capped_shares)
        self.assertEqual(short.dtl_days_target, long.dtl_days_target)

    def test_liquid_position_is_not_liquidity_constrained(self):
        # $100,000 at $150 = 666.67 shares; cap is 0.10 * 10,000,000 = 1,000,000 shares.
        res = self.sizer.calculate_size("AAPL", 100_000.0, price=150.0, adv_shares_20d=10_000_000.0)

        self.assertFalse(res.is_liquidity_constrained)
        self.assertEqual(res.binding_constraint, CONSTRAINT_NONE)
        # Floored to whole units: 666 shares * $150 = $99,900, never $100,050.
        self.assertEqual(res.liquidity_capped_shares, 666.0)
        self.assertEqual(res.liquidity_capped_capital_usd, 99_900.0)
        self.assertEqual(res.scaling_factor, round(666.0 / (100_000.0 / 150.0), 4))

    def test_position_exactly_at_the_cap_is_allowed(self):
        # 10,000 shares requested against a 10,000-share cap: at the limit, not over.
        res = self.sizer.calculate_size("EDGE", 100_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertFalse(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_shares, 10_000.0)
        self.assertEqual(res.dtl_days_final, 1.0)

    def test_one_share_over_the_cap_binds(self):
        res = self.sizer.calculate_size("EDGE", 100_010.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertTrue(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_shares, 10_000.0)

    def test_cap_is_floored_not_rounded(self):
        """
        Regression: rounding the capped share count to two decimals could round *up*
        past the cap. With ADV 99,999.99 the cap is 9,999.999 shares, which
        ``round(x, 2)`` turned into 10,000.0 — a share more than the limit permits.
        """
        res = self.sizer.calculate_size("RND", 10_000_000.0, price=10.0, adv_shares_20d=99_999.99)

        self.assertEqual(res.liquidity_capped_shares, 9_999.0)
        self.assertLessEqual(res.liquidity_capped_shares, res.max_shares_adv_dtl)

    def test_microcap_cap_below_one_share_returns_zero_not_one(self):
        # 0.10 * 5 * 1.0 = 0.5 shares of capacity: no whole unit is tradeable.
        res = self.sizer.calculate_size("MICRO", 50_000.0, price=10.0, adv_shares_20d=5.0)

        self.assertTrue(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_shares, 0.0)
        self.assertEqual(res.liquidity_capped_capital_usd, 0.0)
        self.assertEqual(res.scaling_factor, 0.0)

    def test_zero_target_returns_a_flat_position(self):
        res = self.sizer.calculate_size("FLAT", 0.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertFalse(res.is_liquidity_constrained)
        self.assertEqual(res.liquidity_capped_shares, 0.0)
        self.assertEqual(res.scaling_factor, 1.0)
        self.assertEqual(res.dtl_days_target, 0.0)
        self.assertEqual(res.dtl_days_final, 0.0)


class TestDaysToLiquidateReporting(unittest.TestCase):
    def setUp(self):
        self.sizer = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)

    def test_target_and_final_dtl_are_reported_separately(self):
        """
        Regression: a single ``dtl_days`` field held the *requested* position's DTL
        while the log line asserted "DTL <= 1.0d". A reader auditing the capped
        result saw 5.0 days next to a message claiming the limit was met.
        """
        res = self.sizer.calculate_size("ILLIQ", 500_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertEqual(res.dtl_days_target, 5.0)   # 50,000 / 10,000
        self.assertEqual(res.dtl_days_final, 1.0)    # 10,000 / 10,000

    def test_dtl_scales_linearly_with_the_dtl_budget(self):
        # Doubling the permitted liquidation horizon doubles the permitted size.
        one_day = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)
        two_day = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=2.0)
        args = dict(target_capital_usd=500_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertEqual(one_day.calculate_size("X", **args).liquidity_capped_shares, 10_000.0)
        self.assertEqual(two_day.calculate_size("X", **args).liquidity_capped_shares, 20_000.0)


class TestBookDepthCap(unittest.TestCase):
    """
    ADV alone can be inflated by a handful of block prints. The depth cap is the
    second, independent ceiling promised by this skill's description.
    """

    def setUp(self):
        self.sizer = LiquidityPositionSizer(
            max_participation_pct=10.0, max_dtl_days=1.0, max_book_depth_multiple=1.0)

    def test_depth_binds_when_tighter_than_the_adv_cap(self):
        # ADV cap = 0.10 * 10,000,000 = 1,000,000 shares; depth cap = 1.0 * 5,000.
        res = self.sizer.calculate_size(
            "THIN", 10_000_000.0, price=10.0, adv_shares_20d=10_000_000.0,
            book_depth_shares=5_000.0)

        self.assertTrue(res.is_liquidity_constrained)
        self.assertEqual(res.binding_constraint, CONSTRAINT_BOOK_DEPTH)
        self.assertEqual(res.liquidity_capped_shares, 5_000.0)
        self.assertEqual(res.max_shares_book_depth, 5_000.0)

    def test_adv_binds_when_tighter_than_the_depth_cap(self):
        # ADV cap = 10,000 shares; depth cap = 500,000 shares.
        res = self.sizer.calculate_size(
            "ILLIQ", 10_000_000.0, price=10.0, adv_shares_20d=100_000.0,
            book_depth_shares=500_000.0)

        self.assertEqual(res.binding_constraint, CONSTRAINT_ADV_DTL)
        self.assertEqual(res.liquidity_capped_shares, 10_000.0)

    def test_depth_multiple_scales_the_depth_cap(self):
        sizer = LiquidityPositionSizer(
            max_participation_pct=10.0, max_dtl_days=1.0, max_book_depth_multiple=3.0)
        res = sizer.calculate_size(
            "THIN", 10_000_000.0, price=10.0, adv_shares_20d=10_000_000.0,
            book_depth_shares=5_000.0)

        self.assertEqual(res.liquidity_capped_shares, 15_000.0)

    def test_depth_is_ignored_when_not_supplied(self):
        res = self.sizer.calculate_size("ILLIQ", 500_000.0, price=10.0, adv_shares_20d=100_000.0)

        self.assertIsNone(res.book_depth_shares)
        self.assertIsNone(res.max_shares_book_depth)
        self.assertEqual(res.binding_constraint, CONSTRAINT_ADV_DTL)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.sizer = LiquidityPositionSizer(max_participation_pct=10.0, max_dtl_days=1.0)

    def test_non_finite_inputs_raise_rather_than_returning_nan(self):
        """
        Regression: NaN slipped through ``price <= 0`` and ``target > cap`` alike —
        both comparisons are False for NaN — so the sizer returned a NaN share count
        labelled "Liquidity Sizing OK" with a scaling factor of 1.0.
        """
        for bad in NON_FINITE:
            with self.subTest(field="target_capital_usd", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", bad, price=10.0, adv_shares_20d=100_000.0)
            with self.subTest(field="price", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", 1_000.0, price=bad, adv_shares_20d=100_000.0)
            with self.subTest(field="adv_shares_20d", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", 1_000.0, price=10.0, adv_shares_20d=bad)
            with self.subTest(field="book_depth_shares", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size(
                        "X", 1_000.0, price=10.0, adv_shares_20d=100_000.0, book_depth_shares=bad)

    def test_non_positive_price_and_adv_raise(self):
        for bad in (0.0, -1.0):
            with self.subTest(field="price", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", 1_000.0, price=bad, adv_shares_20d=100_000.0)
            with self.subTest(field="adv_shares_20d", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", 1_000.0, price=10.0, adv_shares_20d=bad)
            with self.subTest(field="book_depth_shares", value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size(
                        "X", 1_000.0, price=10.0, adv_shares_20d=100_000.0, book_depth_shares=bad)

    def test_non_numeric_input_raises_value_error(self):
        for bad in (None, "1000", object()):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size("X", 1_000.0, price=bad, adv_shares_20d=100_000.0)

    def test_overflow_to_infinity_raises_rather_than_uncapping(self):
        """A cap of infinity caps nothing, so an overflow must fail, not pass."""
        with self.assertRaises(ValueError):   # target / price overflows
            self.sizer.calculate_size("X", 1e308, price=1e-300, adv_shares_20d=100_000.0)
        with self.assertRaises(ValueError):   # capacity * dtl budget overflows
            LiquidityPositionSizer(max_dtl_days=1e308).calculate_size(
                "X", 1_000_000.0, price=10.0, adv_shares_20d=1e308)

    def test_blank_symbol_raises(self):
        for bad in ("", "   ", None, 42):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.sizer.calculate_size(bad, 1_000.0, price=10.0, adv_shares_20d=100_000.0)


class TestConfigurationValidation(unittest.TestCase):
    def test_non_positive_participation_rejected(self):
        """
        Regression: ``max_participation_pct=-10.0`` produced a cap of -10,000 shares
        and returned a -$100,000 position for a +$500,000 long request — a
        misconfigured risk limit silently inverting the side of the trade.
        """
        for bad in (0.0, -10.0) + NON_FINITE:
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    LiquidityPositionSizer(max_participation_pct=bad)

    def test_participation_above_one_hundred_percent_rejected(self):
        with self.assertRaises(ValueError):
            LiquidityPositionSizer(max_participation_pct=100.01)
        # 100% exactly is the boundary and is permitted.
        self.assertEqual(LiquidityPositionSizer(max_participation_pct=100.0).max_participation_pct, 100.0)

    def test_non_positive_dtl_and_depth_multiple_rejected(self):
        for bad in (0.0, -1.0) + NON_FINITE:
            with self.subTest(field="max_dtl_days", value=bad):
                with self.assertRaises(ValueError):
                    LiquidityPositionSizer(max_dtl_days=bad)
            with self.subTest(field="max_book_depth_multiple", value=bad):
                with self.assertRaises(ValueError):
                    LiquidityPositionSizer(max_book_depth_multiple=bad)


class TestInvariants(unittest.TestCase):
    """The properties every returned position must satisfy, over a parameter grid."""

    def test_result_never_exceeds_target_or_any_cap(self):
        for pct in (0.5, 10.0, 25.0, 100.0):
            for dtl in (0.5, 1.0, 3.0):
                for adv in (7.0, 100_000.0, 10_000_000.0):
                    for capital in (-1_000_000.0, -1.0, 0.0, 1.0, 250_000.0, 1e9):
                        for depth in (None, 1_000.0):
                            sizer = LiquidityPositionSizer(pct, dtl, max_book_depth_multiple=2.0)
                            res = sizer.calculate_size(
                                "GRID", capital, price=37.5, adv_shares_20d=adv,
                                book_depth_shares=depth)
                            with self.subTest(pct=pct, dtl=dtl, adv=adv, cap=capital, depth=depth):
                                shares = abs(res.liquidity_capped_shares)
                                self.assertTrue(math.isfinite(shares))
                                self.assertEqual(shares, math.floor(shares))
                                self.assertLessEqual(shares, abs(capital) / 37.5)
                                self.assertLessEqual(shares, res.max_shares_adv_dtl)
                                if res.max_shares_book_depth is not None:
                                    self.assertLessEqual(shares, res.max_shares_book_depth)
                                # The horizon the cap exists to enforce.
                                daily_capacity = (pct / 100.0) * adv
                                self.assertLessEqual(shares / daily_capacity, dtl)
                                # Sign of the request survives the cap.
                                if res.liquidity_capped_shares != 0.0:
                                    self.assertEqual(
                                        res.liquidity_capped_shares < 0.0, capital < 0.0)


if __name__ == "__main__":
    unittest.main()
