import unittest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from categorical_feature_encoding_for_instrument_identity import PointInTimeTargetEncoder

class TestTargetEncoder(unittest.TestCase):
    
    def setUp(self):
        self.encoder = PointInTimeTargetEncoder(smoothing_weight=1.0)
        
        # Create a simple dataset
        # Day 1: AAPL (1.0), TSLA (-1.0)
        # Day 2: AAPL (1.0), MSFT (0.0) <- MSFT is new
        # Day 3: AAPL (1.0)
        
        base_time = datetime(2025, 1, 1)
        data = {
            'timestamp': [
                base_time, base_time,
                base_time + timedelta(days=1), base_time + timedelta(days=1),
                base_time + timedelta(days=2)
            ],
            'symbol': ['AAPL', 'TSLA', 'AAPL', 'MSFT', 'AAPL'],
            'target': [1.0, -1.0, 1.0, 0.0, 1.0]
        }
        self.df = pd.DataFrame(data)

    def test_no_lookahead_bias_first_row(self):
        df_encoded = self.encoder.fit_transform(self.df, 'timestamp', 'symbol', 'target')
        
        # At day 1, there is no historical data. Global mean is 0, local is 0.
        # Encoded value should be exactly 0.0 for AAPL and TSLA
        self.assertEqual(df_encoded.iloc[0]['symbol_encoded'], 0.0)
        self.assertEqual(df_encoded.iloc[1]['symbol_encoded'], 0.0)

    def test_historical_update(self):
        df_encoded = self.encoder.fit_transform(self.df, 'timestamp', 'symbol', 'target')
        
        # On Day 2 (index 2 for AAPL), history is: global (1.0, -1.0 -> sum 0, mean 0), local AAPL (1.0)
        # Weight = 1.0
        # local_count = 1, local_mean = 1.0
        # global_mean = 0.0
        # smoothed = (1 * 1.0 + 1 * 0.0) / (1 + 1) = 0.5
        self.assertEqual(df_encoded.iloc[2]['symbol_encoded'], 0.5)

    def test_cold_start_fallback(self):
        df_encoded = self.encoder.fit_transform(self.df, 'timestamp', 'symbol', 'target')
        
        # On Day 2 (index 3 for MSFT), MSFT is brand new.
        # History: global sum=0 (1,-1), count=2 -> global mean = 0.0
        # MSFT local count = 0
        # smoothed = (0 * 0.0 + 1 * 0.0) / (0 + 1) = 0.0
        self.assertEqual(df_encoded.iloc[3]['symbol_encoded'], 0.0)

if __name__ == '__main__':
    unittest.main()
