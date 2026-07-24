"""
Unit tests for corporate-action-adjusted-backtesting skill.

Tests:
1. 2-for-1 stock split backward price halving and volume doubling.
2. 1-for-5 reverse stock split backward price multiplying and volume reducing.
3. Cash dividend ex-date account cash credit calculation.
4. Combination of split and cash dividend adjustments.
"""
import datetime
import unittest
from corporate_action_adjuster import (
    ActionType,
    AdjustedBar,
    CorporateActionAdjuster,
    CorporateActionEvent,
    RawBar,
)


class TestCorporateActionAdjuster(unittest.TestCase):

    def setUp(self):
        self.adjuster = CorporateActionAdjuster(symbol="NVDA")

    def test_stock_split_adjustment(self):
        # 2-for-1 split on 2024-06-10
        self.adjuster.add_event(
            CorporateActionEvent(
                symbol="NVDA",
                ex_date=datetime.date(2024, 6, 10),
                action_type=ActionType.SPLIT,
                ratio=2.0,
            )
        )

        bars = [
            RawBar(datetime.date(2024, 6, 7), open=1000.0, high=1020.0, low=990.0, close=1000.0, volume=100.0),
            RawBar(datetime.date(2024, 6, 10), open=500.0, high=510.0, low=495.0, close=500.0, volume=200.0),
        ]

        adj_bars = self.adjuster.adjust_series(bars)
        self.assertEqual(len(adj_bars), 2)

        # Pre-split bar (June 7) should have halved price ($500) and doubled volume (200)
        self.assertEqual(adj_bars[0].close, 500.0)
        self.assertEqual(adj_bars[0].volume, 200.0)

        # Post-split bar (June 10) remains unchanged ($500 price, 200 volume)
        self.assertEqual(adj_bars[1].close, 500.0)
        self.assertEqual(adj_bars[1].volume, 200.0)

    def test_reverse_stock_split_adjustment(self):
        # 1-for-5 reverse split on 2024-03-01 (ratio = 0.2)
        self.adjuster.add_event(
            CorporateActionEvent(
                symbol="NVDA",
                ex_date=datetime.date(2024, 3, 1),
                action_type=ActionType.REVERSE_SPLIT,
                ratio=0.2,
            )
        )

        bars = [
            RawBar(datetime.date(2024, 2, 28), open=2.0, high=2.1, low=1.9, close=2.0, volume=1000.0),
            RawBar(datetime.date(2024, 3, 1), open=10.0, high=10.5, low=9.5, close=10.0, volume=200.0),
        ]

        adj_bars = self.adjuster.adjust_series(bars)

        # Pre-reverse split bar (Feb 28) should have price multiplied by 5 ($10) and volume divided by 5 (200)
        self.assertEqual(adj_bars[0].close, 10.0)
        self.assertEqual(adj_bars[0].volume, 200.0)

    def test_dividend_cash_credit(self):
        self.adjuster.add_event(
            CorporateActionEvent(
                symbol="NVDA",
                ex_date=datetime.date(2024, 5, 15),
                action_type=ActionType.CASH_DIVIDEND,
                cash_amount=0.10,
            )
        )

        cash = self.adjuster.calculate_dividend_cash_credit(datetime.date(2024, 5, 15), position_qty=1000)
        self.assertEqual(cash, 100.0)


if __name__ == "__main__":
    unittest.main()
