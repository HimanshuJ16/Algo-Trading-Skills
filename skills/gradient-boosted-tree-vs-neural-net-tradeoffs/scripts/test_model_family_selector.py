"""Tests for the GBDT-vs-neural-net model family selector.

Expected scores in this file are derived by hand with exact rational
arithmetic, not by re-running the engine's own expression. Worked example for
the tabular scenario (weights before normalisation: tabular 0.40 binding,
sequential 0.05 residual, compliance 0.25 binding, latency 0.25 binding,
regime 0.15; total 1.10):

    GBDT = (9.5*0.40 + 3.0*0.05 + 9.0*0.25 + 9.0*0.25 + 6.0*0.15) / 1.10
         = 9.35 / 1.10 = 8.50
    NN   = (5.5*0.40 + 9.5*0.05 + 4.0*0.25 + 5.0*0.25 + 6.0*0.15) / 1.10
         = 5.825 / 1.10 = 5.295... -> 5.30
"""
import logging
import unittest

from model_family_selector import (
    DATA_BELOW_DEEP_LEARNING_REFERENCE,
    DATA_SUFFICIENT_FOR_DEEP_LEARNING,
    DEFAULT_DIMENSION_PRIORS,
    DatasetSpec,
    DimensionPrior,
    ModelFamilySelectorEngine,
    ModelFamilySelectorError,
    RECOMMEND_GBDT,
    RECOMMEND_HYBRID,
    RECOMMEND_NEURAL_NET,
)


def setUpModule() -> None:
    # The engine deliberately warns on deprecated inputs and gated branches;
    # several tests exercise those paths on purpose.
    logging.getLogger("model_family_selector").setLevel(logging.CRITICAL)


def tabular_spec(**overrides) -> DatasetSpec:
    kwargs = dict(
        modality="TABULAR_ENGINEERED",
        sample_size_rows=100_000,
        feature_count=50,
        latency_budget_us=200.0,
        regulatory_compliance="STRICT_MODEL_GOVERNANCE",
    )
    kwargs.update(overrides)
    return DatasetSpec(**kwargs)


def tick_spec(**overrides) -> DatasetSpec:
    kwargs = dict(
        modality="RAW_HIGH_FREQUENCY_TICKS",
        sample_size_rows=5_000_000,
        feature_count=10,
        latency_budget_us=20_000.0,
        regulatory_compliance="INTERNAL_RESEARCH",
    )
    kwargs.update(overrides)
    return DatasetSpec(**kwargs)


class TestScoringAgainstHandDerivedValues(unittest.TestCase):
    """Scores must match values computed independently of the implementation."""

    def setUp(self) -> None:
        self.engine = ModelFamilySelectorEngine()

    def test_tabular_strict_governance_low_latency_matches_hand_derivation(self):
        report = self.engine.evaluate_model_family_tradeoffs(tabular_spec())

        # 9.35 / 1.10 and 5.825 / 1.10, derived in the module docstring.
        self.assertAlmostEqual(report.gbdt_overall_score_0_to_10, 8.50, places=2)
        self.assertAlmostEqual(report.neural_net_overall_score_0_to_10, 5.30, places=2)
        self.assertEqual(report.recommended_model_family, RECOMMEND_GBDT)

    def test_raw_tick_sequence_at_scale_matches_hand_derivation(self):
        # Weights: tabular .05, sequential .50, compliance .05, latency .05,
        # regime .15; total 0.80.
        #   GBDT = (9.5*.05 + 3.0*.50 + 9.0*.05 + 9.0*.05 + 6.0*.15)/0.80 = 4.72
        #   NN   = (5.5*.05 + 9.5*.50 + 4.0*.05 + 5.0*.05 + 6.0*.15)/0.80 = 7.97
        report = self.engine.evaluate_model_family_tradeoffs(tick_spec())

        self.assertAlmostEqual(report.gbdt_overall_score_0_to_10, 4.72, places=2)
        self.assertAlmostEqual(report.neural_net_overall_score_0_to_10, 7.97, places=2)
        self.assertEqual(report.recommended_model_family, RECOMMEND_NEURAL_NET)

    def test_conflicting_constraints_land_inside_the_decision_margin(self):
        # Tick data that also has a strict governance posture and a 200us
        # budget: weights .05/.50/.25/.25/.15, total 1.20.
        #   GBDT = 7.38/1.20 = 6.15 ; NN = 8.1725/1.20 = 6.81 ; gap -0.66
        report = self.engine.evaluate_model_family_tradeoffs(
            tick_spec(latency_budget_us=200.0,
                      regulatory_compliance="STRICT_MODEL_GOVERNANCE")
        )

        self.assertAlmostEqual(report.gbdt_overall_score_0_to_10, 6.15, places=2)
        self.assertAlmostEqual(report.neural_net_overall_score_0_to_10, 6.81, places=2)
        self.assertEqual(report.recommended_model_family, RECOMMEND_HYBRID)
        self.assertTrue(
            any("decision margin" in lim for lim in report.stated_limitations),
            "a hybrid result must say why it is hybrid",
        )


class TestReportIsAuditReconstructable(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = ModelFamilySelectorEngine()

    def test_scores_recompute_from_the_record_alone(self):
        report = self.engine.evaluate_model_family_tradeoffs(tabular_spec())

        gbdt = sum(
            report.dimension_scores[name]["gbdt"] * weight
            for name, weight in report.applied_dimension_weights.items()
        )
        nn = sum(
            report.dimension_scores[name]["nn"] * weight
            for name, weight in report.applied_dimension_weights.items()
        )
        # Reconstruction is exact to the two decimal places the record publishes.
        self.assertAlmostEqual(gbdt, report.gbdt_overall_score_0_to_10, places=2)
        self.assertAlmostEqual(nn, report.neural_net_overall_score_0_to_10, places=2)

    def test_the_recommendation_follows_from_the_published_gap_and_margin(self):
        for spec in (tabular_spec(), tick_spec(), tick_spec(sample_size_rows=5_000)):
            report = self.engine.evaluate_model_family_tradeoffs(spec)
            if report.score_gap >= report.decision_margin:
                expected = RECOMMEND_GBDT
            elif report.score_gap <= -report.decision_margin:
                expected = RECOMMEND_NEURAL_NET
            else:
                expected = RECOMMEND_HYBRID
            self.assertEqual(report.recommended_model_family, expected)

    def test_applied_weights_are_normalised(self):
        report = self.engine.evaluate_model_family_tradeoffs(tick_spec())
        self.assertAlmostEqual(sum(report.applied_dimension_weights.values()), 1.0, places=5)

    def test_every_report_states_that_it_is_a_prior_not_a_benchmark(self):
        for spec in (tabular_spec(), tick_spec()):
            report = self.engine.evaluate_model_family_tradeoffs(spec)
            joined = " ".join(report.stated_limitations)
            self.assertIn("not a benchmark result", joined)
            self.assertIn("does not certify", joined)
            self.assertIn("material change", joined)

    def test_evidence_is_recorded_for_every_scored_dimension(self):
        report = self.engine.evaluate_model_family_tradeoffs(tabular_spec())
        self.assertEqual(set(report.dimension_evidence), set(DEFAULT_DIMENSION_PRIORS))
        for name, evidence in report.dimension_evidence.items():
            self.assertTrue(evidence.strip(), f"{name} has no recorded evidence")

    def test_mutating_a_returned_report_cannot_corrupt_the_next_one(self):
        first = self.engine.evaluate_model_family_tradeoffs(tabular_spec())
        first.dimension_scores["tabular_data_fit"]["gbdt"] = 0.0

        second = self.engine.evaluate_model_family_tradeoffs(tabular_spec())
        self.assertEqual(second.dimension_scores["tabular_data_fit"]["gbdt"], 9.5)
        self.assertEqual(DEFAULT_DIMENSION_PRIORS["tabular_data_fit"].gbdt, 9.5)

    def test_repeated_evaluation_is_deterministic(self):
        a = self.engine.evaluate_model_family_tradeoffs(tabular_spec())
        b = self.engine.evaluate_model_family_tradeoffs(tabular_spec())
        self.assertEqual(a.recommended_model_family, b.recommended_model_family)
        self.assertEqual(a.gbdt_overall_score_0_to_10, b.gbdt_overall_score_0_to_10)
        self.assertEqual(a.neural_net_overall_score_0_to_10, b.neural_net_overall_score_0_to_10)
        self.assertEqual(a.config_fingerprint, b.config_fingerprint)

    def test_fingerprint_changes_when_the_priors_change(self):
        altered = dict(DEFAULT_DIMENSION_PRIORS)
        altered["tabular_data_fit"] = DimensionPrior(gbdt=7.0, nn=7.0, evidence="test override")
        other = ModelFamilySelectorEngine(dimension_priors=altered)
        self.assertNotEqual(self.engine.config_fingerprint, other.config_fingerprint)


class TestRegimeShiftDimensionIsDecisionNeutral(unittest.TestCase):
    """The published evidence shows no consistent OOD winner, so this dimension
    must not be able to tilt a recommendation."""

    def test_changing_the_neutral_regime_prior_does_not_move_the_score_gap(self):
        baseline = ModelFamilySelectorEngine()
        shifted_priors = dict(DEFAULT_DIMENSION_PRIORS)
        shifted_priors["regime_shift_robustness"] = DimensionPrior(
            gbdt=1.0, nn=1.0, evidence="still neutral, different magnitude"
        )
        shifted = ModelFamilySelectorEngine(dimension_priors=shifted_priors)

        for spec_factory in (tabular_spec, tick_spec):
            base = baseline.evaluate_model_family_tradeoffs(spec_factory())
            alt = shifted.evaluate_model_family_tradeoffs(spec_factory())
            # score_gap is taken from the unrounded scores precisely so that an
            # equally-scored dimension cannot move it. Subtracting the two
            # published (2 dp) scores instead would leave 0.01 of slack.
            self.assertEqual(base.score_gap, alt.score_gap)
            self.assertEqual(base.recommended_model_family, alt.recommended_model_family)


class TestSampleSizeGatesTheDeepLearningBranch(unittest.TestCase):
    """Regression: sample_size_rows was collected but never used."""

    def setUp(self) -> None:
        self.engine = ModelFamilySelectorEngine()

    def test_tick_data_below_the_reference_row_count_loses_the_sequential_weight(self):
        # 5,000 rows < 10,000 reference -> sequential demoted to residual .05,
        # weights .05/.05/.05/.05/.15, total 0.35:
        #   GBDT = 2.425/0.35 = 6.93 ; NN = 2.10/0.35 = 6.00 ; gap 0.93 < 1.0
        report = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=5_000))

        self.assertAlmostEqual(report.gbdt_overall_score_0_to_10, 6.93, places=2)
        self.assertAlmostEqual(report.neural_net_overall_score_0_to_10, 6.00, places=2)
        self.assertEqual(report.data_sufficiency, DATA_BELOW_DEEP_LEARNING_REFERENCE)
        self.assertAlmostEqual(
            report.applied_dimension_weights["sequential_pattern_extraction"],
            report.applied_dimension_weights["tabular_data_fit"],
            places=6,
            msg="a starved sequential dimension must carry only residual weight",
        )
        self.assertEqual(report.recommended_model_family, RECOMMEND_HYBRID)

    def test_the_same_spec_at_scale_recommends_the_neural_network(self):
        starved = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=5_000))
        at_scale = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=5_000_000))

        self.assertEqual(starved.recommended_model_family, RECOMMEND_HYBRID)
        self.assertEqual(at_scale.recommended_model_family, RECOMMEND_NEURAL_NET)
        self.assertEqual(at_scale.data_sufficiency, DATA_SUFFICIENT_FOR_DEEP_LEARNING)

    def test_exactly_the_reference_row_count_is_sufficient(self):
        at_boundary = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=10_000))
        just_below = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=9_999))

        self.assertEqual(at_boundary.data_sufficiency, DATA_SUFFICIENT_FOR_DEEP_LEARNING)
        self.assertEqual(just_below.data_sufficiency, DATA_BELOW_DEEP_LEARNING_REFERENCE)

    def test_the_reference_row_count_is_caller_configurable(self):
        lenient = ModelFamilySelectorEngine(deep_learning_reference_rows=1_000)
        report = lenient.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=5_000))
        self.assertEqual(report.data_sufficiency, DATA_SUFFICIENT_FOR_DEEP_LEARNING)
        self.assertEqual(report.recommended_model_family, RECOMMEND_NEURAL_NET)

    def test_gating_a_starved_dataset_records_a_limitation(self):
        report = self.engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=500))
        self.assertTrue(any("reference point" in lim for lim in report.stated_limitations))

    def test_feature_count_is_recorded_but_does_not_change_the_score(self):
        few = self.engine.evaluate_model_family_tradeoffs(tabular_spec(feature_count=3))
        many = self.engine.evaluate_model_family_tradeoffs(tabular_spec(feature_count=3_000))
        self.assertEqual(few.gbdt_overall_score_0_to_10, many.gbdt_overall_score_0_to_10)
        self.assertEqual(few.neural_net_overall_score_0_to_10, many.neural_net_overall_score_0_to_10)
        self.assertIn("3 features", few.audit_notes)


class TestBoundaryBehaviour(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = ModelFamilySelectorEngine()

    def test_latency_exactly_at_the_threshold_binds(self):
        binding = self.engine.evaluate_model_family_tradeoffs(
            tabular_spec(latency_budget_us=500.0))
        not_binding = self.engine.evaluate_model_family_tradeoffs(
            tabular_spec(latency_budget_us=500.01))

        self.assertGreater(
            binding.applied_dimension_weights["inference_speed_latency"],
            not_binding.applied_dimension_weights["inference_speed_latency"],
        )
        self.assertTrue(any("binding threshold" in f for f in binding.primary_decision_factors))
        self.assertFalse(any("binding threshold" in f for f in not_binding.primary_decision_factors))

    def test_decision_margin_boundary_is_inclusive(self):
        # The starved-tick spec has a hand-derived gap of exactly 0.93.
        spec_kwargs = dict(sample_size_rows=5_000)
        at_gap = ModelFamilySelectorEngine(decision_margin=0.93)
        just_above = ModelFamilySelectorEngine(decision_margin=0.94)

        self.assertEqual(
            at_gap.evaluate_model_family_tradeoffs(tick_spec(**spec_kwargs)).recommended_model_family,
            RECOMMEND_GBDT,
        )
        self.assertEqual(
            just_above.evaluate_model_family_tradeoffs(tick_spec(**spec_kwargs)).recommended_model_family,
            RECOMMEND_HYBRID,
        )

    def test_zero_margin_never_returns_hybrid_for_unequal_scores(self):
        engine = ModelFamilySelectorEngine(decision_margin=0.0)
        report = engine.evaluate_model_family_tradeoffs(tick_spec(sample_size_rows=5_000))
        self.assertEqual(report.recommended_model_family, RECOMMEND_GBDT)


class TestDeprecatedComplianceAlias(unittest.TestCase):

    def test_legacy_sr11_7_value_is_canonicalised(self):
        spec = tabular_spec(regulatory_compliance="STRICT_SR11_7_MIFID2")
        self.assertEqual(spec.regulatory_compliance, "STRICT_MODEL_GOVERNANCE")

    def test_legacy_value_scores_identically_to_the_canonical_one(self):
        engine = ModelFamilySelectorEngine()
        legacy = engine.evaluate_model_family_tradeoffs(
            tabular_spec(regulatory_compliance="STRICT_SR11_7_MIFID2"))
        canonical = engine.evaluate_model_family_tradeoffs(tabular_spec())

        self.assertEqual(legacy.recommended_model_family, canonical.recommended_model_family)
        self.assertEqual(legacy.gbdt_overall_score_0_to_10, canonical.gbdt_overall_score_0_to_10)

    def test_no_report_asserts_a_shap_mandate(self):
        engine = ModelFamilySelectorEngine()
        report = engine.evaluate_model_family_tradeoffs(tabular_spec())
        joined = " ".join(report.primary_decision_factors) + report.audit_notes
        self.assertNotIn("SR 11-7", joined)
        self.assertIn("no regulator mandates SHAP", joined)


class TestInputValidation(unittest.TestCase):
    """Regression: every one of these used to be accepted silently."""

    def setUp(self) -> None:
        self.engine = ModelFamilySelectorEngine()

    def test_unrecognised_modality_raises_instead_of_defaulting(self):
        # Previously 'TABULAR' fell through to the else branch and emitted the
        # audit note "Raw high-frequency tick sequence favours ..." for tabular
        # data - a factually inverted justification in a governance record.
        with self.assertRaises(ModelFamilySelectorError) as ctx:
            tabular_spec(modality="TABULAR")
        self.assertIn("not recognised", str(ctx.exception))

    def test_unrecognised_compliance_level_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(regulatory_compliance="SR11-7")

    def test_non_finite_latency_budget_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ModelFamilySelectorError):
                    tabular_spec(latency_budget_us=bad)

    def test_non_positive_latency_budget_raises(self):
        for bad in (0.0, -1.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ModelFamilySelectorError):
                    tabular_spec(latency_budget_us=bad)

    def test_non_positive_sample_size_or_feature_count_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(sample_size_rows=0)
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(feature_count=0)

    def test_boolean_counts_are_rejected(self):
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(sample_size_rows=True)
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(feature_count=True)

    def test_non_numeric_latency_budget_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            tabular_spec(latency_budget_us="fast")

    def test_spec_mutated_after_construction_is_caught_at_evaluation(self):
        spec = tabular_spec()
        spec.modality = "SOMETHING_ELSE"
        with self.assertRaises(ModelFamilySelectorError):
            self.engine.evaluate_model_family_tradeoffs(spec)

    def test_non_spec_argument_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            self.engine.evaluate_model_family_tradeoffs({"modality": "TABULAR_ENGINEERED"})


class TestEngineConfigurationValidation(unittest.TestCase):

    def test_incomplete_prior_set_raises(self):
        partial = {"tabular_data_fit": DEFAULT_DIMENSION_PRIORS["tabular_data_fit"]}
        with self.assertRaises(ModelFamilySelectorError):
            ModelFamilySelectorEngine(dimension_priors=partial)

    def test_out_of_range_prior_raises(self):
        bad = dict(DEFAULT_DIMENSION_PRIORS)
        bad["tabular_data_fit"] = DimensionPrior(gbdt=11.0, nn=5.0, evidence="out of range")
        with self.assertRaises(ModelFamilySelectorError):
            ModelFamilySelectorEngine(dimension_priors=bad)

    def test_non_finite_prior_raises(self):
        bad = dict(DEFAULT_DIMENSION_PRIORS)
        bad["tabular_data_fit"] = DimensionPrior(gbdt=float("nan"), nn=5.0, evidence="nan")
        with self.assertRaises(ModelFamilySelectorError):
            ModelFamilySelectorEngine(dimension_priors=bad)

    def test_negative_decision_margin_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            ModelFamilySelectorEngine(decision_margin=-1.0)

    def test_invalid_reference_row_count_raises(self):
        with self.assertRaises(ModelFamilySelectorError):
            ModelFamilySelectorEngine(deep_learning_reference_rows=0)


if __name__ == "__main__":
    unittest.main()
