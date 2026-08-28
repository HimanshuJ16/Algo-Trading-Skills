"""Unit tests for the point-in-time patent innovation signal engine.

Expected values in :class:`TestExactScoring` are derived by hand from the
specification in SKILL.md (cohort-adjust, then standardise, then weight), not by
re-running the implementation's own arithmetic.
"""

import logging
import unittest
from datetime import date

from patent_filing_data_for_innovation_signal_research import (
    AssetInnovationScore,
    PatentDataError,
    PatentFilingDataForInnovationSignalResearchEngine,
    PatentFilingRecord,
    PatentInnovationReport,
    PatentSignalConfig,
)

# The engine logs its audit line on every call; report fields carry the
# behavioural contract, so keep test output readable.
logging.disable(logging.CRITICAL)

AS_OF = date(2024, 1, 1)
GRANT = date(2022, 6, 1)
FILED = date(2020, 1, 1)
CITES_READ = date(2023, 12, 1)


def make_record(
    asset,
    patent_id,
    citations=0,
    tech="T",
    grant_date=GRANT,
    pre_grant=None,
    filing_date=FILED,
    cites_asof=CITES_READ,
    claim_count=None,
):
    """Build a record with the point-in-time dates already consistent."""
    return PatentFilingRecord(
        asset_id=asset,
        patent_id=patent_id,
        filing_date=filing_date,
        grant_date=grant_date,
        pre_grant_publication_date=pre_grant,
        forward_citations=citations,
        citations_observed_asof=cites_asof if citations else None,
        claim_count=claim_count,
        technology_class=tech,
    )


def universe(spec, tech="T"):
    """spec = {asset: (n_patents, citations_each)} -> flat record list."""
    records = []
    for asset, (count, cites) in spec.items():
        for i in range(count):
            records.append(make_record(asset, f"{asset}-{i}", citations=cites, tech=tech))
    return records


class TestExactScoring(unittest.TestCase):
    """Numeric checks against independently hand-derived values.

    Universe: AAA = 10 patents x 1 citation, BBB = 2 x 50, CCC = 4 x 5.
    All share cohort (T, 2022), so the cohort holds 16 patents and its mean is
    (10*1 + 2*50 + 4*5) / 16 = 130 / 16 = 8.125.

    Cohort-adjusted quality (mean ratio per patent, log compression off):
        AAA = 1 / 8.125  = 0.123077
        BBB = 50 / 8.125 = 6.153846
        CCC = 5 / 8.125  = 0.615385

    Velocity = 10, 2, 4; population mean 16/3, sigma sqrt(112/9) / ... giving
        z_vel  = (+1.372813, -0.980581, -0.392232)
        z_qual = (-0.795219, +1.410388, -0.615169)
    Equal weights -> composite = (0.288797, 0.214904, -0.503701), and
    re-standardising that composite gives the final factor below.
    """

    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2, log_compress_citations=False)
        )
        self.records = universe({"AAA": (10, 1), "BBB": (2, 50), "CCC": (4, 5)})

    def test_cohort_adjusted_quality_matches_hand_calculation(self):
        report = self.engine.compute_patent_innovation_signals(self.records, as_of=AS_OF)
        self.assertEqual(report.status, "SIGNALS_GENERATED")
        self.assertAlmostEqual(report.detail["AAA"].citation_quality, 0.123077, places=6)
        self.assertAlmostEqual(report.detail["BBB"].citation_quality, 6.153846, places=6)
        self.assertAlmostEqual(report.detail["CCC"].citation_quality, 0.615385, places=6)

    def test_component_z_scores_match_hand_calculation(self):
        report = self.engine.compute_patent_innovation_signals(self.records, as_of=AS_OF)
        self.assertAlmostEqual(report.detail["AAA"].velocity_z, 1.372813, places=6)
        self.assertAlmostEqual(report.detail["BBB"].velocity_z, -0.980581, places=6)
        self.assertAlmostEqual(report.detail["CCC"].velocity_z, -0.392232, places=6)
        self.assertAlmostEqual(report.detail["AAA"].citation_z, -0.795219, places=6)
        self.assertAlmostEqual(report.detail["BBB"].citation_z, 1.410388, places=6)
        self.assertAlmostEqual(report.detail["CCC"].citation_z, -0.615169, places=6)

    def test_final_factor_matches_hand_calculation(self):
        report = self.engine.compute_patent_innovation_signals(self.records, as_of=AS_OF)
        self.assertAlmostEqual(report.z_scores["AAA"], 0.807947, places=6)
        self.assertAlmostEqual(report.z_scores["BBB"], 0.601221, places=6)
        self.assertAlmostEqual(report.z_scores["CCC"], -1.409168, places=6)

    def test_factor_is_mean_zero_unit_variance(self):
        report = self.engine.compute_patent_innovation_signals(self.records, as_of=AS_OF)
        values = list(report.z_scores.values())
        n = len(values)
        mean = sum(values) / n
        var = sum((v - mean) ** 2 for v in values) / n
        self.assertAlmostEqual(mean, 0.0, places=6)
        self.assertAlmostEqual(var, 1.0, places=6)


class TestScaleInvariance(unittest.TestCase):
    """Regression test for the weighting defect (raw count + sum of logs).

    Standardising each component before weighting makes the factor invariant to
    the units of either component. The superseded implementation computed
    ``0.5 * count + 0.5 * sum(ln(1 + citations))`` on the raw scales, so
    multiplying every citation count by a constant moved the score and could
    reorder the universe.
    """

    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2, log_compress_citations=False)
        )

    def test_rescaling_all_citations_leaves_the_factor_unchanged(self):
        base = universe({"AAA": (10, 1), "BBB": (2, 50), "CCC": (4, 5)})
        scaled = universe({"AAA": (10, 10), "BBB": (2, 500), "CCC": (4, 50)})

        base_z = self.engine.compute_patent_innovation_signals(base, as_of=AS_OF).z_scores
        scaled_z = self.engine.compute_patent_innovation_signals(scaled, as_of=AS_OF).z_scores

        self.assertEqual(sorted(base_z), sorted(scaled_z))
        for asset in base_z:
            self.assertAlmostEqual(base_z[asset], scaled_z[asset], places=9)

    def test_weights_actually_select_the_component(self):
        records = universe({"AAA": (10, 1), "BBB": (2, 50), "CCC": (4, 5)})

        velocity_only = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(velocity_weight=1.0, citation_weight=0.0, min_cohort_size=2)
        ).compute_patent_innovation_signals(records, as_of=AS_OF)
        # Pure velocity: most patents wins, regardless of their impact.
        self.assertEqual(velocity_only.top_innovator, "AAA")

        citation_only = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(velocity_weight=0.0, citation_weight=1.0, min_cohort_size=2)
        ).compute_patent_innovation_signals(records, as_of=AS_OF)
        # Pure quality: the two-patent, 50-citation issuer wins.
        self.assertEqual(citation_only.top_innovator, "BBB")


class TestCitationCohortAdjustment(unittest.TestCase):
    """The truncation/field adjustment must actually depend on the cohort."""

    def test_same_raw_count_scores_differently_across_technology_classes(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2, log_compress_citations=False)
        )
        records = []
        # Cohort HOT: mean citations = (20 + 20 + 100 + 100) / 4 = 60
        records += [make_record("AAA", f"h{i}", citations=20, tech="HOT") for i in range(2)]
        records += [make_record("HOTPEER", f"hp{i}", citations=100, tech="HOT") for i in range(2)]
        # Cohort COLD: mean citations = (20 + 20 + 0 + 0) / 4 = 10
        records += [make_record("BBB", f"c{i}", citations=20, tech="COLD") for i in range(2)]
        records += [make_record("COLDPEER", f"cp{i}", citations=0, tech="COLD") for i in range(2)]

        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)

        # Identical raw counts (20), different cohorts: 20/60 vs 20/10.
        self.assertAlmostEqual(report.detail["AAA"].citation_quality, 20.0 / 60.0, places=6)
        self.assertAlmostEqual(report.detail["BBB"].citation_quality, 20.0 / 10.0, places=6)
        self.assertGreater(
            report.detail["BBB"].citation_z, report.detail["AAA"].citation_z
        )

    def test_cohort_below_min_size_is_counted_and_left_unadjusted(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=5, log_compress_citations=False)
        )
        records = [
            make_record("AAA", "a1", citations=8, tech="RARE"),
            make_record("BBB", "b1", citations=2, tech="RARE"),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)

        self.assertEqual(report.cohorts_below_min_size, 1)
        self.assertEqual(report.detail["AAA"].cohort_adjusted_patents, 0)
        # Unadjusted: the raw count is used verbatim.
        self.assertAlmostEqual(report.detail["AAA"].citation_quality, 8.0, places=9)
        self.assertTrue(any("truncation-biased" in w for w in report.warnings))

    def test_all_zero_cohort_does_not_divide_by_zero(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        records = [make_record("AAA", "a1"), make_record("AAA", "a2"), make_record("BBB", "b1")]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.status, "SIGNALS_GENERATED")
        self.assertEqual(report.detail["AAA"].citation_quality, 0.0)


class TestPointInTimeAvailability(unittest.TestCase):
    """Look-ahead controls — the reason this engine takes an as_of at all."""

    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )

    def test_availability_is_the_earlier_of_pre_grant_publication_and_grant(self):
        record = make_record(
            "AAA", "a1", grant_date=date(2023, 5, 1), pre_grant=date(2021, 7, 1)
        )
        self.assertEqual(record.public_availability_date, date(2021, 7, 1))

    def test_non_published_application_becomes_public_only_at_grant(self):
        # 35 U.S.C. 122(b)(2)(B)(i) non-publication request: no A-publication.
        record = make_record("AAA", "a1", grant_date=date(2023, 5, 1), pre_grant=None)
        self.assertEqual(record.public_availability_date, date(2023, 5, 1))

    def test_pending_unpublished_application_is_never_public(self):
        record = make_record("AAA", "a1", grant_date=None, pre_grant=None)
        self.assertIsNone(record.public_availability_date)

    def test_patent_public_after_as_of_is_excluded_not_scored(self):
        """Regression: the superseded engine counted every record it was given."""
        records = [
            make_record("AAA", "a1", grant_date=date(2022, 1, 1)),
            make_record("AAA", "a2", grant_date=date(2023, 12, 1)),  # public before as_of
            make_record("AAA", "a3", grant_date=date(2024, 6, 1)),   # NOT yet public
            make_record("BBB", "b1", grant_date=date(2022, 1, 1)),
        ]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)

        self.assertEqual(report.not_yet_public_excluded, 1)
        self.assertEqual(report.detail["AAA"].patents_in_window, 2)
        self.assertEqual(report.patents_scored, 3)

    def test_pending_application_is_excluded(self):
        records = [
            make_record("AAA", "a1"),
            make_record("AAA", "a2", grant_date=None, pre_grant=None),
            make_record("BBB", "b1"),
        ]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.not_yet_public_excluded, 1)
        self.assertEqual(report.detail["AAA"].patents_in_window, 1)

    def test_citation_count_observed_after_as_of_is_rejected(self):
        """A cumulative count read today contains citations that had not happened."""
        records = [
            make_record("AAA", "a1", citations=40, cites_asof=date(2026, 1, 1)),
            make_record("BBB", "b1", citations=1),
        ]
        with self.assertRaises(PatentDataError) as ctx:
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIn("after as_of", str(ctx.exception))

    def test_undated_citation_count_is_rejected(self):
        records = [
            PatentFilingRecord(
                asset_id="AAA",
                patent_id="a1",
                filing_date=FILED,
                grant_date=GRANT,
                forward_citations=17,
                citations_observed_asof=None,
            )
        ]
        with self.assertRaises(PatentDataError) as ctx:
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIn("citations_observed_asof", str(ctx.exception))

    def test_zero_citations_need_no_observation_date(self):
        records = [make_record("AAA", "a1"), make_record("BBB", "b1")]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.patents_scored, 2)

    def test_as_of_must_be_a_date(self):
        with self.assertRaises(TypeError):
            self.engine.compute_patent_innovation_signals([make_record("AAA", "a1")], as_of="2024-01-01")

    def test_rolling_as_of_forward_admits_more_patents(self):
        records = [
            make_record("AAA", "a1", grant_date=date(2021, 1, 1)),
            make_record("AAA", "a2", grant_date=date(2023, 1, 1)),
            make_record("BBB", "b1", grant_date=date(2021, 1, 1)),
        ]
        early = self.engine.compute_patent_innovation_signals(records, as_of=date(2022, 1, 1))
        late = self.engine.compute_patent_innovation_signals(records, as_of=date(2023, 6, 1))
        self.assertEqual(early.detail["AAA"].patents_in_window, 1)
        self.assertEqual(late.detail["AAA"].patents_in_window, 2)


class TestLookbackWindow(unittest.TestCase):
    def test_patent_older_than_the_window_is_excluded(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(lookback_years=5, min_cohort_size=2)
        )
        records = [
            make_record("AAA", "old", filing_date=date(2010, 1, 1), grant_date=date(2012, 1, 1)),
            make_record("AAA", "new", grant_date=date(2022, 1, 1)),
            make_record("BBB", "b1", grant_date=date(2022, 1, 1)),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.outside_lookback_window_excluded, 1)
        self.assertEqual(report.detail["AAA"].patents_in_window, 1)

    def test_window_boundary_is_inclusive(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(lookback_years=5, min_cohort_size=2)
        )
        records = [
            # as_of 2024-01-01 with a 5y window starts exactly 2019-01-01.
            make_record("AAA", "edge", filing_date=date(2017, 1, 1), grant_date=date(2019, 1, 1)),
            make_record("BBB", "b1", grant_date=date(2022, 1, 1)),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.outside_lookback_window_excluded, 0)
        self.assertEqual(report.detail["AAA"].patents_in_window, 1)

    def test_leap_day_as_of_does_not_raise(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(lookback_years=1, min_cohort_size=2)
        )
        records = [
            make_record("AAA", "a1", filing_date=date(2023, 4, 1), grant_date=date(2023, 6, 1)),
            make_record("AAA", "a2", filing_date=date(2023, 4, 1), grant_date=date(2023, 6, 1)),
            make_record("BBB", "b1", filing_date=date(2023, 4, 1), grant_date=date(2023, 6, 1)),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=date(2024, 2, 29))
        self.assertEqual(report.status, "SIGNALS_GENERATED")


class TestInnovationInputScaling(unittest.TestCase):
    """R&D scaling — Hirshleifer, Hsu & Li (2013) innovative efficiency."""

    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2, log_compress_citations=False)
        )

    def test_velocity_is_divided_by_the_supplied_input(self):
        records = universe({"AAA": (10, 1), "BBB": (2, 1)})
        report = self.engine.compute_patent_innovation_signals(
            records, as_of=AS_OF, innovation_inputs={"AAA": 100.0, "BBB": 5.0}
        )
        # 10/100 = 0.1 vs 2/5 = 0.4 — the small, efficient filer wins on velocity.
        self.assertAlmostEqual(report.detail["AAA"].velocity, 0.1, places=9)
        self.assertAlmostEqual(report.detail["BBB"].velocity, 0.4, places=9)
        self.assertGreater(report.detail["BBB"].velocity_z, report.detail["AAA"].velocity_z)
        self.assertTrue(report.detail["AAA"].velocity_scaled)

    def test_r_and_d_scaling_can_reverse_the_raw_count_ranking(self):
        records = universe({"AAA": (10, 1), "BBB": (2, 1)})
        unscaled = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        scaled = self.engine.compute_patent_innovation_signals(
            records, as_of=AS_OF, innovation_inputs={"AAA": 100.0, "BBB": 5.0}
        )
        self.assertEqual(unscaled.top_innovator, "AAA")
        self.assertEqual(scaled.top_innovator, "BBB")

    def test_missing_input_is_reported_not_silently_mixed(self):
        records = universe({"AAA": (3, 1), "BBB": (2, 1)})
        report = self.engine.compute_patent_innovation_signals(
            records, as_of=AS_OF, innovation_inputs={"AAA": 10.0}
        )
        self.assertEqual(report.assets_missing_innovation_input, ["BBB"])
        self.assertTrue(any("not comparable" in w for w in report.warnings))

    def test_absent_inputs_warn_about_the_size_component(self):
        records = universe({"AAA": (3, 1), "BBB": (2, 1)})
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertTrue(any("firm-size component" in w for w in report.warnings))

    def test_zero_input_falls_back_rather_than_dividing_by_zero(self):
        records = universe({"AAA": (3, 1), "BBB": (2, 1)})
        report = self.engine.compute_patent_innovation_signals(
            records, as_of=AS_OF, innovation_inputs={"AAA": 0.0, "BBB": 4.0}
        )
        self.assertEqual(report.detail["AAA"].velocity, 3.0)
        self.assertFalse(report.detail["AAA"].velocity_scaled)


class TestDataQualityGuards(unittest.TestCase):
    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )

    def test_duplicate_patent_id_is_counted_once(self):
        """Assignee-disambiguation joins emit one row per assignee per patent."""
        records = [
            make_record("AAA", "US-1"),
            make_record("AAA", "US-1"),
            make_record("AAA", "us-1"),  # case-insensitive duplicate
            make_record("BBB", "US-2"),
        ]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.duplicate_patent_ids_dropped, 2)
        self.assertEqual(report.detail["AAA"].patents_in_window, 1)

    def test_nan_citation_count_raises_instead_of_scoring_zero(self):
        """Regression: ``max(0, float('nan'))`` is ``0``, so NaN once read as
        'no citations' rather than as a broken upstream join."""
        records = [make_record("AAA", "a1", citations=float("nan")), make_record("BBB", "b1")]
        with self.assertRaises(PatentDataError) as ctx:
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIn("not finite", str(ctx.exception))

    def test_infinite_citation_count_raises(self):
        records = [make_record("AAA", "a1", citations=float("inf"))]
        with self.assertRaises(PatentDataError):
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)

    def test_negative_citation_count_raises(self):
        records = [make_record("AAA", "a1", citations=-5)]
        with self.assertRaises(PatentDataError):
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)

    def test_blank_identifiers_raise(self):
        with self.assertRaises(PatentDataError):
            self.engine.compute_patent_innovation_signals(
                [make_record("   ", "a1")], as_of=AS_OF
            )
        with self.assertRaises(PatentDataError):
            self.engine.compute_patent_innovation_signals(
                [make_record("AAA", "  ")], as_of=AS_OF
            )

    def test_grant_date_before_filing_date_raises(self):
        records = [
            make_record("AAA", "a1", filing_date=date(2022, 1, 1), grant_date=date(2021, 1, 1))
        ]
        with self.assertRaises(PatentDataError) as ctx:
            self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIn("precedes filing_date", str(ctx.exception))

    def test_asset_ids_are_case_normalised(self):
        records = [make_record("aapl", "a1"), make_record("AAPL", "a2"), make_record("BBB", "b1")]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.total_assets, 2)
        self.assertEqual(report.detail["AAPL"].patents_in_window, 2)

    def test_counters_reconcile_against_records_supplied(self):
        records = [
            make_record("AAA", "a1"),
            make_record("AAA", "a1"),                                   # duplicate
            make_record("AAA", "a2", grant_date=date(2025, 1, 1)),      # not yet public
            make_record("AAA", "a3", filing_date=date(2010, 1, 1),
                        grant_date=date(2012, 1, 1)),                   # outside window
            make_record("BBB", "b1"),
        ]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.records_supplied, 5)
        self.assertEqual(report.patents_scored, 2)
        self.assertEqual(report.duplicate_patent_ids_dropped, 1)
        self.assertEqual(report.not_yet_public_excluded, 1)
        self.assertEqual(report.outside_lookback_window_excluded, 1)
        self.assertTrue(report.reconciles())


class TestDegenerateUniverses(unittest.TestCase):
    def setUp(self):
        self.engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )

    def test_empty_input_returns_empty_universe(self):
        report = self.engine.compute_patent_innovation_signals([], as_of=AS_OF)
        self.assertEqual(report.status, "EMPTY_UNIVERSE")
        self.assertEqual(report.total_assets, 0)
        self.assertEqual(report.z_scores, {})
        self.assertEqual(report.top_innovator, "NONE")

    def test_single_asset_universe_refuses_to_standardise(self):
        records = [make_record("AAA", "a1"), make_record("AAA", "a2")]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.status, "INSUFFICIENT_UNIVERSE")
        # No composite exists with no peers, so neither field is populated --
        # asset_scores must not quietly change meaning between statuses.
        self.assertEqual(report.z_scores, {})
        self.assertEqual(report.asset_scores, {})
        self.assertIn("AAA=2 patent(s)", report.audit_notes)

    def test_universe_with_no_dispersion_returns_zero_factor(self):
        records = universe({"AAA": (3, 4), "BBB": (3, 4), "CCC": (3, 4)})
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.status, "NO_DISPERSION")
        self.assertEqual(report.top_innovator, "NONE")
        self.assertTrue(all(z == 0.0 for z in report.z_scores.values()))

    def test_all_records_excluded_reports_no_eligible_patents(self):
        records = [
            make_record("AAA", "a1", grant_date=date(2030, 1, 1)),
            make_record("BBB", "b1", grant_date=date(2030, 1, 1)),
        ]
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.status, "EMPTY_UNIVERSE")
        self.assertEqual(report.not_yet_public_excluded, 2)
        self.assertIn("not-yet-public=2", report.audit_notes)

    def test_small_universe_flags_inert_winsorisation(self):
        records = universe({"AAA": (3, 1), "BBB": (2, 5), "CCC": (1, 9)})
        report = self.engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertFalse(report.winsorisation_can_bind)
        self.assertTrue(report.universe_below_recommended_size)
        self.assertTrue(any("inert" in w for w in report.warnings))


class TestCitationObservationSpan(unittest.TestCase):
    """Counts read on different dates cover different exposure windows."""

    def test_wide_observation_span_warns(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2, max_citation_observation_span_days=31)
        )
        records = [
            make_record("AAA", "a1", citations=10, cites_asof=date(2021, 1, 1)),
            make_record("BBB", "b1", citations=10, cites_asof=date(2023, 12, 1)),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.citation_observation_span_days, 1064)
        self.assertTrue(any("not cross-comparable" in w for w in report.warnings))

    def test_single_read_date_does_not_warn(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        records = [
            make_record("AAA", "a1", citations=10),
            make_record("BBB", "b1", citations=4),
        ]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(report.citation_observation_span_days, 0)
        self.assertFalse(any("not cross-comparable" in w for w in report.warnings))

    def test_negative_span_limit_rejected(self):
        with self.assertRaises(ValueError):
            PatentSignalConfig(max_citation_observation_span_days=-1)


class TestWinsorisation(unittest.TestCase):
    def test_binding_flag_tracks_the_configured_limit_not_a_fixed_three(self):
        records = universe({"AAA": (3, 1), "BBB": (2, 5), "CCC": (1, 9)})
        # N=3 bounds |z| at sqrt(2) = 1.414: inert at 3.0, able to bind at 1.0.
        loose = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(winsorize_z=3.0, min_cohort_size=2)
        ).compute_patent_innovation_signals(records, as_of=AS_OF)
        tight = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(winsorize_z=1.0, min_cohort_size=2)
        ).compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertFalse(loose.winsorisation_can_bind)
        self.assertTrue(tight.winsorisation_can_bind)
        self.assertTrue(all(abs(z) <= 1.0 for z in tight.z_scores.values()))


class TestDeterminism(unittest.TestCase):
    def test_output_is_independent_of_input_order(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        records = universe({"AAA": (3, 4), "BBB": (2, 9), "CCC": (5, 1)})
        forward = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        reverse = engine.compute_patent_innovation_signals(
            list(reversed(records)), as_of=AS_OF
        )
        self.assertEqual(forward.z_scores, reverse.z_scores)
        self.assertEqual(forward.top_innovator, reverse.top_innovator)

    def test_tied_top_scores_break_deterministically_on_asset_id(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        # ZZZ and AAA tie exactly; CCC is strictly worse.
        records = (
            [make_record("ZZZ", f"z{i}", citations=6) for i in range(3)]
            + [make_record("AAA", f"a{i}", citations=6) for i in range(3)]
            + [make_record("CCC", "c0", citations=0)]
        )
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertAlmostEqual(report.z_scores["AAA"], report.z_scores["ZZZ"], places=9)
        self.assertEqual(report.top_innovator, "AAA")
        reordered = engine.compute_patent_innovation_signals(
            list(reversed(records)), as_of=AS_OF
        )
        self.assertEqual(reordered.top_innovator, "AAA")


class TestClaimCountHandling(unittest.TestCase):
    def test_claim_count_is_reported_but_not_scored(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        few = [
            make_record("AAA", "a1", citations=5, claim_count=3),
            make_record("BBB", "b1", citations=5, claim_count=3),
            make_record("CCC", "c1", citations=1, claim_count=3),
        ]
        many = [
            make_record("AAA", "a1", citations=5, claim_count=90),
            make_record("BBB", "b1", citations=5, claim_count=3),
            make_record("CCC", "c1", citations=1, claim_count=3),
        ]
        few_report = engine.compute_patent_innovation_signals(few, as_of=AS_OF)
        many_report = engine.compute_patent_innovation_signals(many, as_of=AS_OF)

        self.assertEqual(few_report.detail["AAA"].mean_claim_count, 3.0)
        self.assertEqual(many_report.detail["AAA"].mean_claim_count, 90.0)
        # A 30x claim count must not move the factor.
        self.assertEqual(few_report.z_scores, many_report.z_scores)

    def test_missing_claim_counts_report_none(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        records = [make_record("AAA", "a1"), make_record("BBB", "b1")]
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIsNone(report.detail["AAA"].mean_claim_count)


class TestConfigValidation(unittest.TestCase):
    def test_negative_weight_rejected(self):
        with self.assertRaises(ValueError):
            PatentSignalConfig(velocity_weight=-0.1)

    def test_zero_total_weight_rejected(self):
        with self.assertRaises(ValueError):
            PatentSignalConfig(velocity_weight=0.0, citation_weight=0.0)

    def test_non_positive_lookback_rejected(self):
        with self.assertRaises(ValueError):
            PatentSignalConfig(lookback_years=0)

    def test_min_universe_below_two_rejected(self):
        with self.assertRaises(ValueError):
            PatentSignalConfig(min_universe_size=1)

    def test_unequal_weights_are_renormalised(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(velocity_weight=2.0, citation_weight=2.0, min_cohort_size=2)
        )
        equal = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(velocity_weight=0.5, citation_weight=0.5, min_cohort_size=2)
        )
        records = universe({"AAA": (3, 4), "BBB": (2, 9), "CCC": (5, 1)})
        a = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        b = equal.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertEqual(a.z_scores, b.z_scores)


class TestReportShape(unittest.TestCase):
    def test_report_types(self):
        engine = PatentFilingDataForInnovationSignalResearchEngine(
            PatentSignalConfig(min_cohort_size=2)
        )
        records = universe({"AAA": (3, 4), "BBB": (2, 9), "CCC": (5, 1)})
        report = engine.compute_patent_innovation_signals(records, as_of=AS_OF)
        self.assertIsInstance(report, PatentInnovationReport)
        self.assertIsInstance(report.detail["AAA"], AssetInnovationScore)
        self.assertEqual(report.as_of, AS_OF)
        self.assertEqual(set(report.z_scores), set(report.asset_scores))
        self.assertIn(report.top_innovator, report.z_scores)


if __name__ == "__main__":
    unittest.main()
