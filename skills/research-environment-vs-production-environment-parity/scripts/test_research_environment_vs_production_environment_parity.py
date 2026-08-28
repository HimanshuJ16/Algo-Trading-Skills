"""Unit tests for the research-vs-production environment parity gate.

The tests marked "regression" fail against the previous implementation and pass against
the current one; they are the reason this module was rewritten. Each names the fail-open
hole it closes.
"""
import math
import unittest

from research_environment_vs_production_environment_parity import (
    COMPONENT_FEATURE,
    COMPONENT_PACKAGE,
    COMPONENT_PRECISION,
    COMPONENT_PYTHON,
    COMPONENT_SIGNAL,
    DEFAULT_NUMERICALLY_CRITICAL_PACKAGES,
    MISSING,
    NOT_INSTALLED,
    SEVERITY_CRITICAL,
    SEVERITY_WARNING,
    STATUS_BREACHED,
    STATUS_VERIFIED,
    EnvironmentParityReport,
    EnvironmentSnapshot,
    ResearchEnvironmentVsProductionEnvironmentParityEngine,
    normalize_float_precision,
    normalize_package_name,
)

PY = "3.11.8"
PKGS = {"numpy": "1.26.4", "pandas": "2.2.1"}
FEATS = {"rsi_14": "sha256:aaa", "macd": "sha256:bbb"}


def research(**overrides) -> EnvironmentSnapshot:
    kwargs = dict(
        env_type="RESEARCH",
        python_version=PY,
        package_versions=dict(PKGS),
        float_precision="float64",
        feature_definitions=dict(FEATS),
    )
    kwargs.update(overrides)
    return EnvironmentSnapshot(**kwargs)


def production(**overrides) -> EnvironmentSnapshot:
    kwargs = dict(
        env_type="PRODUCTION",
        python_version=PY,
        package_versions=dict(PKGS),
        float_precision="float64",
        feature_definitions=dict(FEATS),
    )
    kwargs.update(overrides)
    return EnvironmentSnapshot(**kwargs)


class TestSnapshotValidation(unittest.TestCase):
    """Absent or ambiguous evidence must raise, never audit as a match."""

    def test_empty_package_map_rejected(self):
        # Regression: an audit over empty maps compared nothing and returned
        # PARITY_VERIFIED on zero evidence.
        with self.assertRaises(ValueError):
            research(package_versions={})

    def test_empty_feature_map_rejected(self):
        with self.assertRaises(ValueError):
            production(feature_definitions={})

    def test_blank_fields_rejected(self):
        for field, value in [
            ("env_type", "  "),
            ("python_version", ""),
            ("float_precision", "   "),
        ]:
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    research(**{field: value})

    def test_blank_package_version_rejected(self):
        with self.assertRaises(ValueError):
            research(package_versions={"numpy": ""})

    def test_blank_feature_hash_rejected(self):
        with self.assertRaises(ValueError):
            research(feature_definitions={"rsi_14": "  "})

    def test_non_dict_maps_rejected(self):
        with self.assertRaises(TypeError):
            research(package_versions=["numpy==1.26.4"])

    def test_unknown_env_type_rejected(self):
        with self.assertRaises(ValueError):
            research(env_type="NOTEBOOK")

    def test_env_type_is_upper_cased(self):
        self.assertEqual(research(env_type="research").env_type, "RESEARCH")

    def test_minor_only_python_version_rejected(self):
        # "3.11" on both sides compares equal across 3.11.2 and 3.11.8.
        with self.assertRaises(ValueError):
            research(python_version="3.11")

    def test_prerelease_python_version_accepted(self):
        self.assertEqual(research(python_version="3.13.0rc1").python_version,
                         "3.13.0rc1")

    def test_package_names_normalized_per_pep503(self):
        snap = research(package_versions={"Scikit_Learn": "1.4.2", "NumPy": "1.26.4"})
        self.assertEqual(set(snap.package_versions), {"scikit-learn", "numpy"})

    def test_conflicting_package_names_after_normalization_rejected(self):
        with self.assertRaises(ValueError):
            research(package_versions={"scikit_learn": "1.4.2",
                                       "scikit-learn": "1.5.0"})

    def test_colliding_package_names_with_same_version_accepted(self):
        snap = research(package_versions={"scikit_learn": "1.4.2",
                                          "scikit-learn": "1.4.2"})
        self.assertEqual(snap.package_versions, {"scikit-learn": "1.4.2"})

    def test_python_minor_release_property(self):
        self.assertEqual(research(python_version="3.10.14").python_minor_release,
                         (3, 10))


class TestPrecisionNormalization(unittest.TestCase):
    def test_ieee_synonyms_canonicalize(self):
        for token in ["float64", "FLOAT64", "double", "fp64", "binary64", " Double "]:
            with self.subTest(token=token):
                self.assertEqual(normalize_float_precision(token), "float64")
        for token in ["float32", "single", "fp32", "binary32"]:
            with self.subTest(token=token):
                self.assertEqual(normalize_float_precision(token), "float32")

    def test_ambiguous_precision_rejected(self):
        # Bare "float" is binary64 in Python and binary32 in C. Two environments
        # meaning opposite things must not compare equal.
        for token in ["float", "mixed", "auto", "native"]:
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    normalize_float_precision(token)

    def test_unknown_precision_compared_literally(self):
        self.assertEqual(normalize_float_precision("posit32"), "posit32")

    def test_package_name_normalizer(self):
        self.assertEqual(normalize_package_name("  TA_Lib "), "ta-lib")


class TestArgumentRoles(unittest.TestCase):
    def setUp(self):
        self.engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()

    def test_swapped_arguments_rejected(self):
        # Regression: both arguments share a type, so a swapped call previously
        # produced a plausible report describing the drift backwards.
        with self.assertRaises(ValueError):
            self.engine.audit_environment_parity(production(), research())

    def test_two_research_snapshots_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_environment_parity(research(), research())

    def test_non_snapshot_argument_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_environment_parity(research(), {"env_type": "PRODUCTION"})


class TestStaticVectors(unittest.TestCase):
    def setUp(self):
        self.engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()

    def test_identical_environments_verify(self):
        report = self.engine.audit_environment_parity(research(), production())
        self.assertEqual(report.status, STATUS_VERIFIED)
        self.assertTrue(report.is_parity_achieved)
        self.assertEqual(report.total_discrepancies, 0)
        self.assertEqual(report.critical_discrepancies, 0)
        self.assertEqual(report.warning_discrepancies, 0)

    def test_static_only_audit_records_that_signals_were_not_diffed(self):
        report = self.engine.audit_environment_parity(research(), production())
        self.assertFalse(report.signal_diffing_performed)
        self.assertEqual(report.signal_samples_compared, 0)
        self.assertIn("signal diffing NOT performed", report.audit_notes)

    def test_python_minor_drift_is_critical(self):
        report = self.engine.audit_environment_parity(
            research(python_version="3.10.14"), production(python_version="3.11.8"))
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.critical_discrepancies, 1)
        self.assertEqual(report.discrepancies[0].component, COMPONENT_PYTHON)

    def test_python_patch_drift_is_a_non_blocking_warning(self):
        report = self.engine.audit_environment_parity(
            research(python_version="3.11.2"), production(python_version="3.11.8"))
        self.assertEqual(report.status, STATUS_VERIFIED)
        self.assertTrue(report.is_parity_achieved)
        self.assertEqual(report.warning_discrepancies, 1)
        self.assertEqual(report.discrepancies[0].severity, SEVERITY_WARNING)

    def test_numpy_minor_drift_blocks(self):
        # Regression: numeric-package drift was a WARNING, so numpy 1.26 in research
        # against 1.24 in production certified as PARITY_VERIFIED.
        report = self.engine.audit_environment_parity(
            research(), production(package_versions={"numpy": "1.24.3",
                                                     "pandas": "2.2.1"}))
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.critical_discrepancies, 1)
        found = report.discrepancies[0]
        self.assertEqual(found.component, COMPONENT_PACKAGE)
        self.assertEqual(found.item_name, "numpy")
        self.assertEqual(found.severity, SEVERITY_CRITICAL)

    def test_major_version_drift_blocks_even_for_unlisted_package(self):
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            numerically_critical_packages=[])
        report = engine.audit_environment_parity(
            research(package_versions={"numpy": "1.26.4", "somelib": "1.0.0"}),
            production(package_versions={"numpy": "1.26.4", "somelib": "2.0.0"}))
        self.assertEqual(report.critical_discrepancies, 1)
        self.assertIn("Major-version", report.discrepancies[0].details)

    def test_unlisted_package_minor_drift_is_a_warning(self):
        report = self.engine.audit_environment_parity(
            research(package_versions=dict(PKGS, black="24.1.0")),
            production(package_versions=dict(PKGS, black="24.3.0")))
        self.assertEqual(report.status, STATUS_VERIFIED)
        self.assertEqual(report.warning_discrepancies, 1)

    def test_one_sided_install_is_critical(self):
        report = self.engine.audit_environment_parity(
            research(package_versions=dict(PKGS, talib="0.4.28")), production())
        self.assertEqual(report.status, STATUS_BREACHED)
        found = [d for d in report.discrepancies if d.item_name == "talib"][0]
        self.assertEqual(found.production_val, NOT_INSTALLED)
        self.assertEqual(found.severity, SEVERITY_CRITICAL)

    def test_unparseable_version_is_critical(self):
        report = self.engine.audit_environment_parity(
            research(package_versions=dict(PKGS, ourlib="git-3fa91cd")),
            production(package_versions=dict(PKGS, ourlib="git-77b0e12")))
        self.assertEqual(report.critical_discrepancies, 1)

    def test_custom_numerically_critical_set_is_honoured(self):
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            numerically_critical_packages=["Our_Indicators"])
        report = engine.audit_environment_parity(
            research(package_versions=dict(PKGS, our_indicators="0.1.0")),
            production(package_versions=dict(PKGS, our_indicators="0.1.1")))
        self.assertEqual(report.critical_discrepancies, 1)

    def test_precision_drift_blocks(self):
        report = self.engine.audit_environment_parity(
            research(), production(float_precision="float32"))
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.discrepancies[0].component, COMPONENT_PRECISION)

    def test_precision_synonyms_are_not_drift(self):
        # Regression: "double" and "float64" name the same IEEE 754 format and must
        # not audit as a CRITICAL mismatch.
        report = self.engine.audit_environment_parity(
            research(float_precision="float64"), production(float_precision="double"))
        self.assertEqual(report.status, STATUS_VERIFIED)

    def test_feature_hash_drift_blocks(self):
        report = self.engine.audit_environment_parity(
            research(),
            production(feature_definitions={"rsi_14": "sha256:aaa",
                                            "macd": "sha256:CHANGED"}))
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.critical_discrepancies, 1)
        self.assertEqual(report.discrepancies[0].item_name, "macd")
        self.assertEqual(report.discrepancies[0].component, COMPONENT_FEATURE)

    def test_missing_feature_reports_the_sentinel(self):
        report = self.engine.audit_environment_parity(
            research(), production(feature_definitions={"rsi_14": "sha256:aaa"}))
        found = report.discrepancies[0]
        self.assertEqual(found.item_name, "macd")
        self.assertEqual(found.production_val, MISSING)
        self.assertEqual(found.severity, SEVERITY_CRITICAL)

    def test_discrepancy_order_is_deterministic(self):
        prod = production(feature_definitions={"zeta": "1", "alpha": "2"})
        first = self.engine.audit_environment_parity(research(), prod)
        second = self.engine.audit_environment_parity(research(), prod)
        self.assertEqual([d.item_name for d in first.discrepancies],
                         [d.item_name for d in second.discrepancies])
        feature_items = [d.item_name for d in first.discrepancies
                         if d.component == COMPONENT_FEATURE]
        self.assertEqual(feature_items, sorted(feature_items))

    def test_multiple_vectors_accumulate(self):
        report = self.engine.audit_environment_parity(
            research(),
            production(python_version="3.10.14", float_precision="float32",
                       package_versions={"numpy": "2.1.0", "pandas": "2.2.1"},
                       feature_definitions={"rsi_14": "sha256:aaa",
                                            "macd": "sha256:X"}))
        self.assertEqual(report.critical_discrepancies, 4)
        self.assertEqual(
            set(report.critical_component_names),
            {COMPONENT_PYTHON, COMPONENT_PACKAGE, COMPONENT_PRECISION,
             COMPONENT_FEATURE})


class TestSignalDiffing(unittest.TestCase):
    def setUp(self):
        self.engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()

    def audit(self, signals):
        return self.engine.audit_environment_parity(research(), production(), signals)

    def test_signals_within_tolerance_pass(self):
        # 1.5000 vs 1.5001 is 6.67e-5 relative, inside the 1e-3 tolerance.
        report = self.audit([(1.5000, 1.5001), (2.1, 2.1)])
        self.assertEqual(report.status, STATUS_VERIFIED)
        self.assertEqual(report.signal_samples_compared, 2)
        self.assertEqual(report.signal_breach_count, 0)
        self.assertTrue(report.signal_diffing_performed)

    def test_signal_skew_breaches(self):
        # 1.50 vs 1.60 is 6.25e-2 relative against the larger magnitude.
        report = self.audit([(1.5000, 1.6000)])
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.signal_breach_count, 1)
        self.assertEqual(report.discrepancies[0].component, COMPONENT_SIGNAL)

    def test_tolerance_boundary_is_inclusive(self):
        # isclose passes at exactly rel_tol: 1.0 vs 1.001 is 1e-3 / 1.001 relative.
        self.assertEqual(self.audit([(1.0, 1.001)]).status, STATUS_VERIFIED)
        # And 1.0 vs 1.0011 exceeds it.
        self.assertEqual(self.audit([(1.0, 1.0011)]).status, STATUS_BREACHED)

    def test_nan_production_signal_breaches(self):
        # Regression: abs(1.5 - nan) > tol is False, so a NaN production signal was
        # certified as parity-verified.
        report = self.audit([(1.5, float("nan"))])
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertEqual(report.signal_breach_count, 1)
        self.assertIn("Non-finite", report.discrepancies[0].details)

    def test_nan_research_signal_breaches(self):
        self.assertEqual(self.audit([(float("nan"), 1.5)]).status, STATUS_BREACHED)

    def test_two_nans_are_not_parity(self):
        self.assertEqual(self.audit([(float("nan"), float("nan"))]).status,
                         STATUS_BREACHED)

    def test_two_infinities_are_not_parity(self):
        # math.isclose(inf, inf) is True, so the finite check has to come first.
        report = self.audit([(float("inf"), float("inf"))])
        self.assertEqual(report.status, STATUS_BREACHED)
        self.assertIn("Non-finite", report.discrepancies[0].details)

    def test_sign_flip_near_zero_breaches(self):
        # Regression: the old denominator floor of 1e-5 made +1e-9 vs -1e-9 a
        # relative difference of 2e-4, inside tolerance -- a long certified as a short.
        report = self.audit([(1e-9, -1e-9)])
        self.assertEqual(report.status, STATUS_BREACHED)

    def test_round_off_noise_around_zero_passes(self):
        # Below the absolute floor both values are indistinguishable from zero, so the
        # sign carries no information and this is not drift.
        self.assertEqual(self.audit([(1e-18, -1e-18)]).status, STATUS_VERIFIED)

    def test_exact_zeros_are_parity(self):
        self.assertEqual(self.audit([(0.0, 0.0)]).status, STATUS_VERIFIED)

    def test_zero_against_material_value_breaches(self):
        self.assertEqual(self.audit([(0.0, 5.0)]).status, STATUS_BREACHED)

    def test_comparison_is_symmetric(self):
        forward = self.audit([(1.0, 1.05)]).signal_breach_count
        reverse = self.audit([(1.05, 1.0)]).signal_breach_count
        self.assertEqual(forward, reverse)
        self.assertEqual(forward, 1)

    def test_none_means_not_run_and_is_recorded(self):
        report = self.engine.audit_environment_parity(research(), production(), None)
        self.assertFalse(report.signal_diffing_performed)

    def test_empty_signal_sequence_rejected(self):
        # Regression: [] was falsy and read as "no shadow diffing configured", so an
        # empty sample certified the strongest vector on zero comparisons.
        with self.assertRaises(ValueError):
            self.audit([])

    def test_malformed_sample_rejected(self):
        for bad in [[(1.0,)], [(1.0, 2.0, 3.0)], [1.0], ["ab"]]:
            with self.subTest(sample=bad):
                with self.assertRaises(TypeError):
                    self.audit(bad)

    def test_non_numeric_signal_rejected(self):
        for bad in [[(1.0, None)], [(1.0, "1.0")], [(True, 1.0)]]:
            with self.subTest(sample=bad):
                with self.assertRaises(TypeError):
                    self.audit(bad)

    def test_integer_signals_accepted(self):
        self.assertEqual(self.audit([(1, 1)]).status, STATUS_VERIFIED)

    def test_breach_list_is_capped_but_counts_stay_exact(self):
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            max_reported_signal_breaches=5)
        report = engine.audit_environment_parity(
            research(), production(), [(1.0, 2.0)] * 200)
        self.assertEqual(report.signal_breach_count, 200)
        self.assertEqual(report.critical_discrepancies, 200)
        self.assertEqual(len(report.discrepancies), 5)
        self.assertTrue(report.discrepancies_truncated)
        self.assertIn("truncated", report.audit_notes)
        self.assertFalse(report.is_parity_achieved)

    def test_untruncated_report_reports_no_truncation(self):
        report = self.audit([(1.0, 2.0)])
        self.assertFalse(report.discrepancies_truncated)

    def test_sample_index_is_reported(self):
        report = self.audit([(1.0, 1.0), (1.0, 5.0)])
        self.assertEqual(report.discrepancies[0].item_name, "signal_sample_1")


class TestEngineConfiguration(unittest.TestCase):
    def test_tolerance_bounds_enforced(self):
        for bad in [0.0, -0.1, 1.0, 2.0, float("nan"), float("inf")]:
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    ResearchEnvironmentVsProductionEnvironmentParityEngine(
                        max_signal_rel_diff=bad)

    def test_absolute_tolerance_bounds_enforced(self):
        for bad in [-1e-12, float("nan")]:
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    ResearchEnvironmentVsProductionEnvironmentParityEngine(
                        signal_abs_tol=bad)

    def test_non_numeric_tolerance_rejected(self):
        with self.assertRaises(TypeError):
            ResearchEnvironmentVsProductionEnvironmentParityEngine(
                max_signal_rel_diff="0.001")

    def test_bare_string_package_set_rejected(self):
        # A str satisfies Iterable[str], so an unguarded string would register one
        # single-character package name per letter.
        with self.assertRaises(TypeError):
            ResearchEnvironmentVsProductionEnvironmentParityEngine(
                numerically_critical_packages="numpy")

    def test_breach_cap_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            ResearchEnvironmentVsProductionEnvironmentParityEngine(
                max_reported_signal_breaches=0)

    def test_custom_tolerance_changes_the_verdict(self):
        loose = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            max_signal_rel_diff=0.1)
        report = loose.audit_environment_parity(
            research(), production(), [(1.00, 1.05)])
        self.assertEqual(report.status, STATUS_VERIFIED)

    def test_default_critical_package_set_is_normalized(self):
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()
        self.assertIn("scikit-learn", engine.numerically_critical_packages)
        self.assertEqual(engine.numerically_critical_packages,
                         DEFAULT_NUMERICALLY_CRITICAL_PACKAGES)


class TestReportContract(unittest.TestCase):
    def setUp(self):
        self.engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()

    def test_report_type_and_counts_are_consistent(self):
        report = self.engine.audit_environment_parity(
            research(python_version="3.11.2"),
            production(float_precision="float32"), [(1.0, 9.0)])
        self.assertIsInstance(report, EnvironmentParityReport)
        self.assertEqual(
            report.total_discrepancies,
            report.critical_discrepancies + report.warning_discrepancies)
        self.assertEqual(report.total_discrepancies, len(report.discrepancies))
        self.assertEqual(report.status, STATUS_BREACHED)

    def test_warnings_alone_do_not_block(self):
        report = self.engine.audit_environment_parity(
            research(python_version="3.11.2"), production(python_version="3.11.8"))
        self.assertTrue(report.is_parity_achieved)
        self.assertGreater(report.warning_discrepancies, 0)
        self.assertEqual(report.critical_discrepancies, 0)

    def test_every_discrepancy_carries_actionable_detail(self):
        report = self.engine.audit_environment_parity(
            research(),
            production(python_version="3.10.14", float_precision="float32",
                       package_versions={"numpy": "1.24.3", "pandas": "2.2.1"},
                       feature_definitions={"rsi_14": "sha256:aaa"}),
            [(1.0, 2.0)])
        self.assertTrue(report.discrepancies)
        for found in report.discrepancies:
            with self.subTest(item=found.item_name):
                self.assertTrue(found.details.strip())
                self.assertIn(found.severity, {SEVERITY_CRITICAL, SEVERITY_WARNING})

    def test_audit_notes_report_signal_counts(self):
        report = self.engine.audit_environment_parity(
            research(), production(), [(1.0, 1.0), (1.0, 4.0)])
        self.assertIn("1 of 2 signal samples breached", report.audit_notes)


class TestNumericalGrounding(unittest.TestCase):
    """Independently derived expectations, not restatements of the implementation."""

    def test_isclose_boundary_matches_hand_computation(self):
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            max_signal_rel_diff=0.01, signal_abs_tol=0.0)
        # |100 - 101| = 1; rel_tol * max(|a|,|b|) = 0.01 * 101 = 1.01 -> within.
        self.assertEqual(
            engine.audit_environment_parity(research(), production(),
                                            [(100.0, 101.0)]).status,
            STATUS_VERIFIED)
        # |100 - 102| = 2 > 0.01 * 102 = 1.02 -> breach.
        self.assertEqual(
            engine.audit_environment_parity(research(), production(),
                                            [(100.0, 102.0)]).status,
            STATUS_BREACHED)

    def test_float32_round_trip_of_a_price_stays_within_default_tolerance(self):
        # A single float64 -> float32 -> float64 round trip of an index level loses
        # about 1e-8 relative, far inside 1e-3: a precision mismatch is not by itself
        # a signal-tolerance breach, which is why precision is its own vector.
        import struct
        price = 45123.456789
        as_float32 = struct.unpack("f", struct.pack("f", price))[0]
        self.assertLess(abs(price - as_float32) / price, 1e-6)
        engine = ResearchEnvironmentVsProductionEnvironmentParityEngine()
        self.assertEqual(
            engine.audit_environment_parity(research(), production(),
                                            [(price, as_float32)]).status,
            STATUS_VERIFIED)

    def test_sign_flip_always_exceeds_any_admissible_tolerance(self):
        # Opposite signs at equal magnitude give a relative difference of exactly 2.0,
        # and the constructor caps rel_tol below 1.0, so no admissible configuration
        # can pass a direction flip at material magnitude.
        a, b = 0.25, -0.25
        self.assertEqual(abs(a - b) / max(abs(a), abs(b)), 2.0)
        loosest = ResearchEnvironmentVsProductionEnvironmentParityEngine(
            max_signal_rel_diff=0.999999)
        self.assertEqual(
            loosest.audit_environment_parity(research(), production(),
                                             [(a, b)]).status,
            STATUS_BREACHED)

    def test_isclose_semantics_assumption_holds(self):
        # The module relies on PEP 485 semantics; assert them rather than assume.
        self.assertFalse(math.isclose(1.0, float("nan")))
        self.assertTrue(math.isclose(float("inf"), float("inf")))


if __name__ == "__main__":
    unittest.main()
