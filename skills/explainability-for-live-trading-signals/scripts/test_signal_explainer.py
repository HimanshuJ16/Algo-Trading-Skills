"""
Unit tests for explainability-for-live-trading-signals skill.

Tests:
1. Local feature contribution summation to prediction score.
2. Classification of top bullish and bearish feature drivers.
3. Natural language audit summary formatting.
4. JSON audit log serialization.
"""
import json
import unittest
from signal_explainer import LiveSignalExplainer, SignalExplanation


class TestLiveSignalExplainer(unittest.TestCase):

    def setUp(self):
        self.explainer = LiveSignalExplainer(
            base_value=0.10, buy_threshold=0.50, sell_threshold=-0.50
        )

    def test_buy_signal_explanation_and_drivers(self):
        features = {"rsi": 75.0, "volatility_zscore": 0.5, "sentiment": -0.2}
        contributions = {"rsi": 0.35, "volatility_zscore": 0.20, "sentiment": -0.05}

        exp = self.explainer.explain_signal("AAPL", features, contributions, timestamp=1700000000.0)

        # Base (0.10) + 0.35 + 0.20 - 0.05 = 0.60 -> BUY
        self.assertAlmostEqual(exp.prediction_score, 0.60, delta=0.01)
        self.assertEqual(exp.signal_action, "BUY")
        self.assertEqual(len(exp.top_bullish_drivers), 2)
        self.assertEqual(exp.top_bullish_drivers[0].feature_name, "rsi")
        self.assertEqual(len(exp.top_bearish_drivers), 1)
        self.assertEqual(exp.top_bearish_drivers[0].feature_name, "sentiment")

        # Verify natural language summary
        self.assertIn("BUY signal (+0.60) for 'AAPL'", exp.natural_language_summary)
        self.assertIn("driven by rsi (+0.35), volatility_zscore (+0.20)", exp.natural_language_summary)

    def test_json_compliance_audit_logging(self):
        features = {"macd": -0.05}
        contributions = {"macd": -0.60}

        exp = self.explainer.explain_signal("NVDA", features, contributions, timestamp=1700000000.0)
        self.assertEqual(exp.signal_action, "SELL")

        audit_json = exp.to_json_audit()
        parsed = json.loads(audit_json)

        self.assertEqual(parsed["action"], "SELL")
        self.assertEqual(parsed["symbol"], "NVDA")
        self.assertEqual(parsed["score"], -0.50)  # 0.10 - 0.60 = -0.50


if __name__ == "__main__":
    unittest.main()
