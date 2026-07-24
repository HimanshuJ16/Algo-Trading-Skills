"""
Unit tests for lookahead-bias-elimination skill.

Tests:
1. Detection of same-bar fill contamination (signal at T filled at T Close).
2. Indicator warmup period violation detection.
3. Signal execution bar alignment (shifting signal T to execute at T+1 Open).
4. Forward leak injection calibration.
5. Backward compatibility with inject_forward_leak and check_feature_timestamps.
"""
import unittest
import pandas as pd
from leak_audit import (
    LookaheadBiasAuditor,
    LookaheadViolationType,
    check_feature_timestamps,
    inject_forward_leak,
)


class TestLookaheadBiasElimination(unittest.TestCase):

    def setUp(self):
        self.auditor = LookaheadBiasAuditor(warmup_periods=5)
        n = 20
        self.df = pd.DataFrame(
            {
                "open": [100.0 + i for i in range(n)],
                "close": [100.5 + i for i in range(n)],
                "signal": [0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                # Flawed backtest: filled at same-bar Close!
                "fill_price": [100.5 + i for i in range(n)],
            }
        )

    def test_audit_detects_same_bar_fill_and_warmup_violations(self):
        findings = self.auditor.audit_backtest_timing(
            self.df, signal_col="signal", close_col="close", fill_price_col="fill_price"
        )

        self.assertGreater(len(findings), 0)
        types = [f.violation_type for f in findings]
        self.assertIn(LookaheadViolationType.SAME_BAR_FILL_CONTAMINATION, types)
        self.assertIn(LookaheadViolationType.INDICATOR_UNWARMED_EXECUTION, types)  # Row 2 < 5 warmup

    def test_align_signal_execution_shifts_to_t_plus_one_open(self):
        aligned = LookaheadBiasAuditor.align_signal_execution(
            self.df, signal_col="signal", open_col="open"
        )

        # Original signal was at index 2 (1). Aligned executed_signal should be at index 3 (1).
        self.assertEqual(aligned.loc[2, "executed_signal"], 0)
        self.assertEqual(aligned.loc[3, "executed_signal"], 1)
        self.assertEqual(aligned.loc[3, "fill_price"], self.df.loc[3, "open"])

    def test_forward_leak_calibration(self):
        df_leaked = self.auditor.run_leak_calibration(self.df, target_col="close")
        self.assertIn("leaked_feature", df_leaked.columns)
        self.assertEqual(df_leaked.loc[0, "leaked_feature"], self.df.loc[1, "close"])

    def test_backward_compatibility(self):
        leaked = inject_forward_leak(self.df, "close")
        self.assertEqual(leaked.loc[0, "leaked_feature"], self.df.loc[1, "close"])

        violating = check_feature_timestamps(
            {"feat1": 100, "feat2": 200}, decision_ts=150
        )
        self.assertEqual(violating, ["feat2"])


if __name__ == "__main__":
    unittest.main()
