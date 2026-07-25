import unittest
from capital_reallocation_engine import (
    CapitalReallocationEngine,
    StrategyMetrics
)

class TestCapitalReallocationEngine(unittest.TestCase):
    
    def setUp(self):
        # 1 Million total fund, using Half-Kelly
        self.engine = CapitalReallocationEngine(total_fund_capital=1_000_000.0, kelly_fraction=0.5)

    def test_kelly_calculation(self):
        # Win rate 0.60, Avg Win = 200, Avg Loss = 100 (R=2.0)
        # Kelly = 0.60 - (0.40 / 2.0) = 0.60 - 0.20 = 0.40
        strat = StrategyMetrics("S1", 0, 1000000.0, 0.60, 200, 100)
        k = self.engine._calculate_kelly_weight(strat)
        self.assertAlmostEqual(k, 0.40)

    def test_zero_edge_moves_to_cash(self):
        # Win rate 0.40, Avg Win 100, Avg Loss 100 -> Kelly < 0
        s1 = StrategyMetrics("S1", 100000, 500000, 0.40, 100, 100)
        strats = {"S1": s1}
        
        instructions = self.engine.reallocate(strats)
        self.assertEqual(instructions["S1"].new_target_capital, 0.0)
        self.assertEqual(instructions["S1"].delta_capital, -100000.0)

    def test_reallocation_with_capacity_limits(self):
        # Both strategies have identical edge, so should split 50/50 raw.
        # But S1 has a capacity limit of 200k.
        s1 = StrategyMetrics("S1", 100000, 200000, 0.60, 200, 100)
        s2 = StrategyMetrics("S2", 100000, 1000000, 0.60, 200, 100)
        
        strats = {"S1": s1, "S2": s2}
        instructions = self.engine.reallocate(strats)
        
        # S1 hits cap
        self.assertAlmostEqual(instructions["S1"].new_target_capital, 200000.0)
        
        # S2 takes the rest of the 1M
        self.assertAlmostEqual(instructions["S2"].new_target_capital, 800000.0)
        
        # Total capital allocated is 1M
        self.assertAlmostEqual(instructions["S1"].new_target_capital + instructions["S2"].new_target_capital, 1000000.0)

if __name__ == '__main__':
    unittest.main()
