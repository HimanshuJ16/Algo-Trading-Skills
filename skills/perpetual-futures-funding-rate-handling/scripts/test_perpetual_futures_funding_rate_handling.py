import unittest
from perpetual_futures_funding_rate_handling import (
    Configuration,
    PerpetualFuturesFundingRateHandlingEngine,
    PerpetualPosition,
    FundingRateUpdate,
    FundingPolicyConfig,
    FundingRateReport
)

class TestPerpetualFuturesFundingRateHandling(unittest.TestCase):

    def test_default_execution_legacy(self):
        engine = PerpetualFuturesFundingRateHandlingEngine()
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 5.0)

    def test_disabled_execution_legacy(self):
        engine = PerpetualFuturesFundingRateHandlingEngine(Configuration(enabled=False))
        res = engine.execute({"value": 10})
        self.assertEqual(res["status"], "disabled")

    def test_empty_data_legacy(self):
        engine = PerpetualFuturesFundingRateHandlingEngine()
        res = engine.execute({})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["value"], 0.0)

    def test_funding_payment_long_outflow_and_apr(self):
        # 10 BTC Long @ $50,000 mark price = $500,000 notional
        pos = PerpetualPosition("BTCUSDT", 10.0, "LONG", 49000.0, 50000.0)
        # Funding rate +0.01% (+0.0001) per 8h
        update = FundingRateUpdate("BTCUSDT", 0.0001, "2026-07-31T16:00:00Z", 8)

        engine = PerpetualFuturesFundingRateHandlingEngine()
        report = engine.process_funding_update(pos, update)

        self.assertEqual(report.status, "FUNDING_OUTFLOW_OK")
        self.assertEqual(report.funding_payment_usd, 50.0)    # $500,000 * 0.0001 = $50.00 fee
        self.assertAlmostEqual(report.annualized_funding_apr, 10.95, delta=0.1) # 0.01% * 3 * 365 = 10.95%

    def test_adverse_funding_drag_breach(self):
        # 10 BTC Long @ $50,000 mark price
        pos = PerpetualPosition("BTCUSDT", 10.0, "LONG", 49000.0, 50000.0)
        # Extreme positive rate +0.10% (+0.0010) per 8h = 109.5% APR > 25% max limit
        update = FundingRateUpdate("BTCUSDT", 0.0010, "2026-07-31T16:00:00Z", 8)

        policy = FundingPolicyConfig(max_adverse_funding_apr=0.25)
        engine = PerpetualFuturesFundingRateHandlingEngine(policy=policy)

        report = engine.process_funding_update(pos, update)
        self.assertEqual(report.status, "ADVERSE_FUNDING_DRAG_BREACH")
        self.assertTrue(report.is_adverse_drag_high)

if __name__ == '__main__':
    unittest.main()
