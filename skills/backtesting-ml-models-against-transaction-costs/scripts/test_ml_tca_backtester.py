import unittest
import numpy as np
from ml_tca_backtester import MlTcaBacktester, MlTcaBacktesterConfig

class TestMlTcaBacktester(unittest.TestCase):
    
    def test_high_turnover_cost_drag(self):
        # Model is perfect, but flips direction every day.
        # Predictions always exceed threshold.
        preds = np.array([0.05, -0.05, 0.05, -0.05, 0.05])
        actuals = np.array([0.05, -0.05, 0.05, -0.05, 0.05])
        
        # 100 bps cost per half-turn (1%). 
        config = MlTcaBacktesterConfig(bps_cost_half_turn=100.0, signal_threshold=0.01)
        tester = MlTcaBacktester(config)
        
        res = tester.process(preds, actuals)
        
        # Turnover calculation:
        # T0: flat to +1 (Turnover 1) -> Cost 1%
        # T1: +1 to -1 (Turnover 2) -> Cost 2%
        # T2: -1 to +1 (Turnover 2) -> Cost 2%
        # T3: +1 to -1 (Turnover 2) -> Cost 2%
        # T4: -1 to +1 (Turnover 2) -> Cost 2%
        # Total Turnover = 9 units
        
        self.assertEqual(res["Total Turnover (Units)"], 9.0)
        self.assertEqual(res["Total Trade Count"], 5)
        
        # Gross is massive because actuals perfectly match positions
        self.assertTrue(res["Total Gross Return"] > 0)
        
        # Net should be dragged heavily by the 9% total structural cost
        self.assertTrue(res["Cost Drag (%)"] > 0)

    def test_thresholding_prevents_trades(self):
        # Model predictions are very weak
        preds = np.array([0.0001, -0.0001, 0.0001])
        actuals = np.array([0.05, -0.05, 0.05])
        
        # Threshold requires 0.001 (10 bps) prediction to trade
        config = MlTcaBacktesterConfig(bps_cost_half_turn=5.0, signal_threshold=0.001)
        tester = MlTcaBacktester(config)
        
        res = tester.process(preds, actuals)
        
        # Predictions never exceed threshold -> 0 turnover, 0 gross, 0 net.
        self.assertEqual(res["Total Turnover (Units)"], 0.0)
        self.assertEqual(res["Total Gross Return"], 0.0)
        self.assertEqual(res["Total Net Return"], 0.0)

if __name__ == '__main__':
    unittest.main()
