import unittest

from capital_efficiency_across_cross_margined_strategies import (
    CrossMarginOptimizer,
    MarginInputError,
    Position,
    net_positions_by_symbol,
)


class TestCrossMarginOptimizer(unittest.TestCase):

    def setUp(self):
        self.corr_matrix = {
            "BTC": {"ETH": 0.90, "SOL": 0.70},
            "ETH": {"SOL": 0.80},
        }
        # Haircut of 0.80 means a 0.90 correlation acts as 0.72 for offset
        self.optimizer = CrossMarginOptimizer(self.corr_matrix, correlation_haircut=0.80)

    def test_isolated_margin_no_offset(self):
        # Two longs. They don't offset.
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("ETH", delta_usd=50000, base_margin_usd=5000)

        report = self.optimizer.calculate_margin([pos1, pos2])

        self.assertEqual(report.isolated_margin_usd, 15000)
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 15000)
        self.assertEqual(report.capital_efficiency_ratio, 1.0)
        self.assertEqual(report.offsets, ())

    def test_perfect_hedge_high_correlation(self):
        # Long BTC, Short ETH. Highly correlated (0.90)
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("ETH", delta_usd=-100000, base_margin_usd=10000)

        report = self.optimizer.calculate_margin([pos1, pos2])

        self.assertEqual(report.isolated_margin_usd, 20000)

        # Max overlap is 10000.
        # Effective corr = 0.90 * 0.80 = 0.72
        # Offset = 10000 * 0.72 = 7200
        self.assertAlmostEqual(report.total_offset_usd, 7200, places=2)

        # Cross Margin = 20000 - 7200 = 12800
        self.assertAlmostEqual(report.cross_margin_usd, 12800, places=2)

        # CER = 20000 / 12800 = 1.5625
        self.assertAlmostEqual(report.capital_efficiency_ratio, 1.5625)

        # The credit is auditable leg by leg.
        self.assertEqual(len(report.offsets), 1)
        credit = report.offsets[0]
        self.assertEqual((credit.long_symbol, credit.short_symbol), ("BTC", "ETH"))
        self.assertAlmostEqual(credit.credit_rate, 0.72)
        self.assertEqual(credit.source, "correlation")

    def test_uncorrelated_assets(self):
        # Long BTC, Short GOLD (Correlation not in matrix -> assumed 0.0)
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("GOLD", delta_usd=-100000, base_margin_usd=5000)

        report = self.optimizer.calculate_margin([pos1, pos2])

        self.assertEqual(report.isolated_margin_usd, 15000)
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 15000)

    def test_offset_is_capped_by_the_smaller_leg(self):
        # SPAN-style: the credit applies to the smaller of the two leg values.
        # min(10000, 3000) = 3000; 3000 * 0.72 = 2160.
        pos1 = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        pos2 = Position("ETH", delta_usd=-30000, base_margin_usd=3000)

        report = self.optimizer.calculate_margin([pos1, pos2])

        self.assertAlmostEqual(report.total_offset_usd, 2160, places=2)
        self.assertAlmostEqual(report.cross_margin_usd, 10840, places=2)


class TestSpreadPriorityDeterminism(unittest.TestCase):
    """
    Spreads must form highest-credit-first, not in whatever order positions
    arrive in. A margin number that changes when the position list is shuffled
    cannot be reconciled or audited.
    """

    def setUp(self):
        self.optimizer = CrossMarginOptimizer(
            {"BTC": {"ETH": 0.90, "SOL": 0.70}}, correlation_haircut=0.80
        )
        # One long competing for two shorts. BTC/ETH credit = 0.72,
        # BTC/SOL credit = 0.56. The long's 10000 can only be spread once.
        self.long_btc = Position("BTC", delta_usd=100000, base_margin_usd=10000)
        self.short_eth = Position("ETH", delta_usd=-100000, base_margin_usd=10000)
        self.short_sol = Position("SOL", delta_usd=-100000, base_margin_usd=10000)

    def test_best_credit_spread_is_formed_first(self):
        # Independently: the whole 10000 of BTC goes to the 0.72 pair,
        # offset = 10000 * 0.72 = 7200. The 0.56 SOL pair gets nothing left.
        report = self.optimizer.calculate_margin(
            [self.long_btc, self.short_sol, self.short_eth]
        )

        self.assertEqual(report.isolated_margin_usd, 30000)
        self.assertAlmostEqual(report.total_offset_usd, 7200, places=2)
        self.assertAlmostEqual(report.cross_margin_usd, 22800, places=2)
        self.assertEqual(len(report.offsets), 1)
        self.assertEqual(report.offsets[0].short_symbol, "ETH")

    def test_result_is_independent_of_position_order(self):
        orderings = [
            [self.long_btc, self.short_sol, self.short_eth],
            [self.long_btc, self.short_eth, self.short_sol],
            [self.short_sol, self.short_eth, self.long_btc],
            [self.short_eth, self.short_sol, self.long_btc],
        ]
        results = [
            self.optimizer.calculate_margin(order).cross_margin_usd for order in orderings
        ]
        for value in results:
            self.assertAlmostEqual(value, results[0], places=9)


class TestCreditRateOverrides(unittest.TestCase):
    """A published exchange credit rate must win over a correlation proxy."""

    def test_published_rate_bypasses_correlation_and_haircut(self):
        optimizer = CrossMarginOptimizer(
            {"CORN": {"SOYB": 0.30}},
            correlation_haircut=0.50,
            credit_rate_overrides={"CORN": {"SOYB": 0.65}},
        )
        report = optimizer.calculate_margin(
            [
                Position("CORN", delta_usd=100000, base_margin_usd=10000),
                Position("SOYB", delta_usd=-100000, base_margin_usd=10000),
            ]
        )

        # 10000 * 0.65 = 6500, with no haircut applied on top.
        self.assertAlmostEqual(report.total_offset_usd, 6500, places=2)
        self.assertAlmostEqual(report.cross_margin_usd, 13500, places=2)
        self.assertEqual(report.offsets[0].source, "published")

    def test_override_is_read_symmetrically(self):
        optimizer = CrossMarginOptimizer(
            {}, credit_rate_overrides={"SOYB": {"CORN": 0.65}}
        )
        report = optimizer.calculate_margin(
            [
                Position("CORN", delta_usd=100000, base_margin_usd=10000),
                Position("SOYB", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertAlmostEqual(report.total_offset_usd, 6500, places=2)

    def test_published_zero_credit_blocks_the_correlation_proxy(self):
        # OCC grants non-index single-stock class groups no offset at all, even
        # though such names are strongly correlated. An explicit 0.0 must stick.
        optimizer = CrossMarginOptimizer(
            {"AAPL": {"MSFT": 0.85}},
            correlation_haircut=1.0,
            credit_rate_overrides={"AAPL": {"MSFT": 0.0}},
        )
        report = optimizer.calculate_margin(
            [
                Position("AAPL", delta_usd=100000, base_margin_usd=15000),
                Position("MSFT", delta_usd=-100000, base_margin_usd=15000),
            ]
        )
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 30000)


class TestConservatismRules(unittest.TestCase):

    def test_negative_correlation_between_long_and_short_earns_no_credit(self):
        # Long A and short B with rho = -0.80 are risk-additive, not offsetting.
        optimizer = CrossMarginOptimizer({"A": {"B": -0.80}}, correlation_haircut=1.0)
        report = optimizer.calculate_margin(
            [
                Position("A", delta_usd=100000, base_margin_usd=10000),
                Position("B", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertEqual(report.total_offset_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 20000)

    def test_zero_delta_leg_forms_no_spread_but_still_carries_margin(self):
        optimizer = CrossMarginOptimizer({"BTC": {"ETH": 1.0}}, correlation_haircut=1.0)
        report = optimizer.calculate_margin(
            [
                Position("BTC", delta_usd=100000, base_margin_usd=10000),
                Position("ETH", delta_usd=0.0, base_margin_usd=4000),
            ]
        )
        self.assertEqual(report.isolated_margin_usd, 14000)
        self.assertEqual(report.total_offset_usd, 0.0)

    def test_efficiency_ratio_is_bounded_at_two(self):
        # Full 100% credit on a matched pair still consumes both legs, so the
        # credit is half the isolated requirement. CER cannot exceed 2.0.
        optimizer = CrossMarginOptimizer({}, credit_rate_overrides={"A": {"B": 1.0}})
        report = optimizer.calculate_margin(
            [
                Position("A", delta_usd=100000, base_margin_usd=10000),
                Position("B", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertAlmostEqual(report.cross_margin_usd, 10000, places=2)
        self.assertAlmostEqual(report.capital_efficiency_ratio, 2.0, places=9)

    def test_minimum_cross_margin_fraction_floors_the_estimate(self):
        # Floor at 60% of isolated: raw estimate 10000 is below 0.60 * 20000.
        optimizer = CrossMarginOptimizer(
            {},
            credit_rate_overrides={"A": {"B": 1.0}},
            min_cross_margin_fraction=0.60,
        )
        report = optimizer.calculate_margin(
            [
                Position("A", delta_usd=100000, base_margin_usd=10000),
                Position("B", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertTrue(report.floor_applied)
        self.assertAlmostEqual(report.cross_margin_usd, 12000, places=2)
        self.assertAlmostEqual(report.total_offset_usd, 8000, places=2)
        self.assertAlmostEqual(report.capital_efficiency_ratio, 20000 / 12000, places=9)

    def test_floor_not_applied_when_estimate_already_exceeds_it(self):
        optimizer = CrossMarginOptimizer(
            {},
            credit_rate_overrides={"A": {"B": 1.0}},
            min_cross_margin_fraction=0.25,
        )
        report = optimizer.calculate_margin(
            [
                Position("A", delta_usd=100000, base_margin_usd=10000),
                Position("B", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertFalse(report.floor_applied)
        self.assertAlmostEqual(report.cross_margin_usd, 10000, places=2)

    def test_empty_portfolio_reports_unit_efficiency_not_infinity(self):
        optimizer = CrossMarginOptimizer({})
        report = optimizer.calculate_margin([])
        self.assertEqual(report.isolated_margin_usd, 0.0)
        self.assertEqual(report.cross_margin_usd, 0.0)
        self.assertEqual(report.capital_efficiency_ratio, 1.0)


class TestFailClosedValidation(unittest.TestCase):
    """
    Every one of these once produced a plausible-looking number instead of an
    error. An under-reported requirement is the failure mode that liquidates an
    account, so unusable input must raise.
    """

    def test_correlation_above_one_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({"BTC": {"ETH": 1.4}})

    def test_correlation_below_minus_one_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({"BTC": {"ETH": -1.4}})

    def test_nan_correlation_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({"BTC": {"ETH": float("nan")}})

    def test_haircut_outside_unit_interval_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({}, correlation_haircut=1.5)
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({}, correlation_haircut=-0.1)

    def test_credit_rate_override_above_one_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({}, credit_rate_overrides={"A": {"B": 1.2}})

    def test_min_cross_margin_fraction_outside_unit_interval_is_rejected(self):
        with self.assertRaises(MarginInputError):
            CrossMarginOptimizer({}, min_cross_margin_fraction=1.2)

    def test_negative_base_margin_is_rejected(self):
        with self.assertRaises(MarginInputError):
            Position("BTC", delta_usd=100000, base_margin_usd=-10000)

    def test_nan_and_infinite_inputs_are_rejected(self):
        with self.assertRaises(MarginInputError):
            Position("BTC", delta_usd=float("nan"), base_margin_usd=10000)
        with self.assertRaises(MarginInputError):
            Position("BTC", delta_usd=100000, base_margin_usd=float("nan"))
        with self.assertRaises(MarginInputError):
            Position("BTC", delta_usd=float("inf"), base_margin_usd=10000)

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(MarginInputError):
            Position("   ", delta_usd=100000, base_margin_usd=10000)

    def test_non_position_entry_is_rejected(self):
        optimizer = CrossMarginOptimizer({})
        with self.assertRaises(MarginInputError):
            optimizer.calculate_margin([{"symbol": "BTC"}])

    def test_single_position_passed_without_a_list_is_rejected(self):
        optimizer = CrossMarginOptimizer({})
        with self.assertRaises(MarginInputError):
            optimizer.calculate_margin(Position("BTC", 100000, 10000))

    def test_duplicate_symbols_are_rejected_with_a_pointer_to_netting(self):
        optimizer = CrossMarginOptimizer({})
        with self.assertRaises(MarginInputError) as ctx:
            optimizer.calculate_margin(
                [
                    Position("BTC", delta_usd=100000, base_margin_usd=10000),
                    Position("BTC", delta_usd=-100000, base_margin_usd=10000),
                ]
            )
        self.assertIn("net_positions_by_symbol", str(ctx.exception))


class TestNetPositionsBySymbol(unittest.TestCase):
    """
    The multi-strategy case: several sleeves trade the same instrument. The
    account is margined on the net position, so the rows must be netted before
    they are margined.
    """

    def test_offsetting_sleeves_net_to_flat(self):
        # Long 100k and short 100k of BTC is a flat book: no delta, no margin.
        netted = net_positions_by_symbol(
            [
                Position("BTC", delta_usd=100000, base_margin_usd=10000),
                Position("BTC", delta_usd=-100000, base_margin_usd=10000),
            ]
        )
        self.assertEqual(len(netted), 1)
        self.assertEqual(netted[0].delta_usd, 0.0)
        self.assertEqual(netted[0].base_margin_usd, 0.0)

        report = CrossMarginOptimizer({}).calculate_margin(netted)
        self.assertEqual(report.isolated_margin_usd, 0.0)
        self.assertEqual(report.capital_efficiency_ratio, 1.0)

    def test_partial_netting_scales_margin_by_net_delta(self):
        # Rate 10000/100000 = 0.10 per unit; net delta = 100000 - 60000 = 40000.
        # Net margin = 0.10 * 40000 = 4000.
        netted = net_positions_by_symbol(
            [
                Position("BTC", delta_usd=100000, base_margin_usd=10000),
                Position("BTC", delta_usd=-60000, base_margin_usd=6000),
            ]
        )
        self.assertEqual(len(netted), 1)
        self.assertAlmostEqual(netted[0].delta_usd, 40000, places=6)
        self.assertAlmostEqual(netted[0].base_margin_usd, 4000, places=6)

    def test_netting_never_raises_the_requirement(self):
        # Mismatched rates (0.10 and 0.50) would scale the net delta to 45000,
        # far above the 15000 the two rows carry standalone. The cap binds.
        netted = net_positions_by_symbol(
            [
                Position("BTC", delta_usd=100000, base_margin_usd=10000),
                Position("BTC", delta_usd=-10000, base_margin_usd=5000),
            ]
        )
        self.assertAlmostEqual(netted[0].base_margin_usd, 15000, places=6)

    def test_zero_delta_row_margin_is_added_not_netted_away(self):
        # 0.10 * 100000 = 10000, plus the 2000 carried by the flat row.
        netted = net_positions_by_symbol(
            [
                Position("BTC", delta_usd=100000, base_margin_usd=10000),
                Position("BTC", delta_usd=0.0, base_margin_usd=2000),
            ]
        )
        self.assertAlmostEqual(netted[0].base_margin_usd, 12000, places=6)

    def test_all_flat_rows_keep_their_summed_margin(self):
        netted = net_positions_by_symbol(
            [
                Position("SPX", delta_usd=0.0, base_margin_usd=3000),
                Position("SPX", delta_usd=0.0, base_margin_usd=2000),
            ]
        )
        self.assertEqual(netted[0].delta_usd, 0.0)
        self.assertAlmostEqual(netted[0].base_margin_usd, 5000, places=6)

    def test_distinct_symbols_pass_through_untouched(self):
        positions = [
            Position("BTC", delta_usd=100000, base_margin_usd=10000),
            Position("ETH", delta_usd=-50000, base_margin_usd=5000),
        ]
        netted = net_positions_by_symbol(positions)
        self.assertEqual(len(netted), 2)
        self.assertEqual({p.symbol for p in netted}, {"BTC", "ETH"})

    def test_netted_output_is_accepted_by_the_optimizer(self):
        optimizer = CrossMarginOptimizer({"BTC": {"ETH": 0.90}}, correlation_haircut=0.80)
        raw = [
            Position("BTC", delta_usd=100000, base_margin_usd=10000),
            Position("BTC", delta_usd=-60000, base_margin_usd=6000),
            Position("ETH", delta_usd=-100000, base_margin_usd=10000),
        ]
        report = optimizer.calculate_margin(net_positions_by_symbol(raw))

        # Netted book: BTC long 40000 delta / 4000 margin, ETH short 10000 margin.
        # Isolated = 14000. Spread on min(4000, 10000) = 4000 at 0.72 = 2880.
        self.assertAlmostEqual(report.isolated_margin_usd, 14000, places=6)
        self.assertAlmostEqual(report.total_offset_usd, 2880, places=6)
        self.assertAlmostEqual(report.cross_margin_usd, 11120, places=6)


if __name__ == '__main__':
    unittest.main()
