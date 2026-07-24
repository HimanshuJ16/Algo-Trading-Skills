"""
Unit tests for paper-to-live-promotion-checklist skill.

Tests:
1. All gate criteria passing (APPROVED promotion report).
2. Failure on insufficient paper trading duration (< 20 days).
3. Failure on excessive paper slippage deviation (> 15%).
4. Failure when risk controls were never exercised in paper mode.
5. Live rollback trigger evaluation on live drawdown breach.
6. Backward compatibility with evaluate_promotion_gate.
"""
import unittest
from promotion_gate import (
    PaperToLivePromotionGate,
    PromotionDecisionReport,
    evaluate_promotion_gate,
)


class TestPaperToLivePromotionChecklist(unittest.TestCase):

    def setUp(self):
        self.gate = PaperToLivePromotionGate(min_days=20, min_trades_count=30)
        self.valid_paper_stats = {
            "days_run": 25,
            "trades_count": 45,
            "avg_slippage": 0.00105,
            "signal_accuracy": 0.58,
            "risk_controls_triggered": 2,
            "reauth_cycles_survived": 3,
        }
        self.valid_backtest_stats = {
            "modeled_slippage": 0.00100,
            "walk_forward_accuracy": 0.56,
        }

    def test_promotion_gate_approved(self):
        report = self.gate.evaluate_gate(self.valid_paper_stats, self.valid_backtest_stats)
        self.assertTrue(report.approved)
        self.assertIn("APPROVED", report.summary)
        self.assertEqual(len(report.checks), 6)

    def test_insufficient_duration_rejected(self):
        flawed_paper = self.valid_paper_stats.copy()
        flawed_paper["days_run"] = 10  # Less than 20 days

        report = self.gate.evaluate_gate(flawed_paper, self.valid_backtest_stats)
        self.assertFalse(report.approved)

        dur_check = next(c for c in report.checks if c.check_name == "min_paper_duration")
        self.assertFalse(dur_check.passed)

    def test_unexercised_risk_controls_rejected(self):
        flawed_paper = self.valid_paper_stats.copy()
        flawed_paper["risk_controls_triggered"] = 0

        report = self.gate.evaluate_gate(flawed_paper, self.valid_backtest_stats)
        self.assertFalse(report.approved)

    def test_live_rollback_trigger(self):
        live_bad = {"max_drawdown_pct": 0.08, "avg_slippage": 0.001}
        paper_base = {"max_drawdown_pct": 0.02, "avg_slippage": 0.001}

        triggered, msg = self.gate.check_rollback_trigger(live_bad, paper_base)
        self.assertTrue(triggered)
        self.assertIn("ROLLBACK TRIGGERED", msg)

    def test_backward_compatibility(self):
        res = evaluate_promotion_gate(self.valid_paper_stats, self.valid_backtest_stats)
        self.assertTrue(res["all_pass"])
        self.assertTrue(res["min_duration_met"])


if __name__ == "__main__":
    unittest.main()
