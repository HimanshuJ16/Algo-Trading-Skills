import unittest
from interest_rate_swap_exposure_in_multi_asset_portfolios import (
    InterestRateSwapExposureEngine, IrsPositionSpec, MultiAssetPortfolioRisk, InterestRateSwapExposureReport
)

class TestInterestRateSwapExposureEngine(unittest.TestCase):

    def setUp(self):
        self.engine = InterestRateSwapExposureEngine()

    def test_pay_fixed_swap_dv01_calculation(self):
        # Pay-Fixed 5Y Swap $10M Notional -> Mod Dur = 2.5 -> Base DV01 = 10M * 2.5 * 0.0001 = +$2,500 / bps
        spec = IrsPositionSpec("IRS_01", notional_usd=10_000_000.0, pay_receive_type="PAY_FIXED", fixed_rate_pct=4.25, tenor_years=5.0)
        dv01 = self.engine.calculate_swap_dv01(spec)
        self.assertEqual(dv01, 2500.0)

    def test_receive_fixed_swap_dv01_calculation(self):
        # Receive-Fixed 5Y Swap $10M Notional -> DV01 = -$2,500 / bps
        spec = IrsPositionSpec("IRS_02", notional_usd=10_000_000.0, pay_receive_type="RECEIVE_FIXED", fixed_rate_pct=4.25, tenor_years=5.0)
        dv01 = self.engine.calculate_swap_dv01(spec)
        self.assertEqual(dv01, -2500.0)

    def test_portfolio_dv01_aggregation_and_hedging(self):
        # Bond DV01 = -$5,000 / bps (Long bond portfolio loses $5k per +1 bps rate rise)
        # IRS Pay-Fixed $10M = +$2,500 / bps
        # Net Portfolio DV01 = -$2,500 / bps
        # Required 5Y Pay-Fixed Hedge Notional to achieve 0.0 DV01 = +$10M Notional!
        portfolio = MultiAssetPortfolioRisk(
            bonds_dv01_usd=-5000.0,
            equities_notional_usd=5_000_000.0,
            irs_positions=[
                IrsPositionSpec("IRS_01", notional_usd=10_000_000.0, pay_receive_type="PAY_FIXED", fixed_rate_pct=4.25, tenor_years=5.0)
            ]
        )
        report = self.engine.analyze_portfolio_irs_exposure(portfolio)

        self.assertEqual(report.status, "IRS_RISK_AUDIT_SUCCESS")
        self.assertEqual(report.total_irs_dv01_usd, 2500.0)
        self.assertEqual(report.net_portfolio_dv01_usd, -2500.0)
        self.assertEqual(report.pnl_impact_plus_10bps_usd, -25000.0) # -$2.5k * 10 = -$25k
        self.assertEqual(report.required_hedge_irs_notional_usd, 10_000_000.0) # +$10M Pay-Fixed

if __name__ == '__main__':
    unittest.main()
