import unittest
import pandas as pd
from options_flow_unusual_activity_detection import (
    OptionsFlowUnusualActivityDetectionEngine, SignalResult, OptionsTrade, OptionsFlowAnomalyReport
)

class TestOptionsFlowUnusualActivityDetection(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsFlowUnusualActivityDetectionEngine()

    def test_legacy_empty_and_valid_data(self):
        df_empty = pd.DataFrame()
        signals_empty = self.engine.generate_signals(df_empty)
        self.assertEqual(len(signals_empty), 0)

        df_valid = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals_valid = self.engine.generate_signals(df_valid)
        self.assertEqual(len(signals_valid), 2)
        self.assertEqual(signals_valid[0].signal_value, 15.0)

    def test_detect_unusual_bullish_sweep(self):
        # 5,000 contracts on 1,000 OI (V/OI = 5.0x > 1.5x)
        # Price $5.00 at Ask $5.00 -> Total Premium = 5000 * 5.0 * 100 = $2,500,000
        trade = OptionsTrade(
            trade_id="TR_101",
            asset_id="NVDA",
            option_symbol="NVDA240621C00500000",
            option_type="CALL",
            volume=5000,
            open_interest=1000,
            adv=1000.0,
            execution_price=5.00,
            bid=4.80,
            ask=5.00,
            timestamp="2024-06-01T10:00:00Z"
        )

        report = self.engine.detect_unusual_activity(trade)
        self.assertTrue(report.is_unusual)
        self.assertEqual(report.classification, "UNUSUAL_BULLISH_SWEEP")
        self.assertEqual(report.vol_oi_ratio, 5.0)
        self.assertEqual(report.total_premium_usd, 2500000.0)

    def test_routine_flow_no_trigger(self):
        trade = OptionsTrade(
            trade_id="TR_102",
            asset_id="AAPL",
            option_symbol="AAPL240119C00150000",
            option_type="CALL",
            volume=10,
            open_interest=5000,
            adv=10000.0,
            execution_price=2.00,
            bid=1.95,
            ask=2.05,
            timestamp="2024-01-01T10:00:00Z"
        )

        report = self.engine.detect_unusual_activity(trade)
        self.assertFalse(report.is_unusual)
        self.assertEqual(report.classification, "ROUTINE_FLOW")

if __name__ == '__main__':
    unittest.main()
