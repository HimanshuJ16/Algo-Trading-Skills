import unittest
import pandas as pd
from synthetic_continuous_futures import (
    Config, Engine,
    SyntheticContinuousFuturesEngine, RollMethod, AdjustmentMethod,
    ContinuousFuturesSeries
)

class TestSyntheticContinuousFutures(unittest.TestCase):
    def setUp(self):
        self.config = Config("test")
        self.engine_legacy = Engine(self.config)
        self.builder = SyntheticContinuousFuturesEngine(
            roll_method=RollMethod.VOLUME_CROSSOVER,
            adjustment_method=AdjustmentMethod.ADDITIVE_BACK_ADJUSTMENT
        )

    def test_legacy_init_and_run(self):
        self.assertEqual(self.engine_legacy.config.name, "test")
        self.assertTrue(self.engine_legacy.run())

    def test_construct_continuous_series_volume_roll(self):
        # Create mock 2-contract data (ESH24 front, ESM24 back)
        dates = ["2024-03-01", "2024-03-02", "2024-03-03"]
        df_esh = pd.DataFrame({
            "close": [5000.0, 5010.0, 5020.0],
            "volume": [10000, 8000, 2000]
        }, index=dates)

        df_esm = pd.DataFrame({
            "close": [5020.0, 5035.0, 5045.0],
            "volume": [1000, 9000, 12000]  # Volume crossover occurs on 2024-03-02 (9000 > 8000)
        }, index=dates)

        contract_data = {
            "ESH24": df_esh,
            "ESM24": df_esm
        }

        series = self.builder.construct_continuous_series("ES", contract_data)

        self.assertEqual(series.ticker, "ES")
        self.assertEqual(series.total_roll_events, 1)
        # Gap on 2024-03-02: ESM close (5035) - ESH close (5010) = +25.0 gap
        self.assertEqual(series.cumulative_gap_usd, 25.0)

        # On 2024-03-02, active contract should be ESM24
        self.assertEqual(series.df_continuous.loc["2024-03-02", "active_contract"], "ESM24")

if __name__ == "__main__":
    unittest.main()
