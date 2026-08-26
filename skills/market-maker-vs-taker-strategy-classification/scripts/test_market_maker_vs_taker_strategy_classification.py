"""
Unit tests for the maker/taker strategy classification engine.

Expected values are derived by hand from the inputs (never by re-running the
engine's own expression), so a formula change fails the test rather than moving
the target with it.
"""
import unittest
from dataclasses import FrozenInstanceError

from market_maker_vs_taker_strategy_classification import (
    ClassificationBasis,
    ExecutedTradeLog,
    LiquidityCategory,
    MarketMakerVsTakerClassifierEngine,
    StrategyClassificationReport,
    TradeLogError,
)


def fills(count, *, prefix, symbol, is_maker, price, quantity, fee,
          liquidity_category=None):
    """Builds `count` identical fills with unique trade ids."""
    return [
        ExecutedTradeLog(
            trade_id=f"{prefix}_{i}",
            symbol=symbol,
            is_maker=is_maker,
            executed_price=price,
            quantity=quantity,
            fee_paid_usd=fee,
            liquidity_category=liquidity_category,
        )
        for i in range(count)
    ]


class TestClassificationCore(unittest.TestCase):
    """Happy-path classification, ratios, and fee attribution."""

    def setUp(self):
        self.engine = MarketMakerVsTakerClassifierEngine(
            ClassificationBasis.NOTIONAL,
            pure_maker_threshold_ratio=0.80,
            pure_taker_threshold_ratio=0.20,
        )

    def test_pure_maker_strategy_classification(self):
        # 90 maker fills (rebate -$1.00 each), 10 taker fills (fee +$5.00 each),
        # all 100 shares at $150.00.
        #   maker notional  = 90 * 100 * 150 = $1,350,000
        #   taker notional  = 10 * 100 * 150 =   $150,000
        #   maker ratio     = 1,350,000 / 1,500,000 = 0.90 >= 0.80 -> PURE_MAKER
        #   net fees        = -90 + 50 = -$40.00 (net rebate)
        #   effective rate  = -40 / 1,500,000 * 10,000 = -0.266666... bps
        trades = (
            fills(90, prefix="M", symbol="AAPL", is_maker=True,
                  price=150.0, quantity=100.0, fee=-1.0)
            + fills(10, prefix="T", symbol="AAPL", is_maker=False,
                    price=150.0, quantity=100.0, fee=5.0)
        )

        report = self.engine.classify_strategy_executions("HFT_MAKER_01", trades)

        self.assertIsInstance(report, StrategyClassificationReport)
        self.assertEqual(report.status, "STRATEGY_CLASSIFICATION_SUCCESS")
        self.assertEqual(report.classification, "PURE_MAKER_STRATEGY")
        self.assertEqual(report.classification_basis, "NOTIONAL")
        self.assertEqual(report.maker_volume_ratio, 0.90)
        self.assertEqual(report.maker_notional_ratio, 0.90)
        self.assertEqual(report.classification_ratio, 0.90)
        self.assertEqual(report.maker_trades_count, 90)
        self.assertEqual(report.taker_trades_count, 10)
        self.assertEqual(report.excluded_trades_count, 0)
        self.assertEqual(report.total_gross_notional_usd, 1_500_000.00)
        self.assertEqual(report.maker_gross_notional_usd, 1_350_000.00)
        self.assertEqual(report.taker_gross_notional_usd, 150_000.00)
        self.assertEqual(report.net_fees_paid_usd, -40.00)
        self.assertEqual(report.maker_fees_paid_usd, -90.00)
        self.assertEqual(report.taker_fees_paid_usd, 50.00)
        self.assertAlmostEqual(report.effective_fee_rate_bps, -0.2667, places=4)
        # Per-side rates are measured against that side's own notional.
        self.assertAlmostEqual(report.maker_effective_fee_bps, -0.6667, places=4)
        self.assertAlmostEqual(report.taker_effective_fee_bps, 3.3333, places=4)
        self.assertEqual(report.symbols, ["AAPL"])
        self.assertEqual(report.warnings, [])

    def test_skill_md_verification_scenario(self):
        # The scenario documented in SKILL.md: 100 fills, 90 maker / 10 taker,
        # $1,000,000 gross notional, -$100.00 net rebate -> R = 0.90, -1.0 bps.
        trades = (
            fills(90, prefix="M", symbol="ETH-USD", is_maker=True,
                  price=100.0, quantity=100.0, fee=-2.0)
            + fills(10, prefix="T", symbol="ETH-USD", is_maker=False,
                    price=100.0, quantity=100.0, fee=8.0)
        )

        report = self.engine.classify_strategy_executions("DOC_EXAMPLE", trades)

        self.assertEqual(report.classification, "PURE_MAKER_STRATEGY")
        self.assertEqual(report.classification_ratio, 0.90)
        self.assertEqual(report.total_gross_notional_usd, 1_000_000.00)
        self.assertEqual(report.net_fees_paid_usd, -100.00)
        self.assertEqual(report.effective_fee_rate_bps, -1.0)

    def test_pure_taker_strategy_classification(self):
        # 100 taker fills of 1.0 unit at $50,000 -> $5,000,000 notional,
        # $25.00 fee each -> $2,500 total -> 2,500 / 5,000,000 * 10,000 = 5 bps.
        trades = fills(100, prefix="T", symbol="BTC-USD", is_maker=False,
                       price=50_000.0, quantity=1.0, fee=25.0)

        report = self.engine.classify_strategy_executions("MOMENTUM_TAKER_02", trades)

        self.assertEqual(report.classification, "PURE_TAKER_STRATEGY")
        self.assertEqual(report.maker_notional_ratio, 0.0)
        self.assertEqual(report.classification_ratio, 0.0)
        self.assertEqual(report.net_fees_paid_usd, 2500.0)
        self.assertEqual(report.effective_fee_rate_bps, 5.0)
        self.assertIsNone(report.maker_effective_fee_bps)
        self.assertEqual(report.taker_effective_fee_bps, 5.0)

    def test_hybrid_classification_between_thresholds(self):
        # 50/50 by notional -> strictly between 0.20 and 0.80.
        trades = (
            fills(5, prefix="M", symbol="MSFT", is_maker=True,
                  price=400.0, quantity=10.0, fee=-0.5)
            + fills(5, prefix="T", symbol="MSFT", is_maker=False,
                    price=400.0, quantity=10.0, fee=1.5)
        )

        report = self.engine.classify_strategy_executions("HYBRID_03", trades)

        self.assertEqual(report.classification, "HYBRID_MAKER_TAKER_STRATEGY")
        self.assertEqual(report.classification_ratio, 0.5)
        # 5 * -0.5 + 5 * 1.5 = +$5.00 net fee on $40,000 notional = 1.25 bps.
        self.assertEqual(report.net_fees_paid_usd, 5.0)
        self.assertEqual(report.effective_fee_rate_bps, 1.25)

    def test_maker_fee_without_rebate_is_flagged(self):
        # Standard crypto tiers charge a positive maker fee; a maker-dominant
        # posture there captures no rebate and the report must say so.
        trades = fills(10, prefix="M", symbol="SOL-USD", is_maker=True,
                       price=200.0, quantity=5.0, fee=0.8)

        report = self.engine.classify_strategy_executions("CRYPTO_MAKER_04", trades)

        self.assertEqual(report.classification, "PURE_MAKER_STRATEGY")
        self.assertEqual(report.maker_fees_paid_usd, 8.0)
        # 8 / 10,000 * 10,000 = 8 bps paid, not earned.
        self.assertEqual(report.effective_fee_rate_bps, 8.0)
        self.assertTrue(any("not credited a rebate" in w for w in report.warnings))


class TestClassificationBasis(unittest.TestCase):
    """The basis is the parameter that decides whether the label means anything."""

    def test_quantity_and_notional_bases_can_disagree(self):
        # Same symbol, same share counts, very different prices:
        #   quantity ratio = 100 / 200 = 0.50            -> HYBRID
        #   notional ratio = 1,000 / 20,000 = 0.05       -> PURE_TAKER
        trades = (
            fills(1, prefix="M", symbol="PENNY", is_maker=True,
                  price=10.0, quantity=100.0, fee=-0.1)
            + fills(1, prefix="T", symbol="PENNY", is_maker=False,
                    price=190.0, quantity=100.0, fee=9.5)
        )

        by_quantity = MarketMakerVsTakerClassifierEngine(
            ClassificationBasis.QUANTITY
        ).classify_strategy_executions("BASIS_05", trades)
        by_notional = MarketMakerVsTakerClassifierEngine(
            ClassificationBasis.NOTIONAL
        ).classify_strategy_executions("BASIS_05", trades)

        self.assertEqual(by_quantity.classification, "HYBRID_MAKER_TAKER_STRATEGY")
        self.assertEqual(by_quantity.classification_ratio, 0.5)
        self.assertEqual(by_notional.classification, "PURE_TAKER_STRATEGY")
        self.assertEqual(by_notional.classification_ratio, 0.05)
        # Both ratios are always reported, whichever basis was selected.
        self.assertEqual(by_quantity.maker_notional_ratio, 0.05)
        self.assertEqual(by_notional.maker_volume_ratio, 0.5)

    def test_quantity_basis_rejects_multi_symbol_log(self):
        # 1 BTC and 100 AAPL shares are not 101 of anything.
        trades = (
            fills(1, prefix="M", symbol="BTC-USD", is_maker=True,
                  price=50_000.0, quantity=1.0, fee=-5.0)
            + fills(1, prefix="T", symbol="AAPL", is_maker=False,
                    price=150.0, quantity=100.0, fee=0.3)
        )
        engine = MarketMakerVsTakerClassifierEngine(ClassificationBasis.QUANTITY)

        with self.assertRaises(TradeLogError) as ctx:
            engine.classify_strategy_executions("MIXED_06", trades)
        self.assertIn("QUANTITY basis requires a single instrument", str(ctx.exception))

    def test_notional_basis_accepts_multi_symbol_and_suppresses_share_ratio(self):
        trades = (
            fills(1, prefix="M", symbol="BTC-USD", is_maker=True,
                  price=50_000.0, quantity=1.0, fee=-5.0)
            + fills(1, prefix="T", symbol="AAPL", is_maker=False,
                    price=150.0, quantity=100.0, fee=0.3)
        )
        engine = MarketMakerVsTakerClassifierEngine(ClassificationBasis.NOTIONAL)

        report = engine.classify_strategy_executions("MIXED_07", trades)

        # maker 50,000 / (50,000 + 15,000) = 0.769230...
        self.assertAlmostEqual(report.maker_notional_ratio, 0.769231, places=6)
        self.assertEqual(report.classification, "HYBRID_MAKER_TAKER_STRATEGY")
        self.assertIsNone(report.maker_volume_ratio)
        self.assertEqual(report.symbols, ["AAPL", "BTC-USD"])
        self.assertTrue(any("not additive across instruments" in w
                            for w in report.warnings))

    def test_basis_accepts_case_insensitive_string(self):
        engine = MarketMakerVsTakerClassifierEngine("notional")
        self.assertIs(engine.classification_basis, ClassificationBasis.NOTIONAL)

    def test_unknown_basis_rejected(self):
        for bad in ("VOLUME", None, 3):
            with self.subTest(basis=bad):
                with self.assertRaises(TradeLogError):
                    MarketMakerVsTakerClassifierEngine(bad)


class TestThresholdBoundaries(unittest.TestCase):
    """Threshold behaviour, including the rounding regression."""

    def setUp(self):
        self.engine = MarketMakerVsTakerClassifierEngine(ClassificationBasis.QUANTITY)

    def _ratio_log(self, maker_qty, taker_qty):
        return (
            fills(1, prefix="M", symbol="XYZ", is_maker=True,
                  price=1.0, quantity=maker_qty, fee=0.0)
            + fills(1, prefix="T", symbol="XYZ", is_maker=False,
                    price=1.0, quantity=taker_qty, fee=0.0)
        )

    def test_ratio_just_below_threshold_is_not_rounded_up(self):
        # Regression: 79,996 / 100,000 = 0.79996 rounds to 0.8000 at four decimal
        # places. Classifying the rounded value promotes this log to PURE_MAKER;
        # classifying the exact value does not.
        report = self.engine.classify_strategy_executions(
            "BOUNDARY_08", self._ratio_log(79_996.0, 20_004.0)
        )
        self.assertEqual(report.classification_ratio, 0.79996)
        self.assertEqual(report.classification, "HYBRID_MAKER_TAKER_STRATEGY")

    def test_thresholds_are_inclusive_at_both_ends(self):
        at_maker = self.engine.classify_strategy_executions(
            "BOUNDARY_09", self._ratio_log(80.0, 20.0)
        )
        at_taker = self.engine.classify_strategy_executions(
            "BOUNDARY_10", self._ratio_log(20.0, 80.0)
        )
        self.assertEqual(at_maker.classification_ratio, 0.8)
        self.assertEqual(at_maker.classification, "PURE_MAKER_STRATEGY")
        self.assertEqual(at_taker.classification_ratio, 0.2)
        self.assertEqual(at_taker.classification, "PURE_TAKER_STRATEGY")

    def test_boundary_proximity_is_warned_about(self):
        report = self.engine.classify_strategy_executions(
            "BOUNDARY_11", self._ratio_log(80.0, 20.0)
        )
        self.assertTrue(any("cut-off artefact" in w for w in report.warnings))

    def test_custom_thresholds_are_honoured(self):
        engine = MarketMakerVsTakerClassifierEngine(
            ClassificationBasis.QUANTITY,
            pure_maker_threshold_ratio=0.60,
            pure_taker_threshold_ratio=0.10,
        )
        report = engine.classify_strategy_executions(
            "CUSTOM_12", self._ratio_log(65.0, 35.0)
        )
        self.assertEqual(report.classification, "PURE_MAKER_STRATEGY")

    def test_swapped_thresholds_rejected(self):
        # Swapped bounds would make the taker branch unreachable and silently
        # label every mixed strategy PURE_MAKER.
        with self.assertRaises(TradeLogError):
            MarketMakerVsTakerClassifierEngine(
                ClassificationBasis.QUANTITY,
                pure_maker_threshold_ratio=0.20,
                pure_taker_threshold_ratio=0.80,
            )

    def test_equal_thresholds_rejected(self):
        with self.assertRaises(TradeLogError):
            MarketMakerVsTakerClassifierEngine(
                ClassificationBasis.QUANTITY,
                pure_maker_threshold_ratio=0.5,
                pure_taker_threshold_ratio=0.5,
            )

    def test_out_of_range_or_non_finite_thresholds_rejected(self):
        for maker, taker in ((1.5, 0.2), (0.8, -0.1), (float("nan"), 0.2),
                             (0.8, float("inf"))):
            with self.subTest(maker=maker, taker=taker):
                with self.assertRaises(TradeLogError):
                    MarketMakerVsTakerClassifierEngine(
                        ClassificationBasis.QUANTITY,
                        pure_maker_threshold_ratio=maker,
                        pure_taker_threshold_ratio=taker,
                    )


class TestLiquidityCategories(unittest.TestCase):
    """FIX tag 851 has four values, not two."""

    def setUp(self):
        self.engine = MarketMakerVsTakerClassifierEngine(ClassificationBasis.NOTIONAL)

    def test_routed_and_auction_fills_are_excluded_from_the_ratio(self):
        # 5 maker + 5 taker at $100 x 10 = $5,000 each side; 10 auction fills of
        # the same size. Forcing the auction fills into the taker bucket would
        # give 5,000 / 20,000 = 0.25; excluding them gives 5,000 / 10,000 = 0.50.
        trades = (
            fills(5, prefix="M", symbol="SPY", is_maker=True,
                  price=100.0, quantity=10.0, fee=-1.0)
            + fills(5, prefix="T", symbol="SPY", is_maker=False,
                    price=100.0, quantity=10.0, fee=3.0)
            + fills(10, prefix="X", symbol="SPY", is_maker=False,
                    price=100.0, quantity=10.0, fee=2.0,
                    liquidity_category=LiquidityCategory.AUCTION)
        )

        report = self.engine.classify_strategy_executions("AUCTION_13", trades)

        self.assertEqual(report.classification_ratio, 0.5)
        self.assertEqual(report.classification, "HYBRID_MAKER_TAKER_STRATEGY")
        self.assertEqual(report.excluded_trades_count, 10)
        self.assertEqual(report.excluded_gross_notional_usd, 10_000.00)
        self.assertEqual(report.excluded_fees_paid_usd, 20.00)
        # Excluded fills stay in the total notional and in the net fee figure.
        self.assertEqual(report.total_gross_notional_usd, 20_000.00)
        self.assertEqual(report.net_fees_paid_usd, -5.0 + 15.0 + 20.0)
        self.assertTrue(any("excluded from the maker ratio" in w
                            for w in report.warnings))

    def test_log_with_no_continuous_book_fills_is_unclassified(self):
        trades = fills(4, prefix="R", symbol="SPY", is_maker=False,
                       price=100.0, quantity=10.0, fee=1.0,
                       liquidity_category=LiquidityCategory.ROUTED_OUT)

        report = self.engine.classify_strategy_executions("ROUTED_14", trades)

        # 0.0 here would read as "entirely taker", which is the opposite of true.
        self.assertIsNone(report.classification_ratio)
        self.assertIsNone(report.maker_notional_ratio)
        self.assertIsNone(report.maker_volume_ratio)
        self.assertEqual(report.classification, "UNCLASSIFIED_NO_MAKER_TAKER_VOLUME")
        self.assertEqual(report.effective_fee_rate_bps, 10.0)  # 4 / 4,000 * 10,000
        self.assertTrue(any("no maker ratio exists" in w for w in report.warnings))

    def test_category_defaults_from_is_maker(self):
        maker = ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, -0.1)
        taker = ExecutedTradeLog("B", "SPY", False, 100.0, 1.0, 0.3)
        self.assertIs(maker.liquidity_category, LiquidityCategory.ADDED)
        self.assertIs(taker.liquidity_category, LiquidityCategory.REMOVED)

    def test_category_string_is_normalised(self):
        trade = ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, -0.1,
                                 liquidity_category="added")
        self.assertIs(trade.liquidity_category, LiquidityCategory.ADDED)

    def test_category_contradicting_is_maker_rejected(self):
        with self.assertRaises(TradeLogError):
            ExecutedTradeLog("A", "SPY", False, 100.0, 1.0, -0.1,
                             liquidity_category=LiquidityCategory.ADDED)
        with self.assertRaises(TradeLogError):
            ExecutedTradeLog("B", "SPY", True, 100.0, 1.0, 0.3,
                             liquidity_category=LiquidityCategory.REMOVED)

    def test_unknown_category_rejected(self):
        with self.assertRaises(TradeLogError):
            ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, -0.1,
                             liquidity_category="MIDPOINT")


class TestFillValidation(unittest.TestCase):
    """Malformed fills are rejected at construction, not counted."""

    def test_string_is_maker_rejected(self):
        # 'false' is truthy in Python; coercing it would book every taker fill
        # in a JSON-sourced log as a maker fill.
        for flag in ("false", "true", 1, 0, None):
            with self.subTest(is_maker=flag):
                with self.assertRaises(TradeLogError):
                    ExecutedTradeLog("A", "SPY", flag, 100.0, 1.0, 0.1)

    def test_non_positive_or_non_finite_price_rejected(self):
        for price in (0.0, -100.0, float("nan"), float("inf"), "100"):
            with self.subTest(price=price):
                with self.assertRaises(TradeLogError):
                    ExecutedTradeLog("A", "SPY", True, price, 1.0, 0.1)

    def test_non_positive_or_non_finite_quantity_rejected(self):
        for quantity in (0.0, -50.0, float("nan"), float("-inf"), None):
            with self.subTest(quantity=quantity):
                with self.assertRaises(TradeLogError):
                    ExecutedTradeLog("A", "SPY", True, 100.0, quantity, 0.1)

    def test_non_finite_fee_rejected(self):
        # A NaN fee would propagate silently into net fees and the bps rate.
        for fee in (float("nan"), float("inf"), "0.1"):
            with self.subTest(fee=fee):
                with self.assertRaises(TradeLogError):
                    ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, fee)

    def test_blank_identifiers_rejected(self):
        with self.assertRaises(TradeLogError):
            ExecutedTradeLog("  ", "SPY", True, 100.0, 1.0, 0.1)
        with self.assertRaises(TradeLogError):
            ExecutedTradeLog("A", "", True, 100.0, 1.0, 0.1)

    def test_negative_fee_is_accepted_as_a_rebate(self):
        trade = ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, -0.25)
        self.assertEqual(trade.fee_paid_usd, -0.25)
        self.assertEqual(trade.notional_usd, 100.0)

    def test_fill_is_immutable_after_validation(self):
        # Validation that can be undone by assigning to the field afterwards is
        # not validation.
        trade = ExecutedTradeLog("A", "SPY", True, 100.0, 1.0, -0.25)
        for attribute, value in (("quantity", -50.0), ("is_maker", "false"),
                                 ("liquidity_category", "MIDPOINT")):
            with self.subTest(attribute=attribute):
                with self.assertRaises(FrozenInstanceError):
                    setattr(trade, attribute, value)


class TestEngineInputValidation(unittest.TestCase):
    """Log-level input handling, including AI-agent misuse shapes."""

    def setUp(self):
        self.engine = MarketMakerVsTakerClassifierEngine(ClassificationBasis.NOTIONAL)

    def test_empty_log_rejected_as_value_error(self):
        # TradeLogError subclasses ValueError, so pre-existing callers still catch it.
        with self.assertRaises(ValueError):
            self.engine.classify_strategy_executions("EMPTY_15", [])

    def test_raw_dict_elements_rejected(self):
        payload = [{"trade_id": "A", "symbol": "SPY", "is_maker": "false",
                    "executed_price": 100.0, "quantity": 1.0, "fee_paid_usd": 0.1}]
        with self.assertRaises(TradeLogError) as ctx:
            self.engine.classify_strategy_executions("DICT_16", payload)
        self.assertIn("must be an ExecutedTradeLog", str(ctx.exception))

    def test_duplicate_trade_ids_rejected(self):
        # An overlapping paginated fetch double-counts volume and fees in every
        # figure in the report, with nothing in the output to show it happened.
        trade = ExecutedTradeLog("FILL_1", "SPY", True, 100.0, 10.0, -0.5)
        with self.assertRaises(TradeLogError) as ctx:
            self.engine.classify_strategy_executions("DUPE_18", [trade, trade])
        self.assertIn("Duplicate trade_id", str(ctx.exception))

    def test_tuple_log_is_accepted(self):
        trades = tuple(fills(2, prefix="M", symbol="SPY", is_maker=True,
                             price=100.0, quantity=1.0, fee=-0.1))
        report = self.engine.classify_strategy_executions("TUPLE_19", trades)
        self.assertEqual(report.total_trades_count, 2)

    def test_non_sequence_trades_rejected(self):
        for bad in (None, 42, "AAPL"):
            with self.subTest(trades=bad):
                with self.assertRaises(TradeLogError):
                    self.engine.classify_strategy_executions("BAD_17", bad)

    def test_blank_strategy_id_rejected(self):
        trades = fills(1, prefix="M", symbol="SPY", is_maker=True,
                       price=100.0, quantity=1.0, fee=-0.1)
        with self.assertRaises(TradeLogError):
            self.engine.classify_strategy_executions("   ", trades)


if __name__ == "__main__":
    unittest.main()
