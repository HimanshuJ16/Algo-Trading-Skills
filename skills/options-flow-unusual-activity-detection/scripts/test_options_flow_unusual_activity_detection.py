import math
import unittest

from options_flow_unusual_activity_detection import (
    DEFAULT_MIN_PREMIUM_USD,
    OptionsFlowUnusualActivityDetectionEngine,
    OptionsTrade,
    classify_aggressor,
)


def make_trade(**overrides):
    """A print that clears every gate unless a field is overridden.

    5,000 contracts on 1,000 OI (V/OI = 5.0) and 1,000 ADV (V/ADV = 5.0), printed at
    the $5.00 ask => premium 5,000 x 5.00 x 100 = $2,500,000.
    """
    defaults = dict(
        trade_id="TR_101",
        asset_id="NVDA",
        option_symbol="NVDA240621C00500000",
        option_type="CALL",
        volume=5000,
        open_interest=1000,
        adv=1000.0,
        execution_price=5.00,
        bid=4.80,
        ask=5.00,
        timestamp="2024-06-01T10:00:00Z",
    )
    defaults.update(overrides)
    return OptionsTrade(**defaults)


class TestAggressorClassification(unittest.TestCase):
    """Quote rule: at/above ask = buy, at/below bid = sell, inside = unclassifiable."""

    def test_at_and_above_ask_is_buy(self):
        self.assertEqual(classify_aggressor(5.00, 4.80, 5.00), "BUY_AT_ASK")
        self.assertEqual(classify_aggressor(5.20, 4.80, 5.00), "BUY_AT_ASK")

    def test_at_and_below_bid_is_sell(self):
        self.assertEqual(classify_aggressor(4.80, 4.80, 5.00), "SELL_AT_BID")
        self.assertEqual(classify_aggressor(4.50, 4.80, 5.00), "SELL_AT_BID")

    def test_inside_spread_is_mid_market(self):
        self.assertEqual(classify_aggressor(4.90, 4.80, 5.00), "MID_MARKET")

    def test_locked_market_resolves_to_buy_not_sell(self):
        # bid == ask: the >= ask branch is evaluated first, so a locked quote reports
        # BUY_AT_ASK. Documented behaviour, asserted so it cannot drift silently.
        self.assertEqual(classify_aggressor(5.00, 5.00, 5.00), "BUY_AT_ASK")

    def test_missing_quote_is_unclassified_not_buy(self):
        # Regression: a zero/absent quote previously satisfied `price >= ask` and
        # labelled every quote outage an aggressive buy.
        self.assertEqual(classify_aggressor(5.00, None, None), "UNCLASSIFIED")
        self.assertEqual(classify_aggressor(5.00, 0.0, 0.0), "UNCLASSIFIED")

    def test_crossed_quote_is_unclassified(self):
        self.assertEqual(classify_aggressor(5.00, 5.20, 5.00), "UNCLASSIFIED")


class TestUnusualActivityDetection(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_bullish_sweep_on_call_bought_at_ask(self):
        report = self.engine.detect_unusual_activity(make_trade())
        self.assertTrue(report.is_unusual)
        self.assertEqual(report.classification, "UNUSUAL_BULLISH_SWEEP")
        self.assertEqual(report.vol_oi_ratio, 5.0)
        self.assertEqual(report.vol_adv_ratio, 5.0)
        self.assertEqual(report.total_premium_usd, 2_500_000.0)
        self.assertEqual(set(report.gates_passed), {"v_oi", "v_adv", "premium"})
        self.assertEqual(report.gates_unevaluable, ())
        self.assertTrue(report.direction_is_inferred)

    def test_bearish_sweep_on_put_bought_at_ask(self):
        report = self.engine.detect_unusual_activity(make_trade(option_type="PUT"))
        self.assertEqual(report.classification, "UNUSUAL_BEARISH_SWEEP")

    def test_call_sold_at_bid_is_bearish_block(self):
        report = self.engine.detect_unusual_activity(make_trade(execution_price=4.80))
        self.assertEqual(report.aggressor_side, "SELL_AT_BID")
        self.assertEqual(report.classification, "UNUSUAL_BEARISH_BLOCK")

    def test_put_sold_at_bid_is_bullish_block(self):
        report = self.engine.detect_unusual_activity(
            make_trade(option_type="PUT", execution_price=4.80))
        self.assertEqual(report.classification, "UNUSUAL_BULLISH_BLOCK")

    def test_midspread_print_is_flagged_but_non_directional(self):
        report = self.engine.detect_unusual_activity(make_trade(execution_price=4.90))
        self.assertTrue(report.is_unusual)
        self.assertEqual(report.classification, "UNUSUAL_FLOW_NEUTRAL")
        self.assertFalse(report.direction_is_inferred)

    def test_large_print_without_quote_is_not_labelled_directional(self):
        # Regression: previously classified UNUSUAL_BULLISH_SWEEP because a missing
        # quote defaulted to BUY_AT_ASK.
        report = self.engine.detect_unusual_activity(make_trade(bid=None, ask=None))
        self.assertTrue(report.is_unusual)
        self.assertEqual(report.classification, "UNUSUAL_FLOW_UNCLASSIFIED")
        self.assertFalse(report.direction_is_inferred)
        self.assertIn("Direction NOT inferable", report.audit_notes)

    def test_routine_flow_no_trigger(self):
        report = self.engine.detect_unusual_activity(make_trade(
            trade_id="TR_102", volume=10, open_interest=5000, adv=10000.0,
            execution_price=2.00, bid=1.95, ask=2.05))
        self.assertFalse(report.is_unusual)
        self.assertEqual(report.classification, "ROUTINE_FLOW")
        self.assertEqual(report.gates_passed, ())

    def test_all_three_gates_are_required(self):
        # V/OI = 5.0 and V/ADV = 5.0 clear, premium = 5,000 x 0.05 x 100 = $25,000 does not.
        report = self.engine.detect_unusual_activity(make_trade(
            execution_price=0.05, bid=0.04, ask=0.05))
        self.assertEqual(report.total_premium_usd, 25_000.0)
        self.assertFalse(report.is_unusual)
        self.assertEqual(set(report.gates_passed), {"v_oi", "v_adv"})

        # Premium and V/ADV clear, V/OI does not: 5,000 on 100,000 OI = 0.05.
        report = self.engine.detect_unusual_activity(make_trade(open_interest=100_000))
        self.assertFalse(report.is_unusual)
        self.assertEqual(set(report.gates_passed), {"v_adv", "premium"})


class TestThresholdBoundaries(unittest.TestCase):
    """Gates are inclusive (>=). Exactly-at-threshold must flag."""

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_exactly_at_every_threshold_flags(self):
        # V/OI = 1500/1000 = 1.5, V/ADV = 1500/750 = 2.0,
        # premium = 1500 x 0.6666666666666666 x 100 = $100,000.00 (>= 100,000).
        report = self.engine.detect_unusual_activity(make_trade(
            volume=1500, open_interest=1000, adv=750.0,
            execution_price=2.0 / 3.0, bid=0.60, ask=2.0 / 3.0))
        self.assertEqual(report.vol_oi_ratio, 1.5)
        self.assertEqual(report.vol_adv_ratio, 2.0)
        self.assertGreaterEqual(report.total_premium_usd, DEFAULT_MIN_PREMIUM_USD)
        self.assertTrue(report.is_unusual)

    def test_just_below_v_oi_threshold_does_not_flag(self):
        # V/OI = 1499/1000 = 1.499 < 1.5; the other two gates still clear.
        report = self.engine.detect_unusual_activity(make_trade(
            volume=1499, open_interest=1000, adv=100.0, execution_price=5.00))
        self.assertFalse(report.is_unusual)
        self.assertNotIn("v_oi", report.gates_passed)

    def test_display_rounding_does_not_contradict_the_gate(self):
        # V/OI = 1.4999 rounds to 1.5 at two decimals. The report must not show a
        # ratio that reads as clearing a gate it did not clear.
        report = self.engine.detect_unusual_activity(make_trade(
            volume=14999, open_interest=10000, adv=100.0, execution_price=5.00))
        self.assertFalse(report.is_unusual)
        self.assertEqual(report.vol_oi_ratio, 1.4999)


class TestMissingAndZeroDenominators(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_zero_open_interest_gives_infinite_ratio(self):
        # A newly listed series with no standing OI: every contract traded opens
        # interest, so V/OI is genuinely unbounded.
        report = self.engine.detect_unusual_activity(make_trade(open_interest=0))
        self.assertTrue(math.isinf(report.vol_oi_ratio))
        self.assertTrue(report.is_unusual)

    def test_zero_open_interest_small_print_still_needs_premium(self):
        # Regression: the old fallback set V/OI = volume, so a 2-contract print on a
        # zero-OI series cleared the 1.5x gate. Here the ratio is infinite but the
        # premium gate ($200) correctly blocks the flag.
        report = self.engine.detect_unusual_activity(make_trade(
            volume=2, open_interest=0, adv=0.0, execution_price=1.00, bid=0.95, ask=1.00))
        self.assertEqual(report.total_premium_usd, 200.0)
        self.assertFalse(report.is_unusual)

    def test_unavailable_open_interest_cannot_clear_its_gate(self):
        report = self.engine.detect_unusual_activity(make_trade(open_interest=None))
        self.assertIsNone(report.vol_oi_ratio)
        self.assertFalse(report.is_unusual)
        self.assertIn("v_oi", report.gates_unevaluable)
        self.assertIn("NOT EVALUABLE", report.audit_notes)

    def test_unavailable_adv_cannot_clear_its_gate(self):
        report = self.engine.detect_unusual_activity(make_trade(adv=None))
        self.assertIsNone(report.vol_adv_ratio)
        self.assertFalse(report.is_unusual)
        self.assertIn("v_adv", report.gates_unevaluable)


class TestContractMultiplier(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_default_multiplier_is_100(self):
        report = self.engine.detect_unusual_activity(make_trade(
            volume=1000, execution_price=3.00))
        self.assertEqual(report.total_premium_usd, 300_000.0)

    def test_adjusted_contract_multiplier_changes_premium(self):
        # OCC adjustments can change a series' multiplier; premium must follow it.
        report = self.engine.detect_unusual_activity(make_trade(
            volume=1000, execution_price=3.00, contract_multiplier=10.0))
        self.assertEqual(report.total_premium_usd, 30_000.0)
        self.assertFalse(report.is_unusual)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_invalid_inputs_raise(self):
        bad_cases = {
            "zero volume": dict(volume=0),
            "negative volume": dict(volume=-100),
            "negative open interest": dict(open_interest=-1),
            "negative adv": dict(adv=-5.0),
            "negative price": dict(execution_price=-1.0),
            "nan price": dict(execution_price=float("nan")),
            "inf price": dict(execution_price=float("inf")),
            "nan bid": dict(bid=float("nan")),
            "zero multiplier": dict(contract_multiplier=0.0),
            "unknown option type": dict(option_type="STRADDLE"),
            "fractional volume": dict(volume=1500.5),
            "fractional open interest": dict(open_interest=100.5),
            "boolean volume": dict(volume=True),
        }
        for label, override in bad_cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    self.engine.detect_unusual_activity(make_trade(**override))

    def test_integral_float_contract_counts_are_accepted(self):
        # JSON feeds deliver counts as floats; 5000.0 contracts is not an error.
        report = self.engine.detect_unusual_activity(
            make_trade(volume=5000.0, open_interest=1000.0))
        self.assertEqual(report.vol_oi_ratio, 5.0)
        self.assertTrue(report.is_unusual)

    def test_option_type_aliases_and_case_are_normalized(self):
        for option_type in ("call", "C", " Call "):
            with self.subTest(option_type=option_type):
                report = self.engine.detect_unusual_activity(make_trade(option_type=option_type))
                self.assertEqual(report.classification, "UNUSUAL_BULLISH_SWEEP")
        for option_type in ("put", "P"):
            with self.subTest(option_type=option_type):
                report = self.engine.detect_unusual_activity(make_trade(option_type=option_type))
                self.assertEqual(report.classification, "UNUSUAL_BEARISH_SWEEP")

    def test_invalid_threshold_config_raises(self):
        for bad in ({"min_v_oi_ratio": 0}, {"min_v_adv_ratio": -1.0},
                    {"min_premium_usd": float("nan")}, {"min_premium_usd": "100000"}):
            with self.subTest(config=bad):
                with self.assertRaises(ValueError):
                    OptionsFlowUnusualActivityDetectionEngine(bad)

    def test_custom_thresholds_are_applied(self):
        engine = OptionsFlowUnusualActivityDetectionEngine(
            {"min_v_oi_ratio": 10.0, "min_v_adv_ratio": 10.0, "min_premium_usd": 1_000_000.0})
        report = engine.detect_unusual_activity(make_trade())  # V/OI = V/ADV = 5.0
        self.assertFalse(report.is_unusual)


class TestScan(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_scan_scores_every_trade(self):
        trades = [
            make_trade(trade_id="A"),
            make_trade(trade_id="B", volume=10, open_interest=5000, adv=10000.0,
                       execution_price=2.00, bid=1.95, ask=2.05),
        ]
        reports = self.engine.scan(trades)
        self.assertEqual([r.trade_id for r in reports], ["A", "B"])
        self.assertEqual([r.is_unusual for r in reports], [True, False])

    def test_scan_unusual_only_filters(self):
        trades = [
            make_trade(trade_id="A"),
            make_trade(trade_id="B", volume=10, open_interest=5000, adv=10000.0,
                       execution_price=2.00, bid=1.95, ask=2.05),
        ]
        reports = self.engine.scan(trades, unusual_only=True)
        self.assertEqual([r.trade_id for r in reports], ["A"])

    def test_scan_skips_malformed_print_and_continues(self):
        trades = [make_trade(trade_id="BAD", volume=0), make_trade(trade_id="GOOD")]
        with self.assertLogs("options_flow_unusual_activity_detection", level="ERROR"):
            reports = self.engine.scan(trades)
        self.assertEqual([r.trade_id for r in reports], ["GOOD"])

    def test_scan_of_empty_feed_returns_empty_list(self):
        self.assertEqual(self.engine.scan([]), [])


if __name__ == '__main__':
    unittest.main()
