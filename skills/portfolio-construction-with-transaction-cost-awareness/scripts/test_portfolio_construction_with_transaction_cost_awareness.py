import unittest
from portfolio_construction_with_transaction_cost_awareness import (
    Config, Engine, PortfolioConstructionEngine,
    AssetAlphaSpec, CostSpec, TCAwarePortfolioReport, AssetTradeDecision,
    BPS_PER_UNIT, MAX_ABS_WEIGHT,
)


class TestPortfolioConstructionWithTransactionCostAwareness(unittest.TestCase):

    def setUp(self):
        self.config = Config(rebalance_threshold=0.02)
        self.legacy_engine = Engine(self.config)
        self.tc_engine = PortfolioConstructionEngine(self.config)
        # Zero-impact cost spec: isolates the proportional term so expected values
        # can be derived by hand without the quadratic term confounding them.
        self.linear_only = CostSpec(commission_rate=0.0005, spread_cost_bps=5.0, impact_coeff=0.0)

    def test_legacy_init_and_run(self):
        self.assertTrue(self.legacy_engine.config.enabled)
        self.assertTrue(self.legacy_engine.run())

    def test_no_trade_buffer_band_suppression(self):
        # AAPL proposed shift: 40% -> 41% (1% shift <= 2% threshold => Suppressed)
        # MSFT proposed shift: 30% -> 40% (10% shift > 2% threshold => Traded)
        assets = [
            AssetAlphaSpec("AAPL", expected_return=0.10, current_weight=0.40, target_weight=0.41),
            AssetAlphaSpec("MSFT", expected_return=0.15, current_weight=0.30, target_weight=0.40),
        ]
        report = self.tc_engine.construct_portfolio(assets)

        self.assertEqual(report.status, "REBALANCED_COST_OPTIMIZED")
        self.assertIn("AAPL", report.suppressed_symbols)
        self.assertIn("MSFT", report.traded_symbols)
        self.assertEqual(report.total_turnover, 0.10)  # 10% MSFT shift
        self.assertGreater(report.total_transaction_cost, 0.0)
        # The suppressed asset keeps its CURRENT weight, the traded one takes target.
        self.assertAlmostEqual(report.final_weights["AAPL"], 0.40)
        self.assertAlmostEqual(report.final_weights["MSFT"], 0.40)

    # ------------------------------------------------------------------
    # Cost arithmetic, against values derived independently of the engine
    # ------------------------------------------------------------------

    def test_proportional_cost_matches_hand_calculation(self):
        # Single 10% buy. Proportional rate = 5 bps commission + 5 bps spread = 10 bps.
        # Cost = 0.0010 * 0.10 = 0.00010 exactly. Impact disabled.
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [AssetAlphaSpec("SPY", expected_return=0.08, current_weight=0.30, target_weight=0.40)]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.total_transaction_cost, 0.00010, places=9)
        decision = report.trade_decisions[0]
        self.assertAlmostEqual(decision.proportional_cost, 0.00010, places=9)
        self.assertEqual(decision.impact_cost, 0.0)

    def test_quadratic_impact_matches_hand_calculation(self):
        # impact_coeff 0.5, dw = 0.20  =>  0.5 * 0.04 = 0.020 exactly.
        # Proportional term zeroed so the quadratic term stands alone.
        spec = CostSpec(commission_rate=0.0, spread_cost_bps=0.0, impact_coeff=0.5)
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.02), spec)
        assets = [AssetAlphaSpec("SPY", expected_return=0.08, current_weight=0.20, target_weight=0.40)]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.total_transaction_cost, 0.020, places=9)

    def test_impact_is_quadratic_not_linear(self):
        # Doubling trade size must QUADRUPLE the impact cost, not double it.
        # This is the property that distinguishes the model from a linear one.
        spec = CostSpec(commission_rate=0.0, spread_cost_bps=0.0, impact_coeff=0.5)
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.01), spec)

        small = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.0, current_weight=0.10, target_weight=0.15)])   # dw = 0.05
        large = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.0, current_weight=0.10, target_weight=0.20)])   # dw = 0.10

        self.assertAlmostEqual(large.total_transaction_cost / small.total_transaction_cost, 4.0, places=6)

    def test_sell_and_buy_cost_the_same(self):
        # Impact uses dw**2, proportional uses |dw|: a 10% sell must cost exactly
        # what a 10% buy costs. Guards against a sign leak into the cost terms.
        engine = PortfolioConstructionEngine(self.config)
        buy = engine.construct_portfolio([AssetAlphaSpec("A", 0.0, current_weight=0.30, target_weight=0.40)])
        sell = engine.construct_portfolio([AssetAlphaSpec("A", 0.0, current_weight=0.40, target_weight=0.30)])

        self.assertAlmostEqual(buy.total_transaction_cost, sell.total_transaction_cost, places=12)
        self.assertGreater(sell.total_transaction_cost, 0.0)

    def test_net_return_is_gross_minus_cost(self):
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        # Final weights: A traded to 0.50, B suppressed at 0.20.
        # Gross = 0.50*0.10 + 0.20*0.05 = 0.050 + 0.010 = 0.060 exactly.
        # Cost  = 0.0010 * 0.20 = 0.00020  (A moves 0.30 -> 0.50).
        # Net   = 0.060 - 0.0002 = 0.0598.
        assets = [
            AssetAlphaSpec("A", expected_return=0.10, current_weight=0.30, target_weight=0.50),
            AssetAlphaSpec("B", expected_return=0.05, current_weight=0.20, target_weight=0.21),
        ]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.gross_expected_return, 0.0600, places=6)
        self.assertAlmostEqual(report.total_transaction_cost, 0.00020, places=9)
        self.assertAlmostEqual(report.net_expected_return, 0.0598, places=6)

    def test_gross_return_uses_final_not_target_weights(self):
        # B's trade is suppressed, so its 0.90 target return must NOT be credited.
        # Regression guard: crediting target weights would report 0.30*0.0 + 0.90*1.0.
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [AssetAlphaSpec("B", expected_return=1.0, current_weight=0.30, target_weight=0.31)]
        report = engine.construct_portfolio(assets)

        self.assertEqual(report.traded_symbols, [])
        self.assertAlmostEqual(report.gross_expected_return, 0.30, places=6)

    # ------------------------------------------------------------------
    # No-trade band boundary
    # ------------------------------------------------------------------

    def test_delta_exactly_at_threshold_is_suppressed(self):
        # The band is inclusive: |dw| == threshold suppresses. Pinning the
        # documented boundary so it cannot drift silently.
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.02), self.linear_only)
        assets = [AssetAlphaSpec("A", 0.10, current_weight=0.40, target_weight=0.42)]
        report = engine.construct_portfolio(assets)

        self.assertIn("A", report.suppressed_symbols)
        self.assertEqual(report.total_transaction_cost, 0.0)
        self.assertEqual(report.total_turnover, 0.0)

    def test_threshold_boundary_survives_float_representation_error(self):
        # Regression: 0.20 -> 0.18 is exactly a 2% move, but in binary floating point
        # 0.18 - 0.20 == -0.020000000000000018, which a naive `<=` sends to the
        # trading path — incurring precisely the cost the band exists to suppress.
        # Whether a boundary case suppressed used to depend on the binary
        # representation of the particular weights rather than on the contract.
        self.assertGreater(abs(0.18 - 0.20), 0.02)  # documents the raw float hazard

        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.02), self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("TLT", -0.01, current_weight=0.20, target_weight=0.18)])

        self.assertIn("TLT", report.suppressed_symbols)
        self.assertEqual(report.total_turnover, 0.0)
        self.assertEqual(report.total_transaction_cost, 0.0)
        self.assertAlmostEqual(report.final_weights["TLT"], 0.20, places=12)

    def test_threshold_boundary_tolerance_does_not_swallow_real_trades(self):
        # The tolerance must absorb representation error only, not a genuine move.
        # 0.0201 against a 0.02 band is 0.5% larger — comfortably a real trade.
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.02), self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.20, target_weight=0.1799)])

        self.assertIn("A", report.traded_symbols)
        self.assertAlmostEqual(report.total_turnover, 0.0201, places=9)

    def test_delta_just_beyond_threshold_trades(self):
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.02), self.linear_only)
        assets = [AssetAlphaSpec("A", 0.10, current_weight=0.40, target_weight=0.4201)]
        report = engine.construct_portfolio(assets)

        self.assertIn("A", report.traded_symbols)
        self.assertGreater(report.total_transaction_cost, 0.0)

    def test_zero_threshold_trades_any_nonzero_delta(self):
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.0), self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.40, target_weight=0.4001)])

        self.assertIn("A", report.traded_symbols)

    def test_zero_delta_is_never_counted_as_a_trade(self):
        # current == target with a zero threshold must still be a no-op, not a
        # zero-size "trade" polluting the traded list.
        engine = PortfolioConstructionEngine(Config(rebalance_threshold=0.0), self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.40, target_weight=0.40)])

        self.assertEqual(report.traded_symbols, [])
        self.assertIn("A", report.suppressed_symbols)
        self.assertEqual(report.total_turnover, 0.0)

    # ------------------------------------------------------------------
    # Band-edge policy (Constantinides 1986 / Davis-Norman 1990)
    # ------------------------------------------------------------------

    def test_trade_to_band_edge_stops_at_boundary_on_a_buy(self):
        config = Config(rebalance_threshold=0.02, trade_to_band_edge=True)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        # 0.30 -> 0.40 proposed; band edge is 0.30 + 0.02 = 0.32.
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.40)])

        self.assertAlmostEqual(report.final_weights["A"], 0.32, places=9)
        self.assertAlmostEqual(report.total_turnover, 0.02, places=9)
        decision = report.trade_decisions[0]
        self.assertAlmostEqual(decision.proposed_delta, 0.10, places=9)
        self.assertAlmostEqual(decision.executed_delta, 0.02, places=9)

    def test_trade_to_band_edge_stops_at_boundary_on_a_sell(self):
        config = Config(rebalance_threshold=0.02, trade_to_band_edge=True)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        # 0.40 -> 0.30 proposed; band edge is 0.40 - 0.02 = 0.38 (direction preserved).
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.40, target_weight=0.30)])

        self.assertAlmostEqual(report.final_weights["A"], 0.38, places=9)
        self.assertAlmostEqual(report.trade_decisions[0].executed_delta, -0.02, places=9)

    def test_band_edge_costs_are_charged_on_executed_not_proposed_delta(self):
        # Executed delta 0.02 at 10 bps = 0.00002. Charging the proposed 0.10
        # would give 0.00010 - a 5x overstatement.
        config = Config(rebalance_threshold=0.02, trade_to_band_edge=True)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.40)])

        self.assertAlmostEqual(report.total_transaction_cost, 0.00002, places=9)

    def test_band_edge_is_cheaper_than_full_target_snap(self):
        assets = [AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.50)]
        full = PortfolioConstructionEngine(
            Config(rebalance_threshold=0.02, trade_to_band_edge=False)).construct_portfolio(assets)
        edge = PortfolioConstructionEngine(
            Config(rebalance_threshold=0.02, trade_to_band_edge=True)).construct_portfolio(assets)

        self.assertLess(edge.total_transaction_cost, full.total_transaction_cost)
        self.assertLess(edge.total_turnover, full.total_turnover)

    def test_band_edge_never_overshoots_the_target(self):
        # If the proposed move is only slightly beyond the band, moving to the band
        # edge must not land past the target.
        config = Config(rebalance_threshold=0.02, trade_to_band_edge=True)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.325)])

        self.assertAlmostEqual(report.final_weights["A"], 0.32, places=9)
        self.assertLessEqual(report.final_weights["A"], 0.325)

    # ------------------------------------------------------------------
    # Turnover conventions and the limit
    # ------------------------------------------------------------------

    def test_turnover_is_two_way_and_one_way_is_half(self):
        # A sells 20%, B buys 20%: two-way = 0.40, one-way = 0.20.
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.50, target_weight=0.30),
            AssetAlphaSpec("B", 0.10, current_weight=0.30, target_weight=0.50),
        ]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.total_turnover, 0.40, places=9)
        self.assertAlmostEqual(report.one_way_turnover, 0.20, places=9)

    def test_turnover_limit_breach_is_flagged(self):
        config = Config(rebalance_threshold=0.02, max_turnover_limit=0.30)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.50, target_weight=0.30),
            AssetAlphaSpec("B", 0.10, current_weight=0.30, target_weight=0.50),
        ]
        report = engine.construct_portfolio(assets)  # two-way turnover 0.40 > 0.30

        self.assertTrue(report.turnover_limit_breached)
        self.assertEqual(report.status, "TURNOVER_LIMIT_EXCEEDED")

    def test_turnover_limit_is_advisory_weights_returned_unclamped(self):
        # Documented behaviour: the engine flags but does not clamp. If this ever
        # changes to hard enforcement, SKILL.md and standards.md must change too.
        config = Config(rebalance_threshold=0.02, max_turnover_limit=0.05)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.10, target_weight=0.60)])

        self.assertTrue(report.turnover_limit_breached)
        self.assertAlmostEqual(report.final_weights["A"], 0.60, places=9)
        self.assertAlmostEqual(report.total_turnover, 0.50, places=9)

    def test_turnover_exactly_at_limit_is_not_a_breach(self):
        config = Config(rebalance_threshold=0.02, max_turnover_limit=0.50)
        engine = PortfolioConstructionEngine(config, self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", 0.10, current_weight=0.10, target_weight=0.60)])

        self.assertAlmostEqual(report.total_turnover, 0.50, places=9)
        self.assertFalse(report.turnover_limit_breached)
        self.assertEqual(report.status, "REBALANCED_COST_OPTIMIZED")

    # ------------------------------------------------------------------
    # Budget / self-financing audit
    # ------------------------------------------------------------------

    def test_partial_suppression_is_reported_as_not_self_financing(self):
        # This is the SKILL.md verification scenario. AAPL's +1% is suppressed while
        # MSFT's +10% executes, so the book grows by 10% of portfolio value: that
        # funding has to come from cash, and the engine must say so.
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [
            AssetAlphaSpec("AAPL", 0.10, current_weight=0.40, target_weight=0.41),
            AssetAlphaSpec("MSFT", 0.15, current_weight=0.30, target_weight=0.40),
        ]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.current_weight_sum, 0.70, places=9)
        self.assertAlmostEqual(report.final_weight_sum, 0.80, places=9)
        self.assertAlmostEqual(report.net_weight_change, 0.10, places=9)
        self.assertFalse(report.is_self_financing)

    def test_offsetting_trades_are_self_financing(self):
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.50, target_weight=0.30),
            AssetAlphaSpec("B", 0.10, current_weight=0.50, target_weight=0.70),
        ]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(report.net_weight_change, 0.0, places=12)
        self.assertTrue(report.is_self_financing)

    def test_all_suppressed_is_trivially_self_financing(self):
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.50, target_weight=0.51),
            AssetAlphaSpec("B", 0.10, current_weight=0.50, target_weight=0.49),
        ]
        report = engine.construct_portfolio(assets)

        self.assertEqual(report.traded_symbols, [])
        self.assertTrue(report.is_self_financing)
        self.assertEqual(report.total_transaction_cost, 0.0)

    # ------------------------------------------------------------------
    # Short / negative weights
    # ------------------------------------------------------------------

    def test_short_position_weights_are_supported(self):
        # Crossing from long to short is a legitimate 30% trade, priced on |dw|.
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        report = engine.construct_portfolio(
            [AssetAlphaSpec("A", -0.05, current_weight=0.10, target_weight=-0.20)])

        self.assertIn("A", report.traded_symbols)
        self.assertAlmostEqual(report.total_turnover, 0.30, places=9)
        self.assertAlmostEqual(report.final_weights["A"], -0.20, places=9)
        # Gross return on a short with a negative expected return is positive.
        self.assertAlmostEqual(report.gross_expected_return, 0.01, places=6)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_empty_asset_list_raises(self):
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio([])

    def test_none_asset_list_raises(self):
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(None)

    def test_duplicate_symbol_raises(self):
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.40),
            AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.40),
        ]
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(assets)

    def test_nan_weight_raises_instead_of_propagating(self):
        # Regression: a NaN weight previously flowed straight through to a NaN
        # net_expected_return, which compares False against every risk threshold.
        assets = [AssetAlphaSpec("A", 0.10, current_weight=float("nan"), target_weight=0.40)]
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(assets)

    def test_infinite_expected_return_raises(self):
        assets = [AssetAlphaSpec("A", float("inf"), current_weight=0.30, target_weight=0.40)]
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(assets)

    def test_percentage_instead_of_fraction_weight_raises(self):
        # 40 meaning "40%" instead of 0.40 inflates the quadratic impact term by
        # four orders of magnitude, so it must be rejected, not silently priced.
        assets = [AssetAlphaSpec("A", 0.10, current_weight=40.0, target_weight=45.0)]
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(assets)
        self.assertEqual(MAX_ABS_WEIGHT, 10.0)

    def test_non_numeric_weight_raises_type_error(self):
        assets = [AssetAlphaSpec("A", 0.10, current_weight="0.30", target_weight=0.40)]
        with self.assertRaises(TypeError):
            self.tc_engine.construct_portfolio(assets)

    def test_empty_symbol_raises(self):
        assets = [AssetAlphaSpec("   ", 0.10, current_weight=0.30, target_weight=0.40)]
        with self.assertRaises(ValueError):
            self.tc_engine.construct_portfolio(assets)

    def test_negative_rebalance_threshold_raises(self):
        with self.assertRaises(ValueError):
            PortfolioConstructionEngine(Config(rebalance_threshold=-0.01))

    def test_non_positive_turnover_limit_raises(self):
        with self.assertRaises(ValueError):
            PortfolioConstructionEngine(Config(max_turnover_limit=0.0))

    def test_negative_cost_parameter_raises(self):
        with self.assertRaises(ValueError):
            PortfolioConstructionEngine(self.config, CostSpec(commission_rate=-0.0001))
        with self.assertRaises(ValueError):
            PortfolioConstructionEngine(self.config, CostSpec(impact_coeff=-1.0))

    # ------------------------------------------------------------------
    # Disabled engine
    # ------------------------------------------------------------------

    def test_disabled_engine_returns_explicit_status(self):
        engine = PortfolioConstructionEngine(Config(enabled=False))
        assets = [AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.90)]
        report = engine.construct_portfolio(assets)

        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertEqual(report.traded_symbols, [])
        self.assertEqual(report.total_transaction_cost, 0.0)
        # A disabled engine must not be mistaken for a considered no-trade decision.
        self.assertIn("disabled", report.audit_notes.lower())

    # ------------------------------------------------------------------
    # Report shape
    # ------------------------------------------------------------------

    def test_report_types_and_per_asset_decisions(self):
        engine = PortfolioConstructionEngine(self.config, self.linear_only)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.50),
            AssetAlphaSpec("B", 0.05, current_weight=0.20, target_weight=0.205),
        ]
        report = engine.construct_portfolio(assets)

        self.assertIsInstance(report, TCAwarePortfolioReport)
        self.assertEqual(report.total_assets, 2)
        self.assertEqual(len(report.trade_decisions), 2)
        for decision in report.trade_decisions:
            self.assertIsInstance(decision, AssetTradeDecision)
            self.assertAlmostEqual(
                decision.total_cost, decision.proportional_cost + decision.impact_cost, places=12)
            self.assertAlmostEqual(
                decision.executed_delta, decision.final_weight - decision.current_weight, places=12)
        # Every asset appears exactly once across the two symbol lists.
        self.assertEqual(
            sorted(report.traded_symbols + report.suppressed_symbols), ["A", "B"])
        self.assertEqual(sorted(report.final_weights), ["A", "B"])

    def test_total_cost_equals_sum_of_per_asset_costs(self):
        engine = PortfolioConstructionEngine(self.config)
        assets = [
            AssetAlphaSpec("A", 0.10, current_weight=0.30, target_weight=0.50),
            AssetAlphaSpec("B", 0.05, current_weight=0.20, target_weight=0.05),
            AssetAlphaSpec("C", 0.05, current_weight=0.10, target_weight=0.105),
        ]
        report = engine.construct_portfolio(assets)

        self.assertAlmostEqual(
            report.total_transaction_cost,
            round(sum(d.total_cost for d in report.trade_decisions), 6),
            places=9,
        )

    def test_bps_constant(self):
        self.assertEqual(BPS_PER_UNIT, 10_000.0)


if __name__ == '__main__':
    unittest.main()
