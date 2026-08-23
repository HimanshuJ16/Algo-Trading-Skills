import unittest

from decentralized_exchange_dex_integration_uniswap_style import (
    UniswapDexIntegrationEngine,
    AmmPoolState,
    UniswapSwapRequest,
    UniswapSwapExecutionReport,
    get_amount_out_integer,
    MAX_PERMITTED_SLIPPAGE_FRACTION,
)


class TestUniswapDexIntegrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = UniswapDexIntegrationEngine()

        # ETH/USDC Pool: 1,000 ETH, 3,000,000 USDC -> Spot Price = 3,000 USDC / ETH
        self.pool = AmmPoolState("ETH_USDC_0.3", "ETH", "USDC", reserve_in=1000.0, reserve_out=3000000.0, fee_pct=0.003)
        self.engine.register_pool(self.pool)

    def _req(self, **kw):
        params = dict(
            swap_id="SWAP_01", token_in_symbol="ETH", token_out_symbol="USDC",
            amount_in=10.0, max_slippage_pct=0.005, max_price_impact_pct=0.05,
            current_timestamp_sec=1700000000.0, deadline_seconds=60.0,
        )
        params.update(kw)
        return UniswapSwapRequest(**params)

    # ------------------------------------------------------------------
    # Constant product math
    # ------------------------------------------------------------------
    def test_constant_product_swap_math(self):
        # Independently derived from UniswapV2Library.getAmountOut:
        #   amountInWithFee = 10 * 997          = 9970
        #   numerator       = 9970 * 3_000_000  = 29_910_000_000
        #   denominator     = 1000 * 1000 + 9970 = 1_009_970
        #   amountOut       = 29_910_000_000 / 1_009_970 = 29_614.741032...
        # execPrice = 2_961.4741032 -> impact = 1 - 2961.4741032/3000 = 1.28419656%
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req())

        self.assertTrue(report.is_executed)
        self.assertAlmostEqual(report.expected_amount_out, 29614.741032, places=6)
        self.assertEqual(report.spot_price_before, 3000.0)
        self.assertAlmostEqual(report.execution_price, 2961.4741032, places=6)
        self.assertAlmostEqual(report.price_impact_pct, 1.28419656, places=6)

    def test_reserves_update_conserves_direction_and_grows_k(self):
        """The fee makes k strictly increase; reserves must move in the correct direction."""
        k_before = self.pool.reserve_in * self.pool.reserve_out
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req())

        self.assertAlmostEqual(self.pool.reserve_in, 1010.0, places=9)
        self.assertAlmostEqual(self.pool.reserve_out, 3000000.0 - report.expected_amount_out, places=6)
        self.assertGreater(self.pool.reserve_in * self.pool.reserve_out, k_before)

    def test_min_amount_out_floor_is_derived_from_quote(self):
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req(max_slippage_pct=0.005))
        self.assertAlmostEqual(report.min_amount_out, 29614.741032 * 0.995, places=6)

    def test_deadline_is_absolute_timestamp(self):
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req(deadline_seconds=90.0))
        self.assertEqual(report.deadline_timestamp_sec, 1700000090.0)

    def test_zero_fee_pool_has_no_impact_floor(self):
        """With gamma = 1 the impact is purely the reserve shift: dx/(x+dx)."""
        engine = UniswapDexIntegrationEngine()
        engine.register_pool(AmmPoolState("ZF", "ETH", "USDC", 1000.0, 3000000.0, fee_pct=0.0))
        _, _, _, impact = engine.calculate_swap_output(engine.pools["ZF"], 10.0)
        self.assertAlmostEqual(impact, 10.0 / 1010.0 * 100.0, places=9)

    # ------------------------------------------------------------------
    # Price impact semantics (fee-inclusive canonical vs fee-excluded)
    # ------------------------------------------------------------------
    def test_dust_trade_impact_equals_pool_fee(self):
        """
        Uniswap's computePriceImpact is fee-inclusive, so impact -> fee as size -> 0.
        Pinning this stops the metric being silently redefined and documents why a
        ceiling must be set relative to the pool's fee tier.
        """
        _, _, _, impact = self.engine.calculate_swap_output(self.pool, 1e-9)
        self.assertAlmostEqual(impact, 0.3, places=6)

    def test_reserve_shift_impact_excludes_fee_and_goes_to_zero(self):
        self.assertAlmostEqual(
            self.engine.calculate_reserve_shift_impact_pct(self.pool, 10.0),
            10.0 / 1010.0 * 100.0, places=9,
        )
        self.assertLess(self.engine.calculate_reserve_shift_impact_pct(self.pool, 1e-9), 1e-9)

    def test_reserve_shift_reported_and_below_fee_inclusive_impact(self):
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req())
        self.assertAlmostEqual(report.reserve_shift_impact_pct, 10.0 / 1010.0 * 100.0, places=9)
        self.assertLess(report.reserve_shift_impact_pct, report.price_impact_pct)

    def test_high_price_impact_rejection(self):
        # Swap 500 ETH (50% of pool reserves) -> ~33.4% impact -> rejected
        report = self.engine.execute_swap(
            "ETH_USDC_0.3", self._req(swap_id="SWAP_02", amount_in=500.0, max_slippage_pct=0.01)
        )
        self.assertFalse(report.is_executed)
        self.assertIn("HIGH PRICE IMPACT", report.rejection_reason)

    def test_rejected_swap_does_not_mutate_reserves(self):
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req(amount_in=500.0, max_slippage_pct=0.01))
        self.assertFalse(report.is_executed)
        self.assertEqual(self.pool.reserve_in, 1000.0)
        self.assertEqual(self.pool.reserve_out, 3000000.0)

    # ------------------------------------------------------------------
    # Input validation (regression: negatives previously executed)
    # ------------------------------------------------------------------
    def test_negative_amount_in_is_rejected(self):
        """Previously executed: reserve_in fell to 900 and reserve_out GREW by 332k."""
        with self.assertRaises(ValueError) as ctx:
            self.engine.execute_swap("ETH_USDC_0.3", self._req(amount_in=-100.0))
        self.assertIn("INSUFFICIENT_INPUT_AMOUNT", str(ctx.exception))
        self.assertEqual(self.pool.reserve_in, 1000.0)
        self.assertEqual(self.pool.reserve_out, 3000000.0)

    def test_zero_amount_in_raises_value_error_not_zero_division(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.execute_swap("ETH_USDC_0.3", self._req(amount_in=0.0))
        self.assertIn("INSUFFICIENT_INPUT_AMOUNT", str(ctx.exception))

    def test_non_finite_amount_in_is_rejected(self):
        """inf yielded a NaN output whose impact clamped to 0%, passing the gate and
        writing NaN into reserve_out — poisoning every later quote on that pool."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount_in=bad):
                with self.assertRaises(ValueError):
                    self.engine.calculate_swap_output(self.pool, bad)
        self.assertEqual(self.pool.reserve_out, 3000000.0)

    def test_token_orientation_mismatch_is_rejected(self):
        """A reversed request was computed with the pool's orientation but reported as-sent."""
        with self.assertRaises(ValueError) as ctx:
            self.engine.execute_swap(
                "ETH_USDC_0.3",
                self._req(token_in_symbol="USDC", token_out_symbol="ETH", amount_in=30000.0),
            )
        self.assertIn("TOKEN_ORIENTATION_MISMATCH", str(ctx.exception))
        self.assertEqual(self.pool.reserve_in, 1000.0)

    def test_slippage_unit_confusion_is_rejected(self):
        """0.5 meaning '0.5%' would authorise a 50% worse fill."""
        with self.assertRaises(ValueError) as ctx:
            self.engine.execute_swap("ETH_USDC_0.3", self._req(max_slippage_pct=0.5))
        self.assertIn("FRACTION", str(ctx.exception))
        self.assertLessEqual(MAX_PERMITTED_SLIPPAGE_FRACTION, 0.10)

    def test_impact_ceiling_unit_confusion_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.execute_swap("ETH_USDC_0.3", self._req(max_price_impact_pct=5.0))

    def test_non_positive_deadline_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.execute_swap("ETH_USDC_0.3", self._req(deadline_seconds=0.0))
        self.assertIn("EXPIRED", str(ctx.exception))

    def test_unregistered_pool_raises(self):
        with self.assertRaises(ValueError):
            self.engine.execute_swap("NO_SUCH_POOL", self._req())

    def test_invalid_pool_registration_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.register_pool(AmmPoolState("BAD", "ETH", "USDC", 0.0, 100.0))
        with self.assertRaises(ValueError):
            self.engine.register_pool(AmmPoolState("BAD2", "ETH", "USDC", 1.0, 1.0, fee_pct=30.0))
        with self.assertRaises(ValueError):
            self.engine.register_pool(AmmPoolState("BAD3", "ETH", "ETH", 1.0, 1.0))

    # ------------------------------------------------------------------
    # minAmountOut enforcement (previously computed and never checked)
    # ------------------------------------------------------------------
    def test_realized_output_below_floor_is_rejected(self):
        quote = 29614.741032
        floor = quote * 0.995
        report = self.engine.execute_swap(
            "ETH_USDC_0.3", self._req(), realized_amount_out=floor - 1.0
        )
        self.assertFalse(report.is_executed)
        self.assertIn("INSUFFICIENT_OUTPUT_AMOUNT", report.rejection_reason)
        self.assertEqual(self.pool.reserve_in, 1000.0)

    def test_realized_output_exactly_at_floor_is_accepted(self):
        """The router's comparison is >=, so the boundary must pass, not fail."""
        report = self.engine.execute_swap("ETH_USDC_0.3", self._req())
        floor = report.min_amount_out
        self.setUp()
        boundary = self.engine.execute_swap("ETH_USDC_0.3", self._req(), realized_amount_out=floor)
        self.assertTrue(boundary.is_executed)

    def test_all_failed_gates_are_reported_not_just_the_first(self):
        report = self.engine.execute_swap(
            "ETH_USDC_0.3",
            self._req(amount_in=500.0, max_slippage_pct=0.01, max_price_impact_pct=0.05),
            realized_amount_out=1.0,
        )
        self.assertFalse(report.is_executed)
        self.assertEqual(len(report.rejection_reasons), 2)
        joined = " ".join(report.rejection_reasons)
        self.assertIn("HIGH PRICE IMPACT", joined)
        self.assertIn("INSUFFICIENT_OUTPUT_AMOUNT", joined)

    # ------------------------------------------------------------------
    # Numerical fidelity
    # ------------------------------------------------------------------
    def test_inverted_scale_price_is_not_destroyed_by_rounding(self):
        """A true spot price of 1/3000 previously rounded to 0.0003 (a ~10% error)."""
        engine = UniswapDexIntegrationEngine()
        engine.register_pool(AmmPoolState("USDC_ETH", "USDC", "ETH", 3000000.0, 1000.0, 0.003))
        _, spot, _, _ = engine.calculate_swap_output(engine.pools["USDC_ETH"], 3000.0)
        self.assertAlmostEqual(spot, 1.0 / 3000.0, places=12)

    def test_integer_helper_matches_contract_formula_exactly(self):
        # 10 ETH in wei against 1000 ETH / 3,000,000 USDC (both scaled to 1e18) at 30 bps.
        amount_in = 10 * 10**18
        reserve_in = 1000 * 10**18
        reserve_out = 3_000_000 * 10**18
        expected = (amount_in * 9970 * reserve_out) // (reserve_in * 10000 + amount_in * 9970)
        self.assertEqual(get_amount_out_integer(amount_in, reserve_in, reserve_out, 30), expected)
        # Agrees with the float path to within float precision.
        self.assertAlmostEqual(expected / 10**18, 29614.741032, places=5)

    def test_integer_helper_floors_rather_than_rounds(self):
        """uint256 division truncates; a float quote can therefore overstate output."""
        out = get_amount_out_integer(1, 1_000_000, 3_000_000, 30)
        self.assertIsInstance(out, int)
        self.assertEqual(out, (1 * 9970 * 3_000_000) // (1_000_000 * 10000 + 1 * 9970))
        self.assertEqual(out, 2)  # exact value 2.99..., floored

    def test_integer_helper_supports_pancakeswap_25bps(self):
        amount_in, reserve_in, reserve_out = 10**18, 10**21, 3 * 10**24
        pancake = get_amount_out_integer(amount_in, reserve_in, reserve_out, 25)
        uniswap = get_amount_out_integer(amount_in, reserve_in, reserve_out, 30)
        self.assertEqual(pancake, (amount_in * 9975 * reserve_out) // (reserve_in * 10000 + amount_in * 9975))
        self.assertGreater(pancake, uniswap)  # lower fee -> more output

    def test_integer_helper_rejects_contract_preconditions(self):
        with self.assertRaises(ValueError):
            get_amount_out_integer(0, 10**21, 10**21)
        with self.assertRaises(ValueError):
            get_amount_out_integer(-1, 10**21, 10**21)
        with self.assertRaises(ValueError):
            get_amount_out_integer(10**18, 0, 10**21)
        with self.assertRaises(ValueError):
            get_amount_out_integer(10**18, 10**21, 10**21, fee_bps=10_000)
        with self.assertRaises(TypeError):
            get_amount_out_integer(1.5, 10**21, 10**21)


if __name__ == '__main__':
    unittest.main()
