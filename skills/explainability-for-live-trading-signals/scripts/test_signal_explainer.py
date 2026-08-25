"""
Unit tests for explainability-for-live-trading-signals.

Coverage:
1. The additivity gate - it must PASS on a consistent attribution vector and FAIL on
   an inconsistent one (the regression that motivated this suite: the previous
   implementation derived the prediction score from the contributions, so the gate
   could never fail no matter how broken the explainer was).
2. Action classification from the model's actual output, boundary values included.
3. Driver ranking, direction labelling, and the "driven by" / "offset by" semantics on
   both BUY and SELL.
4. Top-N truncation, residual disclosure, and attribution coverage.
5. Input validation: NaN/Inf, empty attributions, feature-name mismatch, bad
   thresholds, bad top_n, bad executed_action.
6. Audit-record completeness, immutability of the stored contribution vector, UTC
   timestamps, and JSONL compliance logging.

Expected values are derived by hand from the arithmetic in the docstrings, not by
re-running the implementation's own expressions.
"""
import dataclasses
import json
import logging
import math
import os
import shutil
import tempfile
import unittest

from signal_explainer import (
    LiveSignalExplainer,
    SignalExplainerError,
    log_explainable_signal,
)


class TestAdditivityGate(unittest.TestCase):
    """The reconciliation check is the reason this skill exists."""

    def setUp(self):
        self.explainer = LiveSignalExplainer(base_value=0.10)
        self.features = {"rsi": 75.0, "volatility_zscore": 0.5, "sentiment": -0.2}
        # 0.10 + 0.35 + 0.20 - 0.05 = 0.60
        self.contribs = {"rsi": 0.35, "volatility_zscore": 0.20, "sentiment": -0.05}

    def test_consistent_attributions_reconcile(self):
        exp = self.explainer.explain_signal(
            "AAPL", self.features, self.contribs, model_prediction=0.60,
            timestamp=1700000000.0,
        )
        self.assertTrue(exp.reconciled)
        self.assertAlmostEqual(exp.reconstructed_score, 0.60, places=10)
        self.assertAlmostEqual(exp.prediction_score, 0.60, places=10)
        self.assertAlmostEqual(exp.reconciliation_error, 0.0, places=10)
        self.assertNotIn("UNRECONCILED", exp.natural_language_summary)

    def test_broken_explainer_fails_the_gate(self):
        """
        Regression test for the core defect. The model emitted 0.20; the attribution
        vector sums (with the base value) to 0.60. The old implementation *defined*
        the score as 0.60 and reported a confident BUY explanation for a signal the
        model never produced. The gate must now catch the 0.40 discrepancy, and the
        action must follow the real output (0.20 -> HOLD), not the reconstruction.
        """
        exp = self.explainer.explain_signal(
            "AAPL", self.features, self.contribs, model_prediction=0.20,
            timestamp=1700000000.0,
        )
        self.assertFalse(exp.reconciled)
        self.assertAlmostEqual(exp.reconciliation_error, 0.40, places=10)
        self.assertEqual(exp.signal_action, "HOLD")
        self.assertEqual(exp.prediction_score, 0.20)
        self.assertAlmostEqual(exp.reconstructed_score, 0.60, places=10)
        self.assertTrue(exp.natural_language_summary.startswith("UNRECONCILED"))
        self.assertIn("DO NOT rely on the drivers", exp.natural_language_summary)

    def test_wrong_output_space_is_caught(self):
        """
        The classic production trap: TreeSHAP on an XGBoost binary:logistic model
        returns log-odds, but the caller compares against predict_proba. Base value
        0.0 + log-odds contributions summing to 2.0 cannot equal a probability of
        sigmoid(2.0) = 0.8808.
        """
        explainer = LiveSignalExplainer(base_value=0.0, buy_threshold=0.5, sell_threshold=-0.5)
        probability = 1.0 / (1.0 + math.exp(-2.0))
        exp = explainer.explain_signal(
            "MSFT", {"f1": 1.0, "f2": 1.0}, {"f1": 1.2, "f2": 0.8},
            model_prediction=probability, timestamp=1700000000.0,
        )
        self.assertFalse(exp.reconciled)
        self.assertAlmostEqual(exp.reconstructed_score, 2.0, places=10)

    def test_tolerance_is_absolute_plus_relative(self):
        """tol = abs_tol + rel_tol * |prediction| = 1e-3 + 1e-2 * 2.0 = 0.021."""
        explainer = LiveSignalExplainer(
            base_value=0.0, buy_threshold=10.0, sell_threshold=-10.0,
            reconciliation_abs_tol=1e-3, reconciliation_rel_tol=1e-2,
        )
        inside = explainer.explain_signal(
            "X", {"a": 1.0}, {"a": 2.02}, model_prediction=2.0, timestamp=1700000000.0)
        self.assertAlmostEqual(inside.reconciliation_tolerance, 0.021, places=12)
        self.assertTrue(inside.reconciled)

        outside = explainer.explain_signal(
            "X", {"a": 1.0}, {"a": 2.03}, model_prediction=2.0, timestamp=1700000000.0)
        self.assertFalse(outside.reconciled)


class TestActionClassification(unittest.TestCase):

    def setUp(self):
        self.explainer = LiveSignalExplainer(
            base_value=0.0, buy_threshold=0.50, sell_threshold=-0.50)

    def test_threshold_boundaries_are_inclusive(self):
        self.assertEqual(self.explainer.classify_action(0.50), "BUY")
        self.assertEqual(self.explainer.classify_action(-0.50), "SELL")
        self.assertEqual(self.explainer.classify_action(0.4999999), "HOLD")
        self.assertEqual(self.explainer.classify_action(-0.4999999), "HOLD")
        self.assertEqual(self.explainer.classify_action(0.0), "HOLD")

    def test_action_follows_model_output_not_reconstruction(self):
        exp = self.explainer.explain_signal(
            "TSLA", {"a": 1.0}, {"a": 5.0}, model_prediction=-0.90,
            timestamp=1700000000.0,
        )
        self.assertEqual(exp.signal_action, "SELL")
        self.assertFalse(exp.reconciled)

    def test_executed_action_mismatch_is_flagged(self):
        exp = self.explainer.explain_signal(
            "TSLA", {"a": 1.0}, {"a": 0.80}, model_prediction=0.80,
            timestamp=1700000000.0, executed_action="hold",
        )
        self.assertTrue(exp.reconciled)
        self.assertEqual(exp.signal_action, "BUY")
        self.assertEqual(exp.executed_action, "HOLD")
        self.assertTrue(exp.action_mismatch)
        self.assertIn("ACTION MISMATCH", exp.natural_language_summary)

    def test_executed_action_agreement_is_not_flagged(self):
        exp = self.explainer.explain_signal(
            "TSLA", {"a": 1.0}, {"a": 0.80}, model_prediction=0.80,
            timestamp=1700000000.0, executed_action="BUY",
        )
        self.assertFalse(exp.action_mismatch)
        self.assertNotIn("ACTION MISMATCH", exp.natural_language_summary)


class TestDriverRankingAndNarrative(unittest.TestCase):

    def setUp(self):
        self.explainer = LiveSignalExplainer(base_value=0.10)

    def test_buy_signal_drivers_and_summary(self):
        features = {"rsi": 75.0, "volatility_zscore": 0.5, "sentiment": -0.2}
        contribs = {"rsi": 0.35, "volatility_zscore": 0.20, "sentiment": -0.05}
        exp = self.explainer.explain_signal(
            "aapl", features, contribs, model_prediction=0.60, timestamp=1700000000.0)

        self.assertEqual(exp.symbol, "AAPL")
        self.assertEqual(exp.signal_action, "BUY")
        self.assertEqual([f.feature_name for f in exp.top_bullish_drivers],
                         ["rsi", "volatility_zscore"])
        self.assertEqual([f.feature_name for f in exp.top_bearish_drivers], ["sentiment"])
        self.assertEqual(exp.top_bullish_drivers[0].direction, "BULLISH")
        self.assertEqual(exp.top_bearish_drivers[0].direction, "BEARISH")
        self.assertEqual(exp.top_bullish_drivers[0].feature_value, 75.0)

        summary = exp.natural_language_summary
        self.assertIn("BUY signal for 'AAPL'", summary)
        self.assertIn("driven by rsi (+0.3500), volatility_zscore (+0.2000)", summary)
        self.assertIn("offset by sentiment (-0.0500)", summary)

    def test_sell_signal_names_the_bearish_features_as_drivers(self):
        """
        Regression test. With a single negative contribution the old template produced
        "SELL signal (-0.50) ... offset by macd (-0.60)" - describing the sole cause of
        the SELL as an offsetting factor, i.e. exactly backwards for anyone reading the
        log during an incident.
        """
        exp = self.explainer.explain_signal(
            "NVDA", {"macd": -0.05}, {"macd": -0.60},
            model_prediction=-0.50, timestamp=1700000000.0,
        )
        self.assertEqual(exp.signal_action, "SELL")
        summary = exp.natural_language_summary
        self.assertIn("driven by macd (-0.6000)", summary)
        self.assertNotIn("offset by macd", summary)

    def test_sell_signal_labels_positive_contributions_as_offsets(self):
        exp = self.explainer.explain_signal(
            "NVDA", {"macd": -0.05, "rsi": 20.0}, {"macd": -0.80, "rsi": 0.10},
            model_prediction=-0.60, timestamp=1700000000.0,
        )
        self.assertEqual(exp.signal_action, "SELL")
        self.assertIn("driven by macd (-0.8000)", exp.natural_language_summary)
        self.assertIn("offset by rsi (+0.1000)", exp.natural_language_summary)

    def test_hold_signal_uses_neutral_language(self):
        exp = self.explainer.explain_signal(
            "SPY", {"a": 1.0, "b": 1.0}, {"a": 0.30, "b": -0.30},
            model_prediction=0.10, timestamp=1700000000.0,
        )
        self.assertEqual(exp.signal_action, "HOLD")
        self.assertIn("largest positive: a (+0.3000)", exp.natural_language_summary)
        self.assertIn("largest negative: b (-0.3000)", exp.natural_language_summary)
        self.assertNotIn("driven by", exp.natural_language_summary)

    def test_ties_break_deterministically_by_name(self):
        contribs = {"zeta": 0.20, "alpha": 0.20, "mid": 0.20}
        features = {k: 1.0 for k in contribs}
        exp = self.explainer.explain_signal(
            "X", features, contribs, model_prediction=0.70, timestamp=1700000000.0)
        self.assertEqual([f.feature_name for f in exp.top_bullish_drivers],
                         ["alpha", "mid", "zeta"])

    def test_top_n_truncation_reports_residual_and_coverage(self):
        """
        Six equal +0.10 bullish contributions, top_n=2. Listed magnitude 0.20 of a
        total 0.60 -> coverage exactly 1/3. Residual = 0.60 - 0.20 = 0.40.
        """
        explainer = LiveSignalExplainer(base_value=0.0, top_n_drivers=2)
        contribs = {f"f{i}": 0.10 for i in range(6)}
        features = {k: 1.0 for k in contribs}
        exp = explainer.explain_signal(
            "X", features, contribs, model_prediction=0.60, timestamp=1700000000.0)

        self.assertTrue(exp.reconciled)
        self.assertEqual(len(exp.top_bullish_drivers), 2)
        self.assertAlmostEqual(exp.attribution_coverage, 1.0 / 3.0, places=12)
        self.assertAlmostEqual(exp.residual_contribution, 0.40, places=12)
        self.assertIn("Listed drivers cover 33.3%", exp.natural_language_summary)
        self.assertIn("across 6 attributed feature(s)", exp.natural_language_summary)

    def test_all_zero_contributions_yield_full_coverage_and_no_drivers(self):
        explainer = LiveSignalExplainer(base_value=0.25)
        exp = explainer.explain_signal(
            "X", {"a": 1.0, "b": 2.0}, {"a": 0.0, "b": 0.0},
            model_prediction=0.25, timestamp=1700000000.0,
        )
        self.assertTrue(exp.reconciled)
        self.assertEqual(exp.top_bullish_drivers, [])
        self.assertEqual(exp.top_bearish_drivers, [])
        self.assertEqual(exp.attribution_coverage, 1.0)
        self.assertIn("no material feature contributions", exp.natural_language_summary)

    def test_materiality_threshold_excludes_small_contributions(self):
        explainer = LiveSignalExplainer(base_value=0.0, materiality_threshold=0.01)
        contribs = {"big": 0.60, "noise": 0.005, "neg_noise": -0.005}
        features = {k: 1.0 for k in contribs}
        exp = explainer.explain_signal(
            "X", features, contribs, model_prediction=0.60, timestamp=1700000000.0)
        self.assertEqual([f.feature_name for f in exp.top_bullish_drivers], ["big"])
        self.assertEqual(exp.top_bearish_drivers, [])
        # Immaterial contributions are excluded from the drivers but never from the
        # record or the reconciliation.
        self.assertIn("noise", exp.all_contributions)
        self.assertTrue(exp.reconciled)

    def test_default_materiality_keeps_tiny_contributions(self):
        """
        The previous implementation hard-coded a +/-0.001 cut-off, which silently
        dropped every driver of a model scored in basis points. The default is now 0.0.
        """
        explainer = LiveSignalExplainer(base_value=0.0, buy_threshold=1.0, sell_threshold=-1.0)
        exp = explainer.explain_signal(
            "X", {"a": 1.0, "b": 1.0}, {"a": 0.0002, "b": -0.0001},
            model_prediction=0.0001, timestamp=1700000000.0,
        )
        self.assertEqual([f.feature_name for f in exp.top_bullish_drivers], ["a"])
        self.assertEqual([f.feature_name for f in exp.top_bearish_drivers], ["b"])


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.explainer = LiveSignalExplainer(base_value=0.0)

    def test_nan_contribution_raises_instead_of_silently_holding(self):
        """
        Previously a NaN contribution made the score NaN; both threshold comparisons
        returned False, so the record was written as a valid HOLD.
        """
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": 1.0}, {"a": float("nan")}, model_prediction=0.0)

    def test_inf_contribution_raises(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": 1.0}, {"a": float("inf")}, model_prediction=0.0)

    def test_nan_feature_value_raises(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": float("nan")}, {"a": 0.5}, model_prediction=0.5)

    def test_nan_model_prediction_raises(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": 1.0}, {"a": 0.5}, model_prediction=float("nan"))

    def test_empty_contributions_raise(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal("X", {"a": 1.0}, {}, model_prediction=0.0)

    def test_contribution_for_unknown_feature_raises(self):
        """A name mismatch must not be papered over with a fabricated 0.0 value."""
        with self.assertRaises(SignalExplainerError) as ctx:
            self.explainer.explain_signal(
                "X", {"rsi_14": 70.0}, {"rsi": 0.5}, model_prediction=0.5)
        self.assertIn("rsi", str(ctx.exception))

    def test_unattributed_feature_is_recorded_not_rejected(self):
        exp = self.explainer.explain_signal(
            "X", {"a": 1.0, "unused": 9.0}, {"a": 0.5},
            model_prediction=0.5, timestamp=1700000000.0,
        )
        self.assertEqual(exp.unattributed_features, ["unused"])
        self.assertTrue(exp.reconciled)

    def test_non_string_contribution_key_raises(self):
        """Non-string keys break deterministic sorting and do not round-trip via JSON."""
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {1: 1.0, "a": 1.0}, {1: 0.5, "a": 0.5}, model_prediction=1.0)

    def test_millisecond_timestamp_raises_a_typed_error(self):
        """A millisecond epoch where POSIX seconds are expected must not escape as OSError."""
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": 1.0}, {"a": 0.5}, model_prediction=0.5, timestamp=1.7e12)

    def test_empty_symbol_raises(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal("   ", {"a": 1.0}, {"a": 0.5}, model_prediction=0.5)

    def test_invalid_executed_action_raises(self):
        with self.assertRaises(SignalExplainerError):
            self.explainer.explain_signal(
                "X", {"a": 1.0}, {"a": 0.5}, model_prediction=0.5, executed_action="LONG")

    def test_inverted_thresholds_raise(self):
        """sell >= buy makes the BUY branch shadow SELL: everything becomes BUY."""
        with self.assertRaises(SignalExplainerError):
            LiveSignalExplainer(buy_threshold=-1.0, sell_threshold=1.0)
        with self.assertRaises(SignalExplainerError):
            LiveSignalExplainer(buy_threshold=0.5, sell_threshold=0.5)

    def test_invalid_top_n_raises(self):
        for bad in (0, -1, 2.5, True):
            with self.assertRaises(SignalExplainerError):
                LiveSignalExplainer(top_n_drivers=bad)

    def test_negative_materiality_threshold_raises(self):
        with self.assertRaises(SignalExplainerError):
            LiveSignalExplainer(materiality_threshold=-0.01)

    def test_non_finite_base_value_raises(self):
        with self.assertRaises(SignalExplainerError):
            LiveSignalExplainer(base_value=float("nan"))


class TestAuditRecord(unittest.TestCase):

    def setUp(self):
        self.explainer = LiveSignalExplainer(base_value=0.10)
        self.features = {"rsi": 75.0, "volatility_zscore": 0.5, "sentiment": -0.2}
        self.contribs = {"rsi": 0.35, "volatility_zscore": 0.20, "sentiment": -0.05}
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _explain(self, **kwargs):
        params = dict(
            symbol="AAPL", feature_dict=self.features, contributions_dict=self.contribs,
            model_prediction=0.60, timestamp=1700000000.0,
        )
        params.update(kwargs)
        return self.explainer.explain_signal(**params)

    def test_audit_record_carries_the_full_attribution_vector(self):
        """
        The old to_json_audit() emitted only the top-N drivers, so a reviewer could not
        re-derive base + sum(phi) from the record - which is precisely the check the
        skill's own checklist demands.
        """
        parsed = json.loads(self._explain().to_json_audit())
        self.assertEqual(parsed["all_contributions"], self.contribs)
        self.assertEqual(parsed["base_value"], 0.10)
        self.assertTrue(parsed["reconciled"])
        self.assertIn("reconciliation_error", parsed)
        self.assertIn("reconciliation_tolerance", parsed)
        self.assertIn("residual_contribution", parsed)
        self.assertIn("attribution_coverage", parsed)
        recomputed = parsed["base_value"] + sum(parsed["all_contributions"].values())
        self.assertAlmostEqual(recomputed, parsed["score"], places=10)

    def test_timestamp_is_utc(self):
        exp = self._explain()
        self.assertEqual(exp.timestamp, 1700000000.0)
        self.assertEqual(exp.timestamp_utc, "2023-11-14T22:13:20Z")

    def test_default_timestamp_is_timezone_aware_utc(self):
        exp = self.explainer.explain_signal(
            "AAPL", self.features, self.contribs, model_prediction=0.60)
        self.assertTrue(exp.timestamp_utc.endswith("Z"))

    def test_stored_contributions_are_a_copy(self):
        """A written compliance record must not change when the caller mutates its dict."""
        mutable = dict(self.contribs)
        exp = self._explain(contributions_dict=mutable)
        mutable["rsi"] = 99.0
        self.assertEqual(exp.all_contributions["rsi"], 0.35)

    def test_json_audit_single_line_for_jsonl(self):
        line = self._explain().to_json_audit(indent=None)
        self.assertNotIn("\n", line)

    def test_log_explainable_signal_appends_jsonl(self):
        path = os.path.join(self.tmpdir, "nested", "audit.jsonl")
        first = self._explain()
        second = self._explain(symbol="MSFT")
        log_explainable_signal(first, path)
        log_explainable_signal(second, path)

        with open(path, encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["symbol"], "AAPL")
        self.assertEqual(json.loads(lines[1])["symbol"], "MSFT")

    def test_unreconciled_explanations_are_still_logged(self):
        path = os.path.join(self.tmpdir, "audit.jsonl")
        broken = self._explain(model_prediction=0.20)
        self.assertFalse(broken.reconciled)
        log_explainable_signal(broken, path)
        with open(path, encoding="utf-8") as handle:
            record = json.loads(handle.readline())
        self.assertFalse(record["reconciled"])
        self.assertAlmostEqual(record["reconciliation_error"], 0.40, places=10)

    def test_log_rejects_non_explanation(self):
        with self.assertRaises(SignalExplainerError):
            log_explainable_signal({"not": "an explanation"},
                                   os.path.join(self.tmpdir, "a.jsonl"))

    def test_log_rejects_empty_path(self):
        with self.assertRaises(SignalExplainerError):
            log_explainable_signal(self._explain(), "")

    def test_feature_contribution_is_immutable(self):
        exp = self._explain()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            exp.top_bullish_drivers[0].contribution = 1.0

    def test_direction_label_always_matches_contribution_sign(self):
        exp = self._explain()
        for driver in exp.top_bullish_drivers:
            self.assertEqual(driver.direction, "BULLISH")
            self.assertGreater(driver.contribution, 0.0)
        for driver in exp.top_bearish_drivers:
            self.assertEqual(driver.direction, "BEARISH")
            self.assertLess(driver.contribution, 0.0)

    def test_reconciliation_failure_is_logged_at_error(self):
        """The alarm must actually sound, not just set a flag on the return value."""
        with self.assertLogs("signal_explainer", level="ERROR") as captured:
            self._explain(model_prediction=0.20)
        self.assertTrue(
            any("additivity gate FAILED" in line for line in captured.output),
            captured.output,
        )

    def test_reconciled_signal_does_not_log_an_error(self):
        logger = logging.getLogger("signal_explainer")
        with self.assertLogs(logger, level="INFO") as captured:
            self._explain()
        self.assertFalse([line for line in captured.output if line.startswith("ERROR")])


if __name__ == "__main__":
    unittest.main()
