import unittest
from datetime import date
from corporate_action_adjuster import (
    CorporateActionAdjuster, CorporateActionEvent, BarData
)

class TestCorporateActionAdjuster(unittest.TestCase):

    def test_stock_split_adjustment(self):
        # 2-for-1 stock split on Day 3 (2025-01-03)
        # Raw prices: Day 1: $100, Day 2: $100, Day 3 (ex-date): $50, Day 4: $50
        events = [CorporateActionEvent(ex_date=date(2025, 1, 3), event_type="SPLIT", value=2.0)]
        adjuster = CorporateActionAdjuster(events)

        bars = [
            BarData(date(2025, 1, 1), 100, 100, 100, 100, 1000),
            BarData(date(2025, 1, 2), 100, 100, 100, 100, 1000),
            BarData(date(2025, 1, 3), 50, 50, 50, 50, 2000),
            BarData(date(2025, 1, 4), 50, 50, 50, 50, 2000),
        ]

        adj_bars = adjuster.adjust_bars(bars)
        
        # Days 1 & 2 historical adjusted prices should be scaled down by 0.5 -> $50
        self.assertEqual(adj_bars[0].adj_close, 50.0)
        self.assertEqual(adj_bars[1].adj_close, 50.0)
        # Days 3 & 4 adjusted prices remain $50 (CAF = 1.0)
        self.assertEqual(adj_bars[2].adj_close, 50.0)
        self.assertEqual(adj_bars[3].adj_close, 50.0)

    def test_cash_dividend_adjustment(self):
        # $2.00 cash dividend on Day 2 when raw close was $100 -> Factor = (100-2)/100 = 0.98
        events = [CorporateActionEvent(ex_date=date(2025, 1, 2), event_type="DIVIDEND", value=2.0)]
        adjuster = CorporateActionAdjuster(events)

        bars = [
            BarData(date(2025, 1, 1), 100, 100, 100, 100, 1000),
            BarData(date(2025, 1, 2), 98, 98, 98, 100, 1000),
            BarData(date(2025, 1, 3), 98, 98, 98, 98, 1000),
        ]

        adj_bars = adjuster.adjust_bars(bars)
        
        # Day 1 historical adjusted close should be 100 * 0.98 = 98.0
        self.assertEqual(adj_bars[0].adj_close, 98.0)
        # Days 2 & 3 adjusted close remain unchanged (CAF = 1.0)
        self.assertEqual(adj_bars[1].adj_close, 100.0)
        self.assertEqual(adj_bars[2].adj_close, 98.0)

if __name__ == '__main__':
    unittest.main()
