import unittest
from single_name_concentration_limiter import SingleNameConcentrationLimiter, OrderCheckResult

class TestSingleNameConcentrationLimiter(unittest.TestCase):

    def setUp(self):
        # 5% NAV limit, 10% ADV limit
        self.limiter = SingleNameConcentrationLimiter(max_nav_pct=0.05, max_adv_pct=0.10, allow_downsizing=True)

    def test_order_approved_in_full(self):
        # NAV = $1,000,000, 5% NAV = $50,000. Current = $10,000 -> $40,000 room (400 shares @ $100)
        # ADV = 100,000 -> 10% ADV = 10,000 shares
        # Order: 200 shares ($20,000) -> Fits both limits
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=200, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=10_000.0, twenty_day_adv=100_000
        )
        self.assertFalse(res.is_downsized)
        self.assertFalse(res.is_rejected)
        self.assertEqual(res.approved_quantity, 200)

    def test_nav_limit_downsizing(self):
        # NAV = $1,000,000, 5% NAV = $50,000. Current = $0 -> $50,000 room (500 shares @ $100)
        # ADV = 100,000 -> 10% ADV = 10,000 shares
        # Order: 800 shares ($80,000) -> Exceeds NAV limit ($50k)
        # Downsizes to 500 shares
        res = self.limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=800, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.nav_limit_breached)
        self.assertEqual(res.approved_quantity, 500)

    def test_adv_limit_downsizing(self):
        # NAV = $10,000,000, 5% NAV = $500,000 -> 5000 shares @ $100
        # ADV = 1,000 -> 10% ADV = 100 shares limit!
        # Order: 500 shares -> Exceeds ADV limit
        # Downsizes to 100 shares
        res = self.limiter.evaluate_order(
            symbol="ILLIQUID", side="BUY", proposed_quantity=500, price=100.0,
            portfolio_nav=10_000_000.0, current_position_value=0.0, twenty_day_adv=1_000
        )
        self.assertTrue(res.is_downsized)
        self.assertTrue(res.adv_limit_breached)
        self.assertEqual(res.approved_quantity, 100)

    def test_hard_rejection_without_downsizing(self):
        strict_limiter = SingleNameConcentrationLimiter(max_nav_pct=0.05, max_adv_pct=0.10, allow_downsizing=False)
        res = strict_limiter.evaluate_order(
            symbol="AAPL", side="BUY", proposed_quantity=800, price=100.0,
            portfolio_nav=1_000_000.0, current_position_value=0.0, twenty_day_adv=100_000
        )
        self.assertTrue(res.is_rejected)
        self.assertEqual(res.approved_quantity, 0)

    def test_hhi_calculation(self):
        # 10 equal positions of $100,000 -> Each weight = 0.10
        # HHI = 10 * (0.10^2) = 0.10
        # N_eff = 1 / 0.10 = 10.0
        positions = [100_000.0] * 10
        hhi_res = SingleNameConcentrationLimiter.calculate_hhi(positions)
        
        self.assertEqual(hhi_res["hhi"], 0.10)
        self.assertEqual(hhi_res["effective_n"], 10.0)

if __name__ == '__main__':
    unittest.main()
