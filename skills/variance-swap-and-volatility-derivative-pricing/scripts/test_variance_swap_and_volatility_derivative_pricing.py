import unittest
import math
from variance_swap_and_volatility_derivative_pricing import (
    OptionType,
    SwapType,
    OptionQuote,
    VarianceSwapContract,
    RealizedVarianceResult,
    FairStrikeResult,
    VarianceSwapMTMResult,
    VarianceSwapPricingEngine,
    VariancePricingError,
)


class TestVarianceSwapPricingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = VarianceSwapPricingEngine()

        # Mock option strip around spot = 100.0, r = 0.05, T = 1.0 year
        self.option_strip = [
            OptionQuote(strike=80.0, option_type=OptionType.PUT, price=1.50),
            OptionQuote(strike=90.0, option_type=OptionType.PUT, price=3.20),
            OptionQuote(strike=100.0, option_type=OptionType.PUT, price=6.50),
            OptionQuote(strike=100.0, option_type=OptionType.CALL, price=8.20),
            OptionQuote(strike=110.0, option_type=OptionType.CALL, price=4.10),
            OptionQuote(strike=120.0, option_type=OptionType.CALL, price=1.80),
        ]

    def test_calculate_realized_variance(self):
        # Simulated daily prices with ~20% annualized volatility
        prices = [100.0, 101.5, 99.8, 102.1, 100.5, 101.2, 98.9, 100.0]
        res = self.engine.calculate_realized_variance(prices)

        self.assertEqual(res.num_observations, 7)
        self.assertTrue(res.annualized_realized_vol_pct > 10.0)
        self.assertTrue(res.annualized_realized_var > 100.0)

    def test_calculate_fair_strikes_log_contract_replication(self):
        spot = 100.0
        r = 0.05
        t_years = 1.0

        res = self.engine.calculate_fair_strikes(spot, r, t_years, self.option_strip)

        self.assertTrue(res.fair_variance_strike > 0.0)
        self.assertTrue(res.fair_volatility_strike > 0.0)
        self.assertAlmostEqual(res.forward_price, 100.0 * math.exp(0.05), places=4)
        self.assertEqual(res.num_options_used, 6)

    def test_variance_notional_calculation(self):
        contract = VarianceSwapContract(
            contract_id="VAR-001",
            symbol="SPX",
            swap_type=SwapType.VARIANCE_SWAP,
            strike_vol_pct=20.0,          # K_vol = 20% -> K_var = 400.0
            vega_notional_usd=100_000.0,  # $100,000 per vol point
            t_years=1.0,
            spot_price=100.0,
            risk_free_rate=0.05,
        )
        # N_var = N_vega / (2 * K_vol) = 100,000 / 40 = 2,500
        self.assertEqual(contract.variance_notional_usd, 2500.0)
        self.assertEqual(contract.strike_var, 400.0)

    def test_price_variance_swap_mtm_seasoned_contract(self):
        contract = VarianceSwapContract(
            contract_id="VAR-001",
            symbol="SPX",
            swap_type=SwapType.VARIANCE_SWAP,
            strike_vol_pct=20.0,          # K_vol = 20%, K_var = 400.0
            vega_notional_usd=100_000.0,  # N_var = 2500
            t_years=1.0,
            spot_price=100.0,
            risk_free_rate=0.05,
        )
        elapsed_t = 0.5  # 6 months elapsed
        prices = [100.0, 105.0, 95.0, 110.0, 90.0, 105.0]  # High volatility period (~40% vol)

        mtm_res = self.engine.price_variance_swap_mtm(
            contract=contract,
            elapsed_t_years=elapsed_t,
            price_history=prices,
            remaining_option_strip=self.option_strip,
        )

        self.assertEqual(mtm_res.contract_id, "VAR-001")
        self.assertTrue(mtm_res.realized_vol_so_far_pct > 25.0)
        # High realized vol -> Positive MTM for long variance receiver!
        self.assertTrue(mtm_res.current_mtm_usd > 0.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(VariancePricingError):
            self.engine.calculate_realized_variance([100.0])  # Only 1 price observation

        with self.assertRaises(VariancePricingError):
            self.engine.calculate_fair_strikes(spot=-10.0, r=0.05, t_years=1.0, option_strip=self.option_strip)


if __name__ == "__main__":
    unittest.main()

