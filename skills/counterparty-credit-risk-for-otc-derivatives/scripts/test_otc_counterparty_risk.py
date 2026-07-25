"""Unit tests for counterparty-credit-risk-for-otc-derivatives."""
import unittest
from otc_counterparty_risk import CounterpartyCreditRiskManager, CounterpartyProfile, OTCTrade


class TestCounterpartyCreditRiskManager(unittest.TestCase):
    def setUp(self):
        self.manager = CounterpartyCreditRiskManager()
        self.manager.register_counterparty(CounterpartyProfile(
            counterparty_id="DEALER_A",
            rating="BBB",
            probability_of_default=0.03,
            recovery_rate=0.40,
            credit_limit_usd=1000000.0,
            csa_collateral_threshold_usd=500000.0,
        ))

    def test_exposure_breach_and_margin_call(self):
        trades = [
            OTCTrade("t1", "DEALER_A", mark_to_market_usd=800000.0, volatility_usd=300000.0, maturity_years=1.0),
            OTCTrade("t2", "DEALER_A", mark_to_market_usd=400000.0, volatility_usd=200000.0, maturity_years=1.0),
        ]

        report = self.manager.evaluate_counterparty_exposure("DEALER_A", trades)

        self.assertTrue(report.is_limit_breached)
        self.assertGreater(report.potential_future_exposure_pfe95_usd, 1000000.0)
        self.assertGreater(report.credit_valuation_adjustment_cva_usd, 0.0)
        self.assertEqual(report.collateral_margin_call_usd, report.potential_future_exposure_pfe95_usd - 500000.0)

    def test_safe_exposure_no_breach(self):
        trades = [
            OTCTrade("t1", "DEALER_A", mark_to_market_usd=100000.0, volatility_usd=50000.0, maturity_years=0.5),
        ]

        report = self.manager.evaluate_counterparty_exposure("DEALER_A", trades)

        self.assertFalse(report.is_limit_breached)
        self.assertEqual(report.collateral_margin_call_usd, 0.0)


if __name__ == "__main__":
    unittest.main()
