import unittest
import datetime
from wash_sale_rule_tracking_us import (
    TradeSide,
    TradeExecution,
    WashSaleMatch,
    WashSaleSummary,
    USWashSaleTrackingEngine,
    WashSaleError,
)


class TestUSWashSaleTrackingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = USWashSaleTrackingEngine(window_days=30)
        self.today = datetime.date(2025, 6, 1)

    def test_classic_wash_sale_replacement_buy_after_loss(self):
        # 1. Buy 100 AAPL @ $150 on Jan 10
        # 2. Sell 100 AAPL @ $100 on Jan 20 (Loss of $5,000)
        # 3. Buy 100 AAPL @ $110 on Feb 5 (Replacement buy 16 days after loss)
        self.engine.add_trade(
            TradeExecution("T1", "AAPL", datetime.date(2025, 1, 10), TradeSide.BUY, 150.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T2", "AAPL", datetime.date(2025, 1, 20), TradeSide.SELL, 100.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T3", "AAPL", datetime.date(2025, 2, 5), TradeSide.BUY, 110.0, 100.0)
        )

        summary = self.engine.evaluate_wash_sales_for_symbol("AAPL")

        self.assertEqual(summary.total_realized_gross_pnl_usd, -5000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 5000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)
        self.assertEqual(len(summary.wash_matches), 1)

        match = summary.wash_matches[0]
        self.assertEqual(match.disallowed_loss_usd, 5000.0)
        # Adjusted basis for T3: $110 + ($5000 / 100) = $160
        self.assertEqual(match.adjusted_replacement_basis_per_share, 160.0)

    def test_pre_loss_replacement_buy_within_30_days(self):
        # Buy replacement 10 days BEFORE selling at a loss
        # 1. Buy 100 TSLA @ $200 on March 1
        # 2. Buy 100 TSLA @ $180 on March 10 (Replacement buy)
        # 3. Sell 100 TSLA @ $150 on March 15 (Loss of $5,000 on March 1 lot)
        self.engine.add_trade(
            TradeExecution("T1", "TSLA", datetime.date(2025, 3, 1), TradeSide.BUY, 200.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T2", "TSLA", datetime.date(2025, 3, 10), TradeSide.BUY, 180.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T3", "TSLA", datetime.date(2025, 3, 15), TradeSide.SELL, 150.0, 100.0)
        )

        summary = self.engine.evaluate_wash_sales_for_symbol("TSLA")

        self.assertEqual(summary.total_disallowed_wash_loss_usd, 5000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, 0.0)

    def test_sale_outside_61_day_window_no_wash_sale(self):
        # Buy replacement 45 days after loss -> Outside 30-day window -> Recognized loss
        self.engine.add_trade(
            TradeExecution("T1", "NVDA", datetime.date(2025, 1, 10), TradeSide.BUY, 100.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T2", "NVDA", datetime.date(2025, 1, 20), TradeSide.SELL, 80.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T3", "NVDA", datetime.date(2025, 3, 15), TradeSide.BUY, 85.0, 100.0)
        )

        summary = self.engine.evaluate_wash_sales_for_symbol("NVDA")

        self.assertEqual(summary.total_realized_gross_pnl_usd, -2000.0)
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 0.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -2000.0)
        self.assertEqual(len(summary.wash_matches), 0)

    def test_partial_wash_sale_quantity_matching(self):
        # Sell 100 shares loss, but only buy 40 replacement shares
        self.engine.add_trade(
            TradeExecution("T1", "MSFT", datetime.date(2025, 4, 1), TradeSide.BUY, 300.0, 100.0)
        )
        self.engine.add_trade(
            TradeExecution("T2", "MSFT", datetime.date(2025, 4, 10), TradeSide.SELL, 200.0, 100.0)  # Loss $10,000 ($100/sh)
        )
        self.engine.add_trade(
            TradeExecution("T3", "MSFT", datetime.date(2025, 4, 15), TradeSide.BUY, 210.0, 40.0)   # Buy 40 shares
        )

        summary = self.engine.evaluate_wash_sales_for_symbol("MSFT")

        self.assertEqual(summary.total_realized_gross_pnl_usd, -10000.0)
        # Disallowed: 40 shares * $100 = $4,000. Allowed: 60 shares * $100 = -$6,000 loss
        self.assertEqual(summary.total_disallowed_wash_loss_usd, 4000.0)
        self.assertEqual(summary.net_allowed_taxable_pnl_usd, -6000.0)

    def test_invalid_trade_inputs_raise_error(self):
        with self.assertRaises(WashSaleError):
            self.engine.add_trade(
                TradeExecution("ERR", "AAPL", datetime.date(2025, 1, 1), TradeSide.BUY, -10.0, 100.0)
            )


if __name__ == "__main__":
    unittest.main()

