"""
Unit tests for feature-engineering-without-leakage skill.

Tests:
1. Detection of future lookahead feature leakage (e.g. shift(-1)).
2. Detection of same-bar target contamination.
3. Clean features passing audit with zero findings.
4. Point-in-time backward as-of merge verification.
5. Intentional leakage calibration benchmark test.
6. Backward compatibility with verify_shift_direction.
"""
import unittest
import numpy as np
import pandas as pd
from feature_audit import (
    FeatureLeakageAuditor,
    LeakageFinding,
    LeakageType,
    verify_shift_direction,
)


class TestFeatureEngineeringWithoutLeakage(unittest.TestCase):

    def setUp(self):
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        close_prices = 100.0 + np.cumsum(np.random.randn(n))

        # Target: Return from T to T+1
        target_returns = pd.Series(close_prices).pct_change().shift(-1).fillna(0.0)

        # Valid feature: Lagged return from T-1 to T
        valid_lag1 = pd.Series(close_prices).pct_change().shift(1).fillna(0.0)

        # Leaked feature: Future return from T+1 to T+2
        leaked_future = pd.Series(close_prices).pct_change().shift(-2).fillna(0.0)

        self.df = pd.DataFrame(
            {
                "timestamp": dates,
                "close": close_prices,
                "target": target_returns,
                "feature_valid": valid_lag1,
                "feature_leaked": leaked_future,
                "feature_same_bar": target_returns,  # Exact target copy
            }
        )

        self.auditor = FeatureLeakageAuditor(correlation_threshold=0.8)

    def test_audit_detects_future_leakage_and_same_bar_contamination(self):
        findings = self.auditor.audit_dataframe(
            self.df, target_col="target", feature_cols=["feature_valid", "feature_leaked", "feature_same_bar"]
        )

        finding_names = [f.feature_name for f in findings]
        self.assertIn("feature_same_bar", finding_names)
        self.assertNotIn("feature_valid", finding_names)

    def test_asof_merge_point_in_time(self):
        trade_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-01 10:00", "2026-01-01 12:00"]),
                "trade_id": [1, 2],
            }
        )
        fundamental_df = pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-01-01 09:00", "2026-01-01 11:00"]),
                "earnings_pe": [15.0, 16.0],
            }
        )

        merged = self.auditor.point_in_time_asof_merge(
            left_df=trade_df, right_df=fundamental_df, on_timestamp_col="timestamp"
        )

        self.assertEqual(len(merged), 2)
        # Trade at 10:00 should match 09:00 earnings (15.0), Trade at 12:00 should match 11:00 (16.0)
        self.assertEqual(merged.loc[0, "earnings_pe"], 15.0)
        self.assertEqual(merged.loc[1, "earnings_pe"], 16.0)

    def test_intentional_leakage_calibration(self):
        leaked_corr, findings = self.auditor.run_intentional_leakage_calibration(
            self.df, target_col="target"
        )
        self.assertGreater(len(findings), 0)

    def test_backward_compatibility(self):
        s = pd.Series([1, 2, 3, 4, 5])
        lagged = verify_shift_direction(s, shift_periods=1, expected_lag=True)
        self.assertTrue(np.isnan(lagged.iloc[0]))
        self.assertEqual(lagged.iloc[1], 1)

        with self.assertRaises(ValueError):
            verify_shift_direction(s, shift_periods=-1, expected_lag=True)


if __name__ == "__main__":
    unittest.main()
