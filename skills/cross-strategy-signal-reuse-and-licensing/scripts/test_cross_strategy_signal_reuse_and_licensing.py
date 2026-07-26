import unittest
from cross_strategy_signal_reuse_and_licensing import (
    SignalReuseAndLicensingEngine, SignalProfile, StrategySubscription, FeeAttributionReport
)

class TestSignalReuseAndLicensingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = SignalReuseAndLicensingEngine()
        
        # Signal: Base Fee $10,000, 5% PnL Share, Max AUM $50,000,000
        self.sig = SignalProfile(
            signal_id="SIG_SENTIMENT_01",
            signal_name="Global NLP Sentiment Alpha",
            owner_entity="US_Quant_Research",
            base_license_fee_annual_usd=10_000.0,
            pnl_share_pct=0.05,
            max_aum_capacity_usd=50_000_000.0
        )
        self.engine.register_signal(self.sig)

    def test_entitlement_grant_and_capacity_breach(self):
        sub1 = StrategySubscription("SUB_01", "Pod_Alpha", "SIG_SENTIMENT_01", "US_Desk", 20_000_000.0, True)
        res1 = self.engine.request_subscription(sub1)
        self.assertTrue(res1.is_entitled)
        self.assertEqual(res1.current_total_subscribed_aum_usd, 20_000_000.0)

        sub2 = StrategySubscription("SUB_02", "Pod_Beta", "SIG_SENTIMENT_01", "UK_Desk", 25_000_000.0, True)
        res2 = self.engine.request_subscription(sub2)
        self.assertTrue(res2.is_entitled)
        self.assertEqual(res2.current_total_subscribed_aum_usd, 45_000_000.0)

        # sub3 tries to add $10,000,000 -> Total $55M > $50M limit!
        sub3 = StrategySubscription("SUB_03", "Pod_Gamma", "SIG_SENTIMENT_01", "HK_Desk", 10_000_000.0, True)
        res3 = self.engine.request_subscription(sub3)
        self.assertFalse(res3.is_entitled)
        self.assertIn("capacity cap breached", res3.reason.lower())

    def test_transfer_pricing_fee_attribution(self):
        sub = StrategySubscription("SUB_01", "Pod_Alpha", "SIG_SENTIMENT_01", "US_Desk", 20_000_000.0, True)
        self.engine.request_subscription(sub)
        
        # Strategy generated $1,000,000 PnL
        # Fee = Base ($10,000) + 5% * $1,000,000 ($50,000) = $60,000
        report = self.engine.calculate_transfer_pricing_fee("SUB_01", strategy_realized_pnl_usd=1_000_000.0)
        
        self.assertEqual(report.base_fee_usd, 10_000.0)
        self.assertEqual(report.pnl_share_fee_usd, 50_000.0)
        self.assertEqual(report.total_fee_usd, 60_000.0)
        self.assertTrue(report.arm_length_compliant)

if __name__ == '__main__':
    unittest.main()
