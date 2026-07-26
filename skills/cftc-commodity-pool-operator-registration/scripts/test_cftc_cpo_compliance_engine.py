import unittest
from cftc_cpo_compliance_engine import CftcCpoComplianceEngine, PortfolioState, ProposedTrade

class TestCftcCpoComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CftcCpoComplianceEngine()

    def test_non_commodity_trade_always_passes(self):
        # A stock trade requiring massive margin should pass because it's not a commodity interest
        portfolio = PortfolioState(liquidation_value=1_000_000, current_commodity_initial_margin=0, current_commodity_notional=0)
        trade = ProposedTrade(is_commodity_interest=False, required_initial_margin=500_000, notional_value=2_000_000)
        
        self.assertTrue(self.engine.check_trade_compliance(portfolio, trade))

    def test_passes_margin_fails_notional(self):
        # High leverage futures contract (e.g. Treasuries). Margin is 2%, Notional is 150%.
        # Should PASS because only one test is required.
        portfolio = PortfolioState(liquidation_value=1_000_000, current_commodity_initial_margin=0, current_commodity_notional=0)
        trade = ProposedTrade(is_commodity_interest=True, required_initial_margin=20_000, notional_value=1_500_000)
        
        self.assertTrue(self.engine.check_trade_compliance(portfolio, trade))

    def test_fails_margin_passes_notional(self):
        # Low leverage options on futures. Margin is 6%, Notional is 50%.
        # Should PASS because only one test is required.
        portfolio = PortfolioState(liquidation_value=1_000_000, current_commodity_initial_margin=0, current_commodity_notional=0)
        trade = ProposedTrade(is_commodity_interest=True, required_initial_margin=60_000, notional_value=500_000)
        
        self.assertTrue(self.engine.check_trade_compliance(portfolio, trade))

    def test_fails_both_tests(self):
        # Highly concentrated position. Margin is 6%, Notional is 110%.
        # Should FAIL.
        portfolio = PortfolioState(liquidation_value=1_000_000, current_commodity_initial_margin=0, current_commodity_notional=0)
        trade = ProposedTrade(is_commodity_interest=True, required_initial_margin=60_000, notional_value=1_100_000)
        
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

    def test_cumulative_portfolio_violation(self):
        # Portfolio is already near the limits.
        portfolio = PortfolioState(liquidation_value=1_000_000, current_commodity_initial_margin=45_000, current_commodity_notional=900_000)
        
        # Adding 10k margin (totals 55k > 50k) and 150k notional (totals 1.05M > 1M)
        trade = ProposedTrade(is_commodity_interest=True, required_initial_margin=10_000, notional_value=150_000)
        
        self.assertFalse(self.engine.check_trade_compliance(portfolio, trade))

if __name__ == '__main__':
    unittest.main()
