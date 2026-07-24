"""
Unit tests for multi-strategy-capital-allocation-limits skill.
"""
import unittest
from capital_allocator import AllocationError, MultiStrategyCapitalAllocator


class TestMultiStrategyCapitalAllocator(unittest.TestCase):

    def setUp(self):
        self.allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.10)
        self.allocator.register_strategy("momentum", 0.40)
        self.allocator.register_strategy("mean_revert", 0.30)
        self.allocator.register_strategy("stat_arb", 0.20)
        self.allocator.validate_allocations()

    def test_order_within_allocation(self):
        """Order within cap should be approved."""
        self.allocator.update_exposure("momentum", 20000.0)
        result = self.allocator.check_order("momentum", 15000.0, 100000.0)
        # Max = 40000, current = 20000, order = 15000, projected = 35000 < 40000
        self.assertTrue(result.approved)
        self.assertAlmostEqual(result.max_allowed_usd, 40000.0)

    def test_order_exceeds_allocation(self):
        """Order exceeding cap should be rejected."""
        self.allocator.update_exposure("mean_revert", 25000.0)
        result = self.allocator.check_order("mean_revert", 10000.0, 100000.0)
        # Max = 30000, current = 25000, order = 10000, projected = 35000 > 30000
        self.assertFalse(result.approved)
        self.assertIn("ALLOCATION BREACH", result.rejection_reason)

    def test_over_allocation_validation(self):
        """Total allocations exceeding investable capital should raise error."""
        allocator = MultiStrategyCapitalAllocator(cash_reserve_pct=0.10)
        allocator.register_strategy("s1", 0.50)
        allocator.register_strategy("s2", 0.50)  # total = 100% > 90% investable
        with self.assertRaises(AllocationError):
            allocator.validate_allocations()

    def test_utilization_report(self):
        """Utilization report should reflect current exposures."""
        self.allocator.update_exposure("momentum", 20000.0)
        self.allocator.update_exposure("mean_revert", 15000.0)
        report = self.allocator.get_utilization_report(100000.0)
        self.assertEqual(len(report), 3)
        momentum = next(r for r in report if r["strategy"] == "momentum")
        self.assertAlmostEqual(momentum["utilization_pct"], 0.50)  # 20k / 40k


if __name__ == "__main__":
    unittest.main()
