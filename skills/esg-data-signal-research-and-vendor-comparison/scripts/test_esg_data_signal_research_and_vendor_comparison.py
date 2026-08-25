"""Behavioural tests for the ESG cross-vendor signal engine.

Every expected value below is derived by hand from the vendor scales documented
in the engine's module docstring, not by re-running the engine's own arithmetic.

Reference normalizations used throughout:
  MSCI band mid-points, (2k+1)/14 for k = 0 (CCC) .. 6 (AAA):
      CCC 0.0714  B 0.2143  BB 0.3571  BBB 0.5000
      A   0.6429  AA 0.7857  AAA 0.9286
  Sustainalytics, bounded at the Severe floor of 40:  1 - min(risk, 40) / 40
      risk 0 -> 1.0    risk 8 -> 0.8    risk 15 -> 0.625
      risk 30 -> 0.25  risk 40 -> 0.0   risk 55 -> 0.0
  LSEG/Refinitiv:  score / 100
"""

import logging
import math
import unittest
from datetime import datetime, timezone

from esg_data_signal_research_and_vendor_comparison import (
    MSCI_RATING_MAP,
    MSCI_RATING_ORDER,
    SIGNAL_BEARISH,
    SIGNAL_BULLISH,
    SIGNAL_EXCLUDED,
    SIGNAL_HIGH_DISAGREEMENT,
    SIGNAL_INSUFFICIENT_COVERAGE,
    SIGNAL_NEUTRAL,
    EsgDataSignalEngine,
    RawVendorEsgData,
)

# The engine logs every classification at INFO/WARNING; silence it so the test
# output stays readable.
logging.getLogger(
    "esg_data_signal_research_and_vendor_comparison"
).addHandler(logging.NullHandler())
logging.getLogger("esg_data_signal_research_and_vendor_comparison").propagate = False


class TestVendorNormalization(unittest.TestCase):
    def setUp(self):
        self.engine = EsgDataSignalEngine()

    def test_msci_map_uses_published_band_midpoints(self):
        # MSCI splits the 0-10 Industry-Adjusted Score into seven equal bands.
        # The point estimate for a letter is the band mid-point, (2k+1)/14.
        expected = {
            "CCC": 0.0714,
            "B": 0.2143,
            "BB": 0.3571,
            "BBB": 0.5000,
            "A": 0.6429,
            "AA": 0.7857,
            "AAA": 0.9286,
        }
        self.assertEqual(MSCI_RATING_MAP, expected)
        self.assertEqual(len(MSCI_RATING_ORDER), 7)
        # Regression: the old map used band end-points, putting AAA at exactly
        # 1.0 and CCC at exactly 0.0.
        self.assertNotEqual(MSCI_RATING_MAP["AAA"], 1.0)
        self.assertNotEqual(MSCI_RATING_MAP["CCC"], 0.0)

    def test_msci_is_monotonic_in_rating_quality(self):
        scores = [MSCI_RATING_MAP[r] for r in MSCI_RATING_ORDER]
        self.assertEqual(scores, sorted(scores))

    def test_msci_tolerates_case_and_whitespace(self):
        self.assertEqual(self.engine.normalize_msci("  aa "), 0.7857)
        self.assertEqual(self.engine.normalize_msci("AAA"), 0.9286)

    def test_msci_missing_coverage_is_none(self):
        self.assertIsNone(self.engine.normalize_msci(None))
        self.assertIsNone(self.engine.normalize_msci(""))
        self.assertIsNone(self.engine.normalize_msci("   "))

    def test_msci_unrecognised_token_raises_instead_of_silently_dropping(self):
        # Regression: these used to return None, which is indistinguishable
        # from "MSCI does not cover this issuer".
        for bad in ("A+", "AAAA", "D", "BBB-"):
            with self.subTest(rating=bad):
                with self.assertRaises(ValueError):
                    self.engine.normalize_msci(bad)

    def test_msci_non_string_raises(self):
        with self.assertRaises(TypeError):
            self.engine.normalize_msci(1.0)

    def test_sustainalytics_bounded_at_severe_floor(self):
        self.assertEqual(self.engine.normalize_sustainalytics(0.0), 1.0)
        self.assertEqual(self.engine.normalize_sustainalytics(8.0), 0.8)
        self.assertEqual(self.engine.normalize_sustainalytics(15.0), 0.625)
        self.assertEqual(self.engine.normalize_sustainalytics(30.0), 0.25)
        self.assertEqual(self.engine.normalize_sustainalytics(40.0), 0.0)
        # Severe Risk is open-ended above 40; the whole band is the scale floor.
        self.assertEqual(self.engine.normalize_sustainalytics(55.0), 0.0)

    def test_sustainalytics_severe_issuer_can_reach_the_laggard_band(self):
        # Regression: under the old 1 - risk/100 rescale a Severe-risk issuer
        # scoring 45 normalized to 0.55, above the mid-point of the scale, and
        # could never contribute to a laggard signal.
        self.assertLessEqual(self.engine.normalize_sustainalytics(45.0), 0.30)

    def test_sustainalytics_full_scale_override_reproduces_naive_rescale(self):
        naive = EsgDataSignalEngine(sustainalytics_severe_threshold=100.0)
        self.assertEqual(naive.normalize_sustainalytics(15.0), 0.85)

    def test_refinitiv_is_a_straight_rescale(self):
        self.assertEqual(self.engine.normalize_refinitiv(0.0), 0.0)
        self.assertEqual(self.engine.normalize_refinitiv(85.0), 0.85)
        self.assertEqual(self.engine.normalize_refinitiv(100.0), 1.0)

    def test_missing_numeric_scores_are_none(self):
        self.assertIsNone(self.engine.normalize_sustainalytics(None))
        self.assertIsNone(self.engine.normalize_refinitiv(None))

    def test_nan_is_rejected_not_silently_normalized(self):
        # Regression: NaN used to normalize to 1.0 (Refinitiv) and 0.0
        # (Sustainalytics), so a single NaN could emit a leader signal.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.engine.normalize_refinitiv(bad)
                with self.assertRaises(ValueError):
                    self.engine.normalize_sustainalytics(bad)

    def test_out_of_range_scores_are_rejected_not_clipped(self):
        # Regression: 500.0 used to clip to a perfect 1.0.
        for bad in (500.0, -20.0, 100.01, -0.01):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    self.engine.normalize_refinitiv(bad)
                with self.assertRaises(ValueError):
                    self.engine.normalize_sustainalytics(bad)

    def test_boolean_is_not_accepted_as_a_score(self):
        with self.assertRaises(TypeError):
            self.engine.normalize_refinitiv(True)

    def test_published_sustainalytics_risk_categories(self):
        cases = [
            (0.0, "NEGLIGIBLE"),
            (9.99, "NEGLIGIBLE"),
            (10.0, "LOW"),
            (19.99, "LOW"),
            (20.0, "MEDIUM"),
            (29.99, "MEDIUM"),
            (30.0, "HIGH"),
            (39.99, "HIGH"),
            (40.0, "SEVERE"),
            (100.0, "SEVERE"),
        ]
        for score, label in cases:
            with self.subTest(score=score):
                self.assertEqual(
                    EsgDataSignalEngine.sustainalytics_risk_category(score), label
                )


class TestConsensusAndDispersion(unittest.TestCase):
    def setUp(self):
        self.engine = EsgDataSignalEngine()

    def test_bullish_esg_leader_across_three_vendors(self):
        # AAA -> 0.9286, risk 8 -> 0.8, Refinitiv 85 -> 0.85.
        # mean = 2.5786 / 3 = 0.85953333 -> 0.8595
        # population sd = sqrt(0.0084053 / 3) = 0.052932 -> 0.0529
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="MSFT",
                msci_rating="AAA",
                sustainalytics_risk_score=8.0,
                refinitiv_esg_score=85.0,
            )
        )
        self.assertEqual(report.consensus_esg_score, 0.8595)
        self.assertEqual(report.vendor_disagreement_dispersion, 0.0529)
        self.assertEqual(report.vendor_count, 3)
        self.assertFalse(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, SIGNAL_BULLISH)

    def test_bearish_esg_laggard_with_agreeing_vendors(self):
        # CCC -> 0.0714, Refinitiv 20 -> 0.2.
        # mean = 0.2714 / 2 = 0.1357; population sd = |0.0714 - 0.2| / 2 = 0.0643
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="LAGGARD", msci_rating="CCC", refinitiv_esg_score=20.0
            )
        )
        self.assertEqual(report.consensus_esg_score, 0.1357)
        self.assertEqual(report.vendor_disagreement_dispersion, 0.0643)
        self.assertEqual(report.signal, SIGNAL_BEARISH)

    def test_high_vendor_disagreement_blocks_a_directional_call(self):
        # AAA -> 0.9286, risk 30 -> 0.25.
        # mean = 1.1786 / 2 = 0.5893; population sd = 0.6786 / 2 = 0.3393
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="TSLA", msci_rating="AAA", sustainalytics_risk_score=30.0
            )
        )
        self.assertEqual(report.consensus_esg_score, 0.5893)
        self.assertEqual(report.vendor_disagreement_dispersion, 0.3393)
        self.assertTrue(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, SIGNAL_HIGH_DISAGREEMENT)

    def test_disagreement_gate_is_applied_symmetrically_to_laggards(self):
        # Regression for the asymmetric gate: the laggard branch used to be
        # evaluated before the disagreement check, so this case emitted a
        # confident BEARISH_ESG_LAGGARD while dispersion was 0.36.
        # CCC -> 0.0714, risk 40 -> 0.0, Refinitiv 80 -> 0.8.
        # mean = 0.8714 / 3 = 0.29046667 -> 0.2905, i.e. inside the laggard band.
        # population sd = sqrt(0.3919853 / 3) = 0.361472 -> 0.3615
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="SPLIT",
                msci_rating="CCC",
                sustainalytics_risk_score=40.0,
                refinitiv_esg_score=80.0,
            )
        )
        self.assertEqual(report.consensus_esg_score, 0.2905)
        self.assertLessEqual(report.consensus_esg_score, 0.30)
        self.assertEqual(report.vendor_disagreement_dispersion, 0.3615)
        self.assertTrue(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, SIGNAL_HIGH_DISAGREEMENT)

    def test_dispersion_exactly_at_threshold_is_not_high_disagreement(self):
        # BBB -> 0.5, Refinitiv 100 -> 1.0.
        # mean = 0.75 exactly; population sd = 0.5 / 2 = 0.25 exactly.
        # The comparison is strictly greater-than, and 0.75 meets the leader
        # threshold at its boundary.
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="EDGE", msci_rating="BBB", refinitiv_esg_score=100.0
            )
        )
        self.assertEqual(report.vendor_disagreement_dispersion, 0.25)
        self.assertFalse(report.has_high_vendor_disagreement)
        self.assertEqual(report.consensus_esg_score, 0.75)
        self.assertEqual(report.signal, SIGNAL_BULLISH)

    def test_consensus_exactly_at_laggard_threshold_is_bearish(self):
        # BB -> 0.3571, Refinitiv 24.29 -> 0.2429. mean = 0.6 / 2 = 0.30 exactly.
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="BOUND", msci_rating="BB", refinitiv_esg_score=24.29
            )
        )
        self.assertEqual(report.consensus_esg_score, 0.30)
        self.assertEqual(report.signal, SIGNAL_BEARISH)

    def test_middle_band_with_perfectly_agreeing_vendors_is_neutral(self):
        # A -> 0.6429 and Refinitiv 64.29 -> 0.6429: mean 0.6429, dispersion 0.0.
        # Between the laggard (0.30) and leader (0.75) thresholds.
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(ticker="MID", msci_rating="A", refinitiv_esg_score=64.29)
        )
        self.assertEqual(report.consensus_esg_score, 0.6429)
        self.assertEqual(report.vendor_disagreement_dispersion, 0.0)
        self.assertFalse(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, SIGNAL_NEUTRAL)


class TestCoverageGating(unittest.TestCase):
    def setUp(self):
        self.engine = EsgDataSignalEngine()

    def test_single_vendor_cannot_produce_a_conviction_signal(self):
        # Regression: one vendor yields dispersion 0.0 by construction, and the
        # engine used to report that as "low vendor dispersion" and emit a
        # leader signal off a single uncorroborated opinion.
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(ticker="SOLO", refinitiv_esg_score=90.0)
        )
        self.assertEqual(report.vendor_count, 1)
        self.assertEqual(report.consensus_esg_score, 0.9)
        self.assertIsNone(report.vendor_disagreement_dispersion)
        self.assertFalse(report.has_high_vendor_disagreement)
        self.assertEqual(report.signal, SIGNAL_INSUFFICIENT_COVERAGE)

    def test_no_vendor_coverage_reports_undefined_not_zero(self):
        # Regression: consensus used to be 0.0, which a factor ranker reads as
        # the worst ESG name in the universe.
        report = self.engine.analyze_esg_signal(RawVendorEsgData(ticker="EMPTY"))
        self.assertEqual(report.vendor_count, 0)
        self.assertIsNone(report.consensus_esg_score)
        self.assertIsNone(report.vendor_disagreement_dispersion)
        self.assertEqual(report.signal, SIGNAL_INSUFFICIENT_COVERAGE)

    def test_lowering_the_coverage_floor_permits_single_vendor_signals(self):
        engine = EsgDataSignalEngine(min_vendors_for_conviction=1)
        report = engine.analyze_esg_signal(
            RawVendorEsgData(ticker="SOLO", refinitiv_esg_score=90.0)
        )
        self.assertEqual(report.signal, SIGNAL_BULLISH)
        self.assertIsNone(report.vendor_disagreement_dispersion)
        self.assertIn("undefined (single vendor)", report.audit_notes)

    def test_requiring_all_three_vendors(self):
        engine = EsgDataSignalEngine(min_vendors_for_conviction=3)
        report = engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="TWO", msci_rating="AAA", refinitiv_esg_score=95.0
            )
        )
        self.assertEqual(report.vendor_count, 2)
        self.assertEqual(report.signal, SIGNAL_INSUFFICIENT_COVERAGE)
        # The note must not claim dispersion is undefined: two vendors define it.
        self.assertNotIn("single opinion", report.audit_notes)
        self.assertIn("coverage floor", report.audit_notes)


class TestExclusions(unittest.TestCase):
    def setUp(self):
        self.engine = EsgDataSignalEngine()

    def test_controversial_weapons_overrides_a_leader_consensus(self):
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="ARMS",
                msci_rating="AAA",
                sustainalytics_risk_score=5.0,
                refinitiv_esg_score=95.0,
                has_controversial_weapons=True,
            )
        )
        self.assertEqual(report.signal, SIGNAL_EXCLUDED)
        self.assertEqual(report.exclusion_reasons, ("CONTROVERSIAL_WEAPONS",))

    def test_exclusion_preserves_the_normalized_audit_trail(self):
        # Regression: the exclusion path used to blank every normalized score
        # and report consensus 0.0, destroying the audit trail on exactly the
        # records most likely to be reviewed.
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="ARMS",
                msci_rating="AAA",
                refinitiv_esg_score=95.0,
                has_controversial_weapons=True,
            )
        )
        self.assertEqual(report.normalized_msci_score, 0.9286)
        self.assertEqual(report.normalized_refinitiv_score, 0.95)
        self.assertEqual(report.consensus_esg_score, 0.9393)
        self.assertEqual(report.vendor_count, 2)

    def test_generic_exclusion_reasons_are_normalized_and_deduplicated(self):
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="COAL",
                msci_rating="BBB",
                refinitiv_esg_score=50.0,
                has_controversial_weapons=True,
                exclusion_reasons=(
                    " tobacco ",
                    "THERMAL_COAL_1PCT",
                    "controversial_weapons",
                    "",
                ),
            )
        )
        self.assertEqual(report.signal, SIGNAL_EXCLUDED)
        self.assertEqual(
            report.exclusion_reasons,
            ("CONTROVERSIAL_WEAPONS", "TOBACCO", "THERMAL_COAL_1PCT"),
        )

    def test_exclusion_applies_even_without_vendor_coverage(self):
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(ticker="ARMS", has_controversial_weapons=True)
        )
        self.assertEqual(report.signal, SIGNAL_EXCLUDED)
        self.assertIsNone(report.consensus_esg_score)

    def test_a_bare_string_is_not_a_sequence_of_reasons(self):
        with self.assertRaises(TypeError):
            self.engine.analyze_esg_signal(
                RawVendorEsgData(ticker="X", exclusion_reasons="TOBACCO")
            )


class TestInputContract(unittest.TestCase):
    def setUp(self):
        self.engine = EsgDataSignalEngine()

    def test_empty_ticker_is_rejected(self):
        for bad in ("", "   "):
            with self.subTest(ticker=bad):
                with self.assertRaises(ValueError):
                    self.engine.analyze_esg_signal(RawVendorEsgData(ticker=bad))

    def test_wrong_payload_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.analyze_esg_signal({"ticker": "AAPL"})

    def test_naive_as_of_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_esg_signal(
                RawVendorEsgData(
                    ticker="AAPL",
                    msci_rating="AA",
                    refinitiv_esg_score=70.0,
                    as_of=datetime(2026, 8, 24, 12, 0, 0),
                )
            )

    def test_aware_as_of_is_carried_into_the_report(self):
        stamp = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)
        report = self.engine.analyze_esg_signal(
            RawVendorEsgData(
                ticker="AAPL",
                msci_rating="AA",
                refinitiv_esg_score=70.0,
                as_of=stamp,
            )
        )
        self.assertEqual(report.as_of, stamp)

    def test_nan_score_cannot_reach_a_signal(self):
        # Regression: a NaN Refinitiv score alone used to emit
        # BULLISH_ESG_LEADER with a consensus of 1.0.
        with self.assertRaises(ValueError):
            self.engine.analyze_esg_signal(
                RawVendorEsgData(ticker="NANCO", refinitiv_esg_score=math.nan)
            )

    def test_invalid_constructor_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(disagreement_threshold=0.0)
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(disagreement_threshold=1.5)
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(leader_threshold=0.2, laggard_threshold=0.5)
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(min_vendors_for_conviction=0)
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(sustainalytics_severe_threshold=0.0)
        with self.assertRaises(ValueError):
            EsgDataSignalEngine(sustainalytics_severe_threshold=101.0)

    def test_custom_msci_map_is_honoured(self):
        engine = EsgDataSignalEngine(msci_rating_map={"AAA": 1.0, "CCC": 0.0})
        self.assertEqual(engine.normalize_msci("AAA"), 1.0)
        with self.assertRaises(ValueError):
            engine.normalize_msci("BBB")
        # The module-level default must not be mutated by the override.
        self.assertEqual(MSCI_RATING_MAP["AAA"], 0.9286)

    def test_engine_is_deterministic(self):
        data = RawVendorEsgData(
            ticker="AAPL",
            msci_rating="AA",
            sustainalytics_risk_score=17.0,
            refinitiv_esg_score=72.0,
        )
        first = self.engine.analyze_esg_signal(data)
        second = self.engine.analyze_esg_signal(data)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
