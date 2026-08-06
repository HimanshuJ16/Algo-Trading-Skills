import unittest
import datetime
from weather_derivs import (
    WeatherIndexType,
    InstrumentType,
    WeatherDerivativeContract,
    BurnAnalysisResult,
    SettlementPayoff,
    WeatherDerivativesEngine,
    WeatherDerivativeError,
)


class TestWeatherDerivativesEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WeatherDerivativesEngine()

        self.cme_hdd_futures = WeatherDerivativeContract(
            contract_id="CME-HDD-NY-JAN25",
            symbol="HDF25_NY",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.FUTURES,
            tick_multiplier_usd=20.0,
        )

        self.cme_hdd_call = WeatherDerivativeContract(
            contract_id="CME-CALL-NY-800",
            symbol="HDF25_C800",
            location="NEW_YORK_KLGA",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CALL_OPTION,
            strike_index=800.0,
            tick_multiplier_usd=20.0,
        )

        self.capped_swap = WeatherDerivativeContract(
            contract_id="OTC-SWAP-CHI-1000",
            symbol="OTC_SWAP_ORD",
            location="CHICAGO_KORD",
            index_type=WeatherIndexType.HDD,
            instrument_type=InstrumentType.CAPPED_SWAP,
            strike_index=1000.0,
            tick_multiplier_usd=20.0,
            max_payout_usd=5000.0,  # $5,000 cap
        )

    def test_monthly_hdd_index_accumulation(self):
        # 10 days of T_min = 20, T_max = 40 -> T_mean = 30 -> Daily HDD = 35 -> Total = 350
        daily_temps = [(20.0, 40.0)] * 10
        idx = self.engine.calculate_monthly_index(daily_temps, WeatherIndexType.HDD)
        self.assertEqual(idx, 350.0)

    def test_cme_futures_settlement_payoff(self):
        # 900 HDD points * $20/point = $18,000 settlement payoff
        payoff = self.engine.calculate_settlement_payoff(self.cme_hdd_futures, accumulated_index=900.0)
        self.assertEqual(payoff.total_payoff_usd, 18000.0)
        self.assertFalse(payoff.is_capped)

    def test_cme_call_option_payoff_with_multiplier(self):
        # Index = 950, Strike = 800 -> Intrinsic = 150 points * $20 = $3,000
        payoff = self.engine.calculate_settlement_payoff(self.cme_hdd_call, accumulated_index=950.0)
        self.assertEqual(payoff.total_payoff_usd, 3000.0)

        # Index = 750 <= Strike 800 -> $0 payoff
        payoff_otm = self.engine.calculate_settlement_payoff(self.cme_hdd_call, accumulated_index=750.0)
        self.assertEqual(payoff_otm.total_payoff_usd, 0.0)

    def test_capped_otc_weather_swap_payoff(self):
        # Uncapped payoff: (1400 - 1000) * $20 = $8,000 -> Capped at $5,000
        payoff = self.engine.calculate_settlement_payoff(self.capped_swap, accumulated_index=1400.0)
        self.assertEqual(payoff.total_payoff_usd, 5000.0)
        self.assertTrue(payoff.is_capped)

    def test_burn_analysis_historical_simulation(self):
        # 5 historical seasons: [800, 850, 900, 950, 1000] HDDs
        historical_indexes = [800.0, 850.0, 900.0, 950.0, 1000.0]
        burn_res = self.engine.run_burn_analysis(self.cme_hdd_call, historical_indexes)

        self.assertEqual(burn_res.historical_seasons_analyzed, 5)
        self.assertEqual(burn_res.mean_index_value, 900.0)
        # Call Strike = 800. Payoffs for [800, 850, 900, 950, 1000] -> [0, $1000, $2000, $3000, $4000] -> Mean = $2000
        self.assertEqual(burn_res.expected_payoff_usd, 2000.0)

    def test_invalid_inputs_raise_error(self):
        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_monthly_index([], WeatherIndexType.HDD)

        with self.assertRaises(WeatherDerivativeError):
            self.engine.calculate_settlement_payoff(self.cme_hdd_futures, accumulated_index=-50.0)


if __name__ == "__main__":
    unittest.main()

