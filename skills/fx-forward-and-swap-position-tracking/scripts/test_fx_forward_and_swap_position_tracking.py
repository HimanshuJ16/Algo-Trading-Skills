import unittest
from fx_forward_and_swap_position_tracking import (
    FxForwardSwapTrackingEngine, FxContractPosition, FxForwardPositionReport
)

class TestFxForwardSwapTrackingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FxForwardSwapTrackingEngine(day_count_basis=360)

    def test_cirp_forward_rate_calculation(self):
        # Spot = 1.1000, r_EUR = 3.0%, r_USD = 5.0%, T = 90 days
        # F = 1.1000 * (1 + 0.05 * 90/360) / (1 + 0.03 * 90/360) = 1.1000 * 1.0125 / 1.0075 = 1.105459
        f_rate = self.engine.calculate_cirp_forward_rate(spot_rate=1.1000, base_interest_rate=0.03, quote_interest_rate=0.05, days_to_maturity=90)
        self.assertAlmostEqual(f_rate, 1.105459, places=4)

    def test_audit_portfolio_positions_mtm_pnl(self):
        positions = [
            FxContractPosition("FWD_EUR_01", "EUR/USD", "EUR", "USD", "FX_FORWARD", "BUY", 1_000_000.0, 1.1000, 90)
        ]
        market_rates = {
            "EUR/USD": {"spot": 1.1000, "r_base": 0.03, "r_quote": 0.05}
        }
        report = self.engine.audit_portfolio_positions(positions, market_rates)

        self.assertEqual(report.total_open_contracts, 1)
        self.assertEqual(report.net_exposure_by_base_currency["EUR"], 1_000_000.0)
        # F_fair = 1.105459. Agreed = 1.1000. MTM PnL = 1M * (1.105459 - 1.1000) = +$5,459.00
        self.assertGreater(report.net_unrealized_mtm_pnl_usd, 5000.0)

if __name__ == '__main__':
    unittest.main()
