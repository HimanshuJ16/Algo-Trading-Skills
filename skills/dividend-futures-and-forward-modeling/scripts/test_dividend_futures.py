import unittest
from dividend_futures import (
    DividendForwardModelingEngine, DiscreteDividendEvent, DividendForwardAuditReport
)

class TestDividendForwardModelingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DividendForwardModelingEngine(arbitrage_cost_threshold_usd=0.50)

    def test_theoretical_forward_price_with_discrete_dividends(self):
        # Spot = $100.0, r = 5.0%, T = 1.0 yr
        # Dividend 1: $2.00 at t = 0.25 yrs -> PV = 2.00 * exp(-0.05 * 0.25) = 2.00 * 0.987577 = 1.97515
        # Dividend 2: $2.00 at t = 0.75 yrs -> PV = 2.00 * exp(-0.05 * 0.75) = 2.00 * 0.963194 = 1.92639
        # Total PV(D) = 3.9015
        # Theo Forward F(0, T) = (100 - 3.9015) * exp(0.05) = 96.0985 * 1.051271 = 101.0259 ~ $101.03
        divs = [
            DiscreteDividendEvent("DIV_Q1", amount_usd=2.00, payment_time_years=0.25),
            DiscreteDividendEvent("DIV_Q3", amount_usd=2.00, payment_time_years=0.75)
        ]
        
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=divs, market_forward_price=101.03
        )
        
        self.assertAlmostEqual(report.pv_dividends_usd, 3.9015, places=2)
        self.assertAlmostEqual(report.theoretical_forward_price, 101.03, places=2)
        self.assertEqual(report.fair_value_dividend_future_points, 4.0)
        self.assertEqual(report.arbitrage_opportunity, "NO_ARBITRAGE")

    def test_cash_and_carry_arbitrage_detection(self):
        # Market forward = $104.00 (vs Theo $101.03) -> Spread = $2.97 > $0.50 Threshold -> Short Forward / Long Spot!
        divs = [
            DiscreteDividendEvent("DIV_Q1", amount_usd=2.00, payment_time_years=0.25),
            DiscreteDividendEvent("DIV_Q3", amount_usd=2.00, payment_time_years=0.75)
        ]
        
        report = self.engine.audit_forward_arbitrage(
            symbol="AAPL", spot_price=100.0, maturity_years=1.0, risk_free_rate=0.05,
            dividends=divs, market_forward_price=104.00
        )
        
        self.assertEqual(report.arbitrage_opportunity, "ARBITRAGE_SHORT_FORWARD_LONG_SPOT")
        self.assertGreater(report.estimated_gross_profit_usd, 2.0)

if __name__ == '__main__':
    unittest.main()
