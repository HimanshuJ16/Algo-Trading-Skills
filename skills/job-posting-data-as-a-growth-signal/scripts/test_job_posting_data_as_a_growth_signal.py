import unittest

from job_posting_data_as_a_growth_signal import (
    CONTRACTION_BEARISH,
    EXPANSION_BULLISH,
    INSUFFICIENT_DATA,
    STABLE_NEUTRAL,
    CompanyJobPostingSnapshot,
    JobPostingSignalEngine,
    JobPostingSignalError,
)


def make_snapshot(**overrides):
    """A valid baseline snapshot; each test overrides only the field under test."""
    base = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_active_postings_count=125,
        previous_active_postings_count=100,
        engineering_postings_pct=0.0,
        sales_postings_pct=0.0,
        avg_posting_duration_days=30.0,
    )
    base.update(overrides)
    return CompanyJobPostingSnapshot(**base)


class TestJobPostingSignalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = JobPostingSignalEngine(ghost_job_stale_days_threshold=120.0)

    def test_tech_expansion_bullish_signal(self):
        # NVIDIA: 300 active postings (+100% vs 150 prev), 50% Engineering, Avg duration 35 days.
        # Role factor = 1 + 0.50*0.5 + 0.30*0.3 = 1.34; raw = 1.00 * 1.34 = 1.34, clamped to +1.0.
        snapshot = CompanyJobPostingSnapshot(
            ticker="NVDA", company_name="NVIDIA Corp", current_active_postings_count=300,
            previous_active_postings_count=150, engineering_postings_pct=0.50,
            sales_postings_pct=0.30, avg_posting_duration_days=35.0
        )
        report = self.engine.calculate_growth_score(snapshot)

        self.assertEqual(report.signal_classification, EXPANSION_BULLISH)
        self.assertEqual(report.qoq_postings_growth_pct, 100.0)
        self.assertFalse(report.has_ghost_postings_penalty)
        self.assertEqual(report.role_weighting_factor, 1.34)
        self.assertEqual(report.raw_growth_score, 1.34)
        self.assertEqual(report.corporate_growth_score, 1.0)

    def test_stale_ghost_job_penalty_and_contraction_signal(self):
        # Layoff firm: 50 active postings (-50% vs 100 prev), Avg duration 150 days (> 120).
        # Role factor = 1 + 0.10*0.5 + 0.10*0.3 = 1.08; raw = -0.50 * 1.08 * 0.5 = -0.27.
        snapshot = make_snapshot(
            ticker="STALE_CO", company_name="Stale Retailer", current_active_postings_count=50,
            previous_active_postings_count=100, engineering_postings_pct=0.10,
            sales_postings_pct=0.10, avg_posting_duration_days=150.0
        )
        report = self.engine.calculate_growth_score(snapshot)

        self.assertEqual(report.signal_classification, CONTRACTION_BEARISH)
        self.assertEqual(report.qoq_postings_growth_pct, -50.0)
        self.assertTrue(report.has_ghost_postings_penalty)
        self.assertEqual(report.corporate_growth_score, -0.27)

    # --- Ghost haircut semantics -------------------------------------------------

    def test_ghost_haircut_shrinks_bearish_signal_toward_neutral(self):
        """The haircut attenuates a contraction as much as an expansion: -0.54 -> -0.27."""
        fresh = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=50, previous_active_postings_count=100,
            engineering_postings_pct=0.10, sales_postings_pct=0.10,
            avg_posting_duration_days=90.0))
        stale = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=50, previous_active_postings_count=100,
            engineering_postings_pct=0.10, sales_postings_pct=0.10,
            avg_posting_duration_days=150.0))

        self.assertEqual(fresh.corporate_growth_score, -0.54)
        self.assertEqual(stale.corporate_growth_score, -0.27)
        self.assertEqual(abs(stale.corporate_growth_score) * 2, abs(fresh.corporate_growth_score))

    def test_ghost_haircut_can_demote_a_contraction_to_neutral(self):
        """-40% growth is bearish while fresh and neutral once halved: the documented trap."""
        fresh = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=60, previous_active_postings_count=100,
            avg_posting_duration_days=30.0))
        stale = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=60, previous_active_postings_count=100,
            avg_posting_duration_days=150.0))

        self.assertEqual(fresh.corporate_growth_score, -0.40)
        self.assertEqual(fresh.signal_classification, CONTRACTION_BEARISH)
        self.assertEqual(stale.corporate_growth_score, -0.20)
        self.assertEqual(stale.signal_classification, STABLE_NEUTRAL)

    def test_stale_threshold_is_strictly_greater_than(self):
        at_threshold = self.engine.calculate_growth_score(
            make_snapshot(avg_posting_duration_days=120.0))
        past_threshold = self.engine.calculate_growth_score(
            make_snapshot(avg_posting_duration_days=120.01))

        self.assertFalse(at_threshold.has_ghost_postings_penalty)
        self.assertTrue(past_threshold.has_ghost_postings_penalty)

    def test_stale_haircut_factor_is_configurable(self):
        disabled = JobPostingSignalEngine(stale_haircut_factor=0.0).calculate_growth_score(
            make_snapshot(avg_posting_duration_days=150.0))
        full = JobPostingSignalEngine(stale_haircut_factor=1.0).calculate_growth_score(
            make_snapshot(avg_posting_duration_days=150.0))

        # 125 vs 100 == +25% growth, role factor 1.0.
        self.assertTrue(disabled.has_ghost_postings_penalty)
        self.assertEqual(disabled.corporate_growth_score, 0.25)
        self.assertEqual(full.corporate_growth_score, 0.0)

    # --- Classification boundaries ------------------------------------------------

    def test_classification_band_is_inclusive_at_the_boundary(self):
        at_bull = self.engine.calculate_growth_score(
            make_snapshot(current_active_postings_count=125))          # +25% -> +0.25
        below_bull = self.engine.calculate_growth_score(
            make_snapshot(current_active_postings_count=124))          # +24% -> +0.24
        at_bear = self.engine.calculate_growth_score(
            make_snapshot(current_active_postings_count=75))           # -25% -> -0.25
        above_bear = self.engine.calculate_growth_score(
            make_snapshot(current_active_postings_count=76))           # -24% -> -0.24

        self.assertEqual(at_bull.corporate_growth_score, 0.25)
        self.assertEqual(at_bull.signal_classification, EXPANSION_BULLISH)
        self.assertEqual(below_bull.signal_classification, STABLE_NEUTRAL)
        self.assertEqual(at_bear.corporate_growth_score, -0.25)
        self.assertEqual(at_bear.signal_classification, CONTRACTION_BEARISH)
        self.assertEqual(above_bear.signal_classification, STABLE_NEUTRAL)

    def test_flat_hiring_is_neutral(self):
        report = self.engine.calculate_growth_score(
            make_snapshot(current_active_postings_count=100))

        self.assertEqual(report.qoq_postings_growth_pct, 0.0)
        self.assertEqual(report.corporate_growth_score, 0.0)
        self.assertEqual(report.signal_classification, STABLE_NEUTRAL)

    # --- Small-base gate (regression: fabricated denominators) ---------------------

    def test_tiny_previous_base_is_gated_not_scored(self):
        """Regression: 2 -> 10 postings is +400% and previously scored a saturated BULLISH."""
        report = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=10, previous_active_postings_count=2))

        self.assertEqual(report.signal_classification, INSUFFICIENT_DATA)
        self.assertEqual(report.corporate_growth_score, 0.0)
        self.assertEqual(report.raw_growth_score, 0.0)
        self.assertEqual(report.qoq_postings_growth_pct, 400.0)  # still reported for audit

    def test_zero_previous_base_is_gated_not_divided_by_one(self):
        """Regression: prev=0 previously became prev=1, yielding a +29,900% growth rate."""
        report = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=300, previous_active_postings_count=0))

        self.assertEqual(report.signal_classification, INSUFFICIENT_DATA)
        self.assertEqual(report.qoq_postings_growth_pct, 0.0)
        self.assertEqual(report.corporate_growth_score, 0.0)
        # The audit line must not read "+0.0%" -- a 0 -> 300 move is undefined, not flat.
        self.assertIn("undefined", report.audit_notes)
        self.assertNotIn("+0.0%", report.audit_notes)

    def test_zero_previous_base_is_gated_even_with_the_floor_disabled(self):
        """A 0 -> 300 expansion must not be reported as flat just because the floor is 0."""
        engine = JobPostingSignalEngine(min_previous_postings=0)
        report = engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=300, previous_active_postings_count=0))

        self.assertEqual(report.signal_classification, INSUFFICIENT_DATA)
        self.assertEqual(report.corporate_growth_score, 0.0)

    def test_previous_base_exactly_at_floor_is_scored(self):
        report = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=20, previous_active_postings_count=10))

        self.assertEqual(report.qoq_postings_growth_pct, 100.0)
        self.assertEqual(report.signal_classification, EXPANSION_BULLISH)

    # --- Saturation ---------------------------------------------------------------

    def test_raw_score_preserves_ranking_where_the_clamp_saturates(self):
        """Both firms clamp to +1.0; only raw_growth_score separates them."""
        moderate = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=300, previous_active_postings_count=150,
            engineering_postings_pct=0.50, sales_postings_pct=0.30))
        extreme = self.engine.calculate_growth_score(make_snapshot(
            current_active_postings_count=600, previous_active_postings_count=150))

        self.assertEqual(moderate.corporate_growth_score, 1.0)
        self.assertEqual(extreme.corporate_growth_score, 1.0)
        self.assertEqual(moderate.raw_growth_score, 1.34)
        self.assertEqual(extreme.raw_growth_score, 3.0)
        self.assertIn("SATURATED", extreme.audit_notes)

    def test_unsaturated_score_is_not_flagged_saturated(self):
        report = self.engine.calculate_growth_score(make_snapshot())
        self.assertEqual(report.raw_growth_score, 0.25)
        self.assertNotIn("SATURATED", report.audit_notes)

    # --- Input validation ---------------------------------------------------------

    def test_nan_duration_raises_instead_of_silently_skipping_the_penalty(self):
        """Regression: nan > 120 is False, so a NaN duration used to escape the haircut."""
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(avg_posting_duration_days=float("nan")))

    def test_nan_count_raises_instead_of_reporting_a_confident_contraction(self):
        """Regression: a NaN growth rate clamped to -1.0 and reported CONTRACTION_BEARISH."""
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(current_active_postings_count=float("nan")))

    def test_infinite_count_raises(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(current_active_postings_count=float("inf")))

    def test_percentage_supplied_as_whole_number_raises_instead_of_clamping(self):
        """50 means 5000%, not 50%: previously clamped to 1.0 and scored confidently."""
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(make_snapshot(engineering_postings_pct=50))

    def test_negative_share_raises(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(make_snapshot(sales_postings_pct=-0.1))

    def test_role_shares_summing_above_one_raise(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(engineering_postings_pct=0.7, sales_postings_pct=0.4))

    def test_role_shares_summing_to_exactly_one_are_accepted(self):
        report = self.engine.calculate_growth_score(
            make_snapshot(engineering_postings_pct=0.7, sales_postings_pct=0.3))
        self.assertEqual(report.role_weighting_factor, 1.44)

    def test_negative_posting_count_raises(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(previous_active_postings_count=-100))

    def test_blank_ticker_raises_so_no_signal_is_unattributable(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(make_snapshot(ticker="   "))

    def test_boolean_count_is_rejected(self):
        with self.assertRaises(JobPostingSignalError):
            self.engine.calculate_growth_score(
                make_snapshot(current_active_postings_count=True))

    # --- Engine configuration validation ------------------------------------------

    def test_invalid_engine_configuration_raises(self):
        with self.assertRaises(JobPostingSignalError):
            JobPostingSignalEngine(stale_haircut_factor=1.5)
        with self.assertRaises(JobPostingSignalError):
            JobPostingSignalEngine(ghost_job_stale_days_threshold=-1.0)
        with self.assertRaises(JobPostingSignalError):
            JobPostingSignalEngine(classification_threshold=0.0)
        with self.assertRaises(JobPostingSignalError):
            JobPostingSignalEngine(min_previous_postings=-5)
        with self.assertRaises(JobPostingSignalError):
            # Above the clamp bound nothing could ever classify: dead configuration.
            JobPostingSignalEngine(classification_threshold=1.5)

    def test_audit_notes_identify_the_company_and_the_classification(self):
        report = self.engine.calculate_growth_score(make_snapshot(ticker="ACME"))
        self.assertIn("ACME", report.audit_notes)
        self.assertIn(report.signal_classification, report.audit_notes)


if __name__ == '__main__':
    unittest.main()
