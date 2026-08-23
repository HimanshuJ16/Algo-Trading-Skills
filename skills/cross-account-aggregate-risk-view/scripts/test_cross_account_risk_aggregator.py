import unittest

from cross_account_risk_aggregator import (
    CrossAccountRiskAggregator, SubAccountState
)

class TestCrossAccountRiskAggregator(unittest.TestCase):

    def setUp(self):
        self.aggregator = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0, max_margin_utilization_pct=80.0)

        # Sub-Account 1 (IBKR): Long 1,000 AAPL, Long 500 NVDA
        self.acc1 = SubAccountState(
            account_id="ACC_IBKR_01", broker_name="InteractiveBrokers",
            cash_usd=200_000.0, margin_used_usd=50_000.0, margin_limit_usd=200_000.0,
            positions={"AAPL": 1000.0, "NVDA": 500.0}
        )
        # Sub-Account 2 (CME FCM): Short 400 AAPL (Internal Offsetting!)
        self.acc2 = SubAccountState(
            account_id="ACC_FUTURES_02", broker_name="CME_FCM",
            cash_usd=150_000.0, margin_used_usd=30_000.0, margin_limit_usd=200_000.0,
            positions={"AAPL": -400.0}
        )

        self.aggregator.register_account(self.acc1)
        self.aggregator.register_account(self.acc2)

        # Market prices: AAPL = $150, NVDA = $200
        self.market_prices = {"AAPL": 150.0, "NVDA": 200.0}

    # ---------- Consolidation ----------

    def test_consolidation_and_internal_offsetting(self):
        report = self.aggregator.aggregate_firm_risk(self.market_prices)

        # AAPL Net = 1000 - 400 = 600 shares ($90,000 value)
        # NVDA Net = 500 shares ($100,000 value)
        # Gross Market Value = $90k + $100k = $190,000
        # Firm NAV = ($200k + $150k cash) + $190k positions = $540,000
        # Margin utilization = ($50k + $30k) / ($200k + $200k) = 20%
        self.assertEqual(report.net_positions["AAPL"], 600.0)
        self.assertEqual(report.net_positions["NVDA"], 500.0)
        self.assertEqual(report.total_gmv_usd, 190_000.0)
        self.assertEqual(report.total_firm_nav_usd, 540_000.0)
        self.assertEqual(report.aggregate_margin_utilization_pct, 20.0)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.unvalued_symbols, [])

        # AAPL held long in Acc 1 and short in Acc 2 -> Internal Offsetting
        self.assertIn("AAPL", report.internal_offsetting_friction_symbols)

    def test_net_zero_cross_account_position_still_flagged(self):
        # Fully offset (+500 / -500) nets to 0 shares and $0 GMV, but the
        # concurrent long/short across accounts is still flagged for review.
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=10_000.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"X": 500.0}))
        agg.register_account(SubAccountState(
            "B", "CME", cash_usd=10_000.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"X": -500.0}))

        report = agg.aggregate_firm_risk({"X": 150.0})
        self.assertEqual(report.net_positions["X"], 0.0)
        self.assertEqual(report.total_gmv_usd, 0.0)
        self.assertEqual(report.internal_offsetting_friction_symbols, ["X"])
        self.assertTrue(report.is_compliant)

    def test_zero_qty_entries_ignored_and_extra_prices_tolerated(self):
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=50_000.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"X": 100.0, "Y": 0.0}))
        # Price supplied for Y (zero-qty, never held) must be harmless.
        report = agg.aggregate_firm_risk({"X": 10.0, "Y": 99.0})
        self.assertEqual(report.total_gmv_usd, 1_000.0)  # |100 * 10|
        self.assertEqual(report.total_firm_nav_usd, 51_000.0)  # 50k cash + 1k
        self.assertNotIn("Y", report.net_positions)

    # ---------- Fail-closed pricing ----------

    def test_missing_price_fails_closed(self):
        # Regression: previously an unpriced position was valued at $0.00 and
        # the firm reported itself compliant with its real exposure unknown.
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=100_000.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"AAPL": 1000.0}))

        report = agg.aggregate_firm_risk({})  # no price feed at all
        self.assertFalse(report.is_compliant)
        self.assertIn("AAPL", report.unvalued_symbols)
        self.assertEqual(report.total_gmv_usd, 0.0)  # explicitly understated
        self.assertTrue(any("Unvalued" in v for v in report.violations))

    def test_missing_price_blocks_pretrade_approval(self):
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=100_000.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"AAPL": 1000.0}))

        # Even a tiny unrelated order must be rejected while any held symbol
        # cannot be valued -- firm GMV compliance is not certifiable.
        approved, msg = agg.evaluate_pre_trade_order(
            "A", "MSFT", proposed_qty=1.0, price=250.0, market_prices={"MSFT": 250.0})
        self.assertFalse(approved)
        self.assertIn("Unvalued", msg)

    def test_non_positive_or_nan_prices_fail_closed(self):
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=1_000_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=0.0, margin_used_usd=0.0,
            margin_limit_usd=100_000.0, positions={"AAPL": 10.0}))

        for bad_price in (0.0, -150.0, float("nan"), float("inf")):
            report = agg.aggregate_firm_risk({"AAPL": bad_price})
            self.assertFalse(report.is_compliant, f"price={bad_price!r}")
            self.assertEqual(report.unvalued_symbols, ["AAPL"], f"price={bad_price!r}")

    # ---------- Input validation ----------

    def test_malformed_subaccount_rejected_at_construction(self):
        cases = [
            {"account_id": "", "broker_name": "IBKR", "cash_usd": 1.0,
             "margin_used_usd": 0.0, "margin_limit_usd": 1.0, "positions": {}},
            {"account_id": "A", "broker_name": "IBKR", "cash_usd": "100000",
             "margin_used_usd": 0.0, "margin_limit_usd": 1.0, "positions": {}},
            {"account_id": "A", "broker_name": "IBKR", "cash_usd": 1.0,
             "margin_used_usd": -1.0, "margin_limit_usd": 1.0, "positions": {}},
            {"account_id": "A", "broker_name": "IBKR", "cash_usd": 1.0,
             "margin_used_usd": 0.0, "margin_limit_usd": float("nan"), "positions": {}},
            {"account_id": "A", "broker_name": "IBKR", "cash_usd": 1.0,
             "margin_used_usd": 0.0, "margin_limit_usd": 1.0,
             "positions": {"AAPL": float("nan")}},
            {"account_id": "A", "broker_name": "IBKR", "cash_usd": 1.0,
             "margin_used_usd": 0.0, "margin_limit_usd": 1.0, "positions": None},
        ]
        for kwargs in cases:
            with self.assertRaises(ValueError, msg=str(kwargs)):
                SubAccountState(**kwargs)

    def test_constructor_validates_limits(self):
        for kwargs in (
            {"max_firm_gmv_limit_usd": -5.0},
            {"max_firm_gmv_limit_usd": 0.0},
            {"max_firm_gmv_limit_usd": float("inf")},
            {"max_margin_utilization_pct": 0.0},
            {"max_margin_utilization_pct": -20.0},
            {"max_margin_utilization_pct": 100.5},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                CrossAccountRiskAggregator(**kwargs)
        # Boundary values are accepted.
        CrossAccountRiskAggregator(max_margin_utilization_pct=100.0)

    def test_pre_trade_validates_numeric_inputs(self):
        for kwargs in (
            {"proposed_qty": float("nan")},
            {"proposed_qty": float("inf")},
            {"proposed_qty": "1000"},
        ):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                self.aggregator.evaluate_pre_trade_order(
                    "ACC_IBKR_01", "AAPL", price=150.0,
                    market_prices=self.market_prices, **kwargs)
        for bad_price in (0.0, -150.0, float("nan"), "150"):
            with self.assertRaises(ValueError, msg=f"price={bad_price!r}"):
                self.aggregator.evaluate_pre_trade_order(
                    "ACC_IBKR_01", "AAPL", proposed_qty=10.0,
                    price=bad_price, market_prices=self.market_prices)

    # ---------- Margin capacity semantics ----------

    def test_zero_margin_capacity_with_usage_is_a_violation(self):
        # Regression: previously reported a reassuring 0.0% utilization.
        agg = CrossAccountRiskAggregator(max_margin_utilization_pct=80.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=10_000.0, margin_used_usd=900_000.0,
            margin_limit_usd=0.0, positions={}))
        report = agg.aggregate_firm_risk({})
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.aggregate_margin_utilization_pct, 100.0)
        self.assertTrue(any("margin capacity undefined" in v.lower() for v in report.violations))

    def test_zero_margin_capacity_without_usage_is_compliant(self):
        agg = CrossAccountRiskAggregator(max_margin_utilization_pct=80.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=10_000.0, margin_used_usd=0.0,
            margin_limit_usd=0.0, positions={}))
        report = agg.aggregate_firm_risk({})
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.aggregate_margin_utilization_pct, 0.0)

    # ---------- Pre-trade gate ----------

    def test_pre_trade_order_approval(self):
        # Order: Buy 1,000 AAPL @ $150 ($150,000 value) -> Total GMV = $190k + $150k = $340k <= $1M
        is_approved, msg = self.aggregator.evaluate_pre_trade_order(
            account_id="ACC_IBKR_01", symbol="AAPL", proposed_qty=1000.0, price=150.0, market_prices=self.market_prices
        )
        self.assertTrue(is_approved)

    def test_pre_trade_order_rejection_on_gmv_limit(self):
        # Order: Buy 10,000 NVDA @ $200 ($2,000,000 value -> Breaches $1M firm GMV cap)
        is_approved, msg = self.aggregator.evaluate_pre_trade_order(
            account_id="ACC_IBKR_01", symbol="NVDA", proposed_qty=10000.0, price=200.0, market_prices=self.market_prices
        )
        self.assertFalse(is_approved)
        self.assertIn("Firm GMV limit breached", msg)

    def test_pre_trade_unknown_account_rejected(self):
        is_approved, msg = self.aggregator.evaluate_pre_trade_order(
            account_id="ACC_GHOST_99", symbol="AAPL", proposed_qty=10.0, price=150.0,
            market_prices=self.market_prices
        )
        self.assertFalse(is_approved)
        self.assertIn("Unknown sub-account ACC_GHOST_99", msg)

    def test_risk_reducing_order_approved_on_breached_firm(self):
        # Firm already over its GMV cap (800 x $150 = $120k vs $100k cap).
        agg = CrossAccountRiskAggregator(max_firm_gmv_limit_usd=100_000.0)
        agg.register_account(SubAccountState(
            "A", "IBKR", cash_usd=0.0, margin_used_usd=50_000.0,
            margin_limit_usd=100_000.0, positions={"AAPL": 800.0}))
        self.assertFalse(agg.aggregate_firm_risk({"AAPL": 150.0}).is_compliant)

        # Selling down to 500 shares ($75k) must NOT be vetoed by the breach.
        approved, _ = agg.evaluate_pre_trade_order(
            "A", "AAPL", proposed_qty=-300.0, price=150.0, market_prices={"AAPL": 150.0})
        self.assertTrue(approved)

    def test_pre_trade_uses_order_price_for_traded_symbol(self):
        # Stale feed says AAPL=$150; the live order price $900 drives the audit.
        # Projected AAPL net = 600 + 1,000 = 1,600 sh @ $900 = $1.44M, plus
        # NVDA $100k -> GMV $1.54M breaches the $1M cap.
        approved, msg = self.aggregator.evaluate_pre_trade_order(
            "ACC_IBKR_01", "AAPL", proposed_qty=1000.0, price=900.0,
            market_prices=self.market_prices)
        self.assertFalse(approved)
        self.assertIn("Firm GMV limit breached", msg)

    # ---------- Registry semantics ----------

    def test_reregistration_replaces_account_record(self):
        self.aggregator.register_account(SubAccountState(
            "ACC_IBKR_01", "InteractiveBrokers",
            cash_usd=999_999.0, margin_used_usd=0.0, margin_limit_usd=200_000.0,
            positions={}))
        report = self.aggregator.aggregate_firm_risk(self.market_prices)
        # Replacement wipes the old book: only acc2's short remains.
        self.assertEqual(report.net_positions["AAPL"], -400.0)
        self.assertEqual(report.total_firm_nav_usd, 999_999.0 + 150_000.0 - 60_000.0)


if __name__ == '__main__':
    unittest.main()
