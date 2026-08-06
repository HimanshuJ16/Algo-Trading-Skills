import unittest
from warrants_integration import (
    WarrantType,
    SettlementType,
    KnockOutStatus,
    WarrantContract,
    WarrantValuation,
    DeltaHedgeSignal,
    WarrantsIntegrationEngine,
    WarrantEngineError,
)


class TestWarrantsIntegrationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WarrantsIntegrationEngine()

        # 10 warrants for 1 share (Entitlement Ratio = 0.1)
        self.call_warrant = WarrantContract(
            warrant_id="WRT-CALL-100",
            symbol="2800.HK_C100",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.COVERED_CALL,
            strike_price=100.0,
            entitlement_ratio=0.1,
            days_to_expiry=90,
            risk_free_rate=0.03,
            implied_volatility=0.25,
        )

        self.turbo_bull = WarrantContract(
            warrant_id="WRT-TURBO-90",
            symbol="2800.HK_B90",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.TURBO_BULL_CBBC,
            strike_price=85.0,
            barrier_price=90.0,
            entitlement_ratio=0.1,
            days_to_expiry=60,
        )

    def test_covered_call_warrant_valuation(self):
        val = self.engine.price_warrant(spot_price=105.0, contract=self.call_warrant)

        self.assertEqual(val.status, KnockOutStatus.ACTIVE)
        self.assertTrue(val.fair_price_usd > 0.0)
        # Intrinsic = (105 - 100) * 0.1 = $0.50
        self.assertEqual(val.intrinsic_value_usd, 0.50)
        self.assertTrue(val.delta > 0.0 and val.delta <= 0.1)  # Delta scaled by 0.1
        self.assertTrue(val.effective_gearing > 1.0)

    def test_covered_put_warrant_valuation(self):
        put_warrant = WarrantContract(
            warrant_id="WRT-PUT-100",
            symbol="2800.HK_P100",
            underlying_symbol="2800.HK",
            warrant_type=WarrantType.COVERED_PUT,
            strike_price=100.0,
            entitlement_ratio=0.1,
            days_to_expiry=90,
        )
        val = self.engine.price_warrant(spot_price=95.0, contract=put_warrant)

        self.assertEqual(val.status, KnockOutStatus.ACTIVE)
        # Intrinsic = (100 - 95) * 0.1 = $0.50
        self.assertEqual(val.intrinsic_value_usd, 0.50)
        self.assertTrue(val.delta < 0.0)

    def test_turbo_bull_knockout_event(self):
        # Spot $88.0 <= Barrier $90.0 -> KNOCKED_OUT
        val = self.engine.price_warrant(spot_price=88.0, contract=self.turbo_bull)

        self.assertEqual(val.status, KnockOutStatus.KNOCKED_OUT)
        self.assertEqual(val.fair_price_usd, 0.0)
        self.assertEqual(val.delta, 0.0)
        self.assertEqual(val.effective_gearing, 0.0)

    def test_effective_gearing_calculation(self):
        val = self.engine.price_warrant(spot_price=100.0, contract=self.call_warrant)

        self.assertTrue(val.simple_gearing > 0.0)
        self.assertTrue(val.effective_gearing > 0.0)
        # Effective Gearing = Simple Gearing * Raw Delta
        self.assertTrue(val.effective_gearing <= val.simple_gearing)

    def test_delta_hedge_rebalance_signal_buy(self):
        val = self.engine.price_warrant(spot_price=105.0, contract=self.call_warrant)
        # Position = 1,000,000 warrants. If delta = 0.06 -> required shares = 60,000
        signal = self.engine.calculate_delta_hedge_signal(
            valuation=val, position_warrants=1_000_000, current_underlying_hedged_shares=50_000.0
        )

        self.assertTrue(signal.required_underlying_delta_shares > 50_000.0)
        self.assertEqual(signal.action, "BUY")
        self.assertTrue(signal.net_rebalance_shares > 0.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(WarrantEngineError):
            self.engine.price_warrant(spot_price=-100.0, contract=self.call_warrant)


if __name__ == "__main__":
    unittest.main()

