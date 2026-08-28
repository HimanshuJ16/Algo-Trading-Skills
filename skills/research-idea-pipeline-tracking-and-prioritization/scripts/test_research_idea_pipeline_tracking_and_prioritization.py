"""
Unit tests for the research-idea-pipeline-tracking-and-prioritization skill.

Expected priority scores are derived independently of the implementation: every
fixture uses a capacity that is an exact power of ten, so ``log10(capacity)`` is
an integer that can be written down by hand and the expected score is plain
arithmetic (e.g. ``1.2 * 7 / (4 * 4) = 0.525``) rather than a re-execution of the
module's own formula.

Tests marked REGRESSION describe the behaviour of the previous implementation
that they would have caught.
"""
import logging
import math
import unittest
from datetime import datetime, timedelta, timezone

from research_idea_pipeline_tracking_and_prioritization import (
    ALLOWED_TRANSITIONS,
    MAX_TIER,
    MIN_CAPACITY_USD,
    MIN_TIER,
    PipelineStage,
    PrioritizedIdea,
    ResearchIdea,
    ResearchIdeaPipelineTrackingAndPrioritizationEngine,
    ResearchPipelineError,
    ResearchPipelineReport,
)

# Keep test output clean without globally disabling logging.
_LOGGER = logging.getLogger("research_idea_pipeline_tracking_and_prioritization")
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FrozenClock:
    """Injectable clock so stall detection and history are deterministic."""

    def __init__(self, start: datetime = EPOCH) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now = self.now + timedelta(**kwargs)


def make_idea(idea_id="ID_01", title="StatArb Pair Trading", author="Quant A",
              sharpe=2.0, capacity=50_000_000.0, complexity=1, data_cost=1,
              stage=PipelineStage.PROPOSED):
    return ResearchIdea(idea_id, title, author, sharpe, capacity, complexity,
                        data_cost, stage=stage)


def make_engine(clock=None, **kwargs):
    return ResearchIdeaPipelineTrackingAndPrioritizationEngine(
        clock=clock or FrozenClock(), **kwargs
    )


class TestPriorityScore(unittest.TestCase):
    """The score itself, checked against hand-computed values."""

    def setUp(self):
        self.engine = make_engine()

    def test_score_matches_hand_computed_value(self):
        # 1.2 * log10(10_000_000) / (4 * 4) = 1.2 * 7 / 16 = 8.4 / 16 = 0.525
        idea = make_idea(sharpe=1.2, capacity=10_000_000.0, complexity=4, data_cost=4)
        self.assertAlmostEqual(self.engine.calculate_priority_score(idea), 0.525, places=12)

    def test_score_scales_linearly_with_sharpe_and_inversely_with_tiers(self):
        # 3.0 * log10(1e6) / (2 * 3) = 3.0 * 6 / 6 = 3.0
        idea = make_idea(sharpe=3.0, capacity=1_000_000.0, complexity=2, data_cost=3)
        self.assertAlmostEqual(self.engine.calculate_priority_score(idea), 3.0, places=12)

    def test_zero_sharpe_scores_zero(self):
        self.assertEqual(
            self.engine.calculate_priority_score(make_idea(sharpe=0.0)), 0.0
        )

    def test_capacity_at_one_dollar_contributes_nothing(self):
        # log10(1) = 0, so the whole score is 0 -- the documented lower bound of
        # the score's domain, not a silently floored value.
        idea = make_idea(capacity=MIN_CAPACITY_USD)
        self.assertEqual(self.engine.calculate_priority_score(idea), 0.0)

    def test_score_is_not_rounded(self):
        """REGRESSION: the score was rounded to 4 dp *before* ranking, so ideas
        differing beyond the 4th decimal tied and fell back to registration order."""
        idea = make_idea(sharpe=1.0, capacity=3_000_000.0, complexity=1, data_cost=1)
        score = self.engine.calculate_priority_score(idea)
        self.assertNotEqual(score, round(score, 4))

    def test_rejects_non_idea_argument(self):
        with self.assertRaises(ResearchPipelineError):
            self.engine.calculate_priority_score({"expected_sharpe": 2.0})


class TestIdeaValidation(unittest.TestCase):
    """Inputs the register must refuse rather than score."""

    def test_non_finite_sharpe_and_capacity_rejected(self):
        """REGRESSION: a NaN input produced priority_score = nan, and because NaN
        compares False against everything it surfaced at rank 1."""
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(ResearchPipelineError):
                    make_idea(sharpe=bad)
                with self.assertRaises(ResearchPipelineError):
                    make_idea(capacity=bad)

    def test_negative_sharpe_rejected(self):
        """A negative Sharpe makes the score non-monotone in complexity:
        -2.0 * 7 / 1 = -14.0 but -2.0 * 7 / 5 = -2.8, i.e. the harder idea wins."""
        with self.assertRaises(ResearchPipelineError):
            make_idea(sharpe=-2.0)

    def test_capacity_below_one_dollar_rejected(self):
        for bad in (0.0, 0.5, -1_000.0):
            with self.subTest(capacity=bad):
                with self.assertRaises(ResearchPipelineError):
                    make_idea(capacity=bad)

    def test_tier_bounds_enforced_not_clamped(self):
        """REGRESSION: complexity was clamped with max(value, 1), so complexity=0
        scored identically to complexity=1 -- the maximum possible score for the
        worst-specified idea -- and complexity=50 was accepted silently."""
        for bad in (0, -3, MAX_TIER + 1, 50):
            with self.subTest(tier=bad):
                with self.assertRaises(ResearchPipelineError):
                    make_idea(complexity=bad)
                with self.assertRaises(ResearchPipelineError):
                    make_idea(data_cost=bad)

    def test_tier_boundaries_accepted(self):
        for good in (MIN_TIER, MAX_TIER):
            with self.subTest(tier=good):
                self.assertEqual(make_idea(complexity=good).implementation_complexity, good)
                self.assertEqual(make_idea(data_cost=good).data_cost_tier, good)

    def test_non_integer_and_boolean_tiers_rejected(self):
        for bad in (2.0, "3", True):
            with self.subTest(tier=bad):
                with self.assertRaises(ResearchPipelineError):
                    make_idea(complexity=bad)

    def test_blank_identity_fields_rejected(self):
        with self.assertRaises(ResearchPipelineError):
            make_idea(idea_id="   ")
        with self.assertRaises(ResearchPipelineError):
            make_idea(title="")
        with self.assertRaises(ResearchPipelineError):
            make_idea(author=None)

    def test_unknown_stage_rejected_at_construction(self):
        with self.assertRaises(ResearchPipelineError):
            make_idea(stage="REJCTED")

    def test_stage_accepts_case_insensitive_string_and_enum(self):
        self.assertIs(make_idea(stage="backtesting").stage, PipelineStage.BACKTESTING)
        self.assertIs(
            make_idea(stage=PipelineStage.PAPER_TRADING).stage, PipelineStage.PAPER_TRADING
        )

    def test_idea_is_immutable(self):
        idea = make_idea()
        with self.assertRaises(Exception):
            idea.expected_sharpe = 99.0


class TestRegistration(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = make_engine(clock=self.clock)

    def test_duplicate_id_rejected_and_original_preserved(self):
        """REGRESSION: add_idea overwrote silently, so a duplicated id replaced a
        PRODUCTION_READY idea with a typo and reset its stage to PROPOSED."""
        self.engine.add_idea(make_idea("D", "original", stage=PipelineStage.BACKTESTING))
        with self.assertRaises(ResearchPipelineError):
            self.engine.add_idea(make_idea("D", "typo duplicate", sharpe=0.1))
        kept = self.engine.get_idea("D")
        self.assertEqual(kept.title, "original")
        self.assertIs(kept.stage, PipelineStage.BACKTESTING)
        self.assertEqual(len(self.engine.ideas), 1)

    def test_register_rejects_non_idea(self):
        with self.assertRaises(ResearchPipelineError):
            self.engine.add_idea({"idea_id": "X"})

    def test_ideas_property_is_a_copy(self):
        self.engine.add_idea(make_idea("A"))
        snapshot = self.engine.ideas
        snapshot.pop("A")
        self.assertIn("A", self.engine.ideas)

    def test_unknown_id_lookups_raise(self):
        for call in (self.engine.get_idea, self.engine.get_history):
            with self.subTest(call=call.__name__):
                with self.assertRaises(ResearchPipelineError):
                    call("NOPE")


class TestStageMachine(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = make_engine(clock=self.clock)
        self.engine.add_idea(make_idea("A"))

    def test_legal_progression(self):
        self.assertTrue(self.engine.update_stage("A", "BACKTESTING"))
        self.assertTrue(self.engine.update_stage("A", PipelineStage.PAPER_TRADING))
        self.assertTrue(self.engine.update_stage("A", "production_ready"))
        self.assertIs(self.engine.get_idea("A").stage, PipelineStage.PRODUCTION_READY)

    def test_same_stage_is_a_no_op(self):
        self.assertFalse(self.engine.update_stage("A", "PROPOSED"))
        self.assertEqual(self.engine.get_history("A"), ())

    def test_illegal_skip_forward_rejected(self):
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("A", "PRODUCTION_READY")
        self.assertIs(self.engine.get_idea("A").stage, PipelineStage.PROPOSED)

    def test_misspelt_stage_rejected(self):
        """REGRESSION: update_stage upper-cased any string, so 'rejcted' created a
        phantom REJCTED bucket and left the idea in the active ranking."""
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("A", "rejcted", reason="typo")
        report = self.engine.generate_pipeline_report()
        self.assertEqual(report.active_ideas, 1)
        self.assertNotIn("REJCTED", report.stage_breakdown)

    def test_unknown_idea_raises_instead_of_returning_false(self):
        """REGRESSION: the old method returned False, so a caller that ignored the
        return value believed an idea had been rejected while it was still ranked."""
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("MISSING", "BACKTESTING")

    def test_rejection_requires_a_reason(self):
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("A", "REJECTED")
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("A", "REJECTED", reason="   ")
        self.assertTrue(self.engine.update_stage("A", "REJECTED", reason="no edge"))

    def test_rejected_is_terminal_and_reopen_is_explicit(self):
        self.engine.update_stage("A", "REJECTED", reason="no edge")
        with self.assertRaises(ResearchPipelineError):
            self.engine.update_stage("A", "BACKTESTING")
        with self.assertRaises(ResearchPipelineError):
            self.engine.reopen_idea("A", reason="")
        self.engine.reopen_idea("A", reason="cheaper data vendor found")
        self.assertIs(self.engine.get_idea("A").stage, PipelineStage.PROPOSED)

    def test_reopen_only_applies_to_rejected_ideas(self):
        with self.assertRaises(ResearchPipelineError):
            self.engine.reopen_idea("A", reason="not rejected yet")

    def test_history_is_append_only_and_timestamped(self):
        self.engine.update_stage("A", "BACKTESTING", reason="passed triage")
        self.clock.advance(days=3)
        self.engine.update_stage("A", "REJECTED", reason="fails after costs")
        history = self.engine.get_history("A")
        self.assertEqual(len(history), 2)
        self.assertIs(history[0].to_stage, PipelineStage.BACKTESTING)
        self.assertEqual(history[0].at, EPOCH)
        self.assertEqual(history[1].reason, "fails after costs")
        self.assertEqual(history[1].at, EPOCH + timedelta(days=3))

    def test_transition_table_is_reachable_and_terminal_where_documented(self):
        self.assertEqual(ALLOWED_TRANSITIONS[PipelineStage.REJECTED], ())
        for stage in PipelineStage:
            if stage is not PipelineStage.REJECTED:
                self.assertIn(PipelineStage.REJECTED, ALLOWED_TRANSITIONS[stage])


class TestPipelineReport(unittest.TestCase):
    def setUp(self):
        self.clock = FrozenClock()
        self.engine = make_engine(clock=self.clock)

    def test_ranking_orders_by_score(self):
        # ID_01: 2.0 * log10(1e8) / (1 * 1) = 16.0
        # ID_02: 1.2 * log10(1e7) / (4 * 4) = 0.525
        self.engine.add_idea(
            make_idea("ID_01", "StatArb Pair Trading", sharpe=2.0, capacity=100_000_000.0,
                      complexity=1, data_cost=1, stage=PipelineStage.BACKTESTING)
        )
        self.engine.add_idea(
            make_idea("ID_02", "Satellite Image Foot Traffic", author="Quant B",
                      sharpe=1.2, capacity=10_000_000.0, complexity=4, data_cost=4)
        )
        report = self.engine.generate_pipeline_report()
        self.assertIsInstance(report, ResearchPipelineReport)
        self.assertEqual(report.status, "PIPELINE_ACTIVE")
        self.assertEqual((report.total_ideas, report.active_ideas), (2, 2))
        self.assertEqual([p.idea_id for p in report.ranked_ideas], ["ID_01", "ID_02"])
        self.assertEqual([p.rank for p in report.ranked_ideas], [1, 2])
        self.assertAlmostEqual(report.ranked_ideas[0].priority_score, 16.0, places=12)
        self.assertAlmostEqual(report.ranked_ideas[1].priority_score, 0.525, places=12)
        self.assertIs(report.top_priority_ideas[0].stage, PipelineStage.BACKTESTING)

    def test_ties_break_on_idea_id_regardless_of_registration_order(self):
        """REGRESSION: equal scores kept registration order, so the same backlog
        registered in a different order produced a different 'top' idea."""
        for idea_id in ("Z_LAST", "A_FIRST"):
            self.engine.add_idea(make_idea(idea_id, f"idea {idea_id}"))
        report = self.engine.generate_pipeline_report()
        self.assertEqual([p.idea_id for p in report.ranked_ideas], ["A_FIRST", "Z_LAST"])

    def test_ranking_uses_full_precision_scores(self):
        """REGRESSION: scores were rounded to 4 dp before sorting, so these two
        ideas tied at 6.4771 and were ordered by registration instead of value."""
        # 1.0 * log10(3e6) = 6.477121254719662
        # 1.0 * log10(3.0001e6) = 6.477135734...  (strictly larger)
        self.engine.add_idea(make_idea("LOW", "lower", sharpe=1.0, capacity=3_000_000.0))
        self.engine.add_idea(make_idea("HIGH", "higher", sharpe=1.0, capacity=3_000_100.0))
        report = self.engine.generate_pipeline_report()
        self.assertEqual([p.idea_id for p in report.ranked_ideas], ["HIGH", "LOW"])
        self.assertGreater(
            report.ranked_ideas[0].priority_score, report.ranked_ideas[1].priority_score
        )

    def test_rejected_ideas_excluded_from_ranking_but_counted(self):
        self.engine.add_idea(make_idea("ID_03", "Crypto Arbitrage", author="Quant C",
                                       sharpe=1.5, capacity=5_000_000.0,
                                       complexity=2, data_cost=2))
        self.engine.update_stage("ID_03", "REJECTED", reason="capacity too small")
        report = self.engine.generate_pipeline_report()
        self.assertEqual(report.total_ideas, 1)
        self.assertEqual(report.active_ideas, 0)
        self.assertEqual(report.stage_breakdown["REJECTED"], 1)
        self.assertEqual(report.ranked_ideas, ())

    def test_status_distinguishes_empty_from_fully_rejected(self):
        """REGRESSION: status was PIPELINE_ACTIVE whenever any idea existed, so a
        register in which every idea had been rejected still read as active."""
        self.assertEqual(self.engine.generate_pipeline_report().status, "NO_IDEAS")
        self.engine.add_idea(make_idea("A"))
        self.assertEqual(self.engine.generate_pipeline_report().status, "PIPELINE_ACTIVE")
        self.engine.update_stage("A", "REJECTED", reason="superseded")
        report = self.engine.generate_pipeline_report()
        self.assertEqual(report.status, "NO_ACTIVE_IDEAS")
        self.assertIn("no active ideas", report.audit_notes)

    def test_stage_breakdown_reports_every_stage(self):
        report = self.engine.generate_pipeline_report()
        self.assertEqual(
            sorted(report.stage_breakdown), sorted(s.value for s in PipelineStage)
        )
        self.assertEqual(set(report.stage_breakdown.values()), {0})

    def test_below_threshold_ideas_are_flagged_not_dropped(self):
        """REGRESSION: min_priority_score was accepted, defaulted to 1.0, and never
        read by any code path."""
        engine = make_engine(clock=self.clock, min_priority_score=1.0)
        # 0.5 * log10(1e6) / (5 * 5) = 0.5 * 6 / 25 = 0.12
        engine.add_idea(make_idea("WEAK", "weak idea", sharpe=0.5, capacity=1_000_000.0,
                                  complexity=5, data_cost=5))
        engine.add_idea(make_idea("STRONG", "strong idea", sharpe=2.0,
                                  capacity=1_000_000.0, complexity=1, data_cost=1))
        report = engine.generate_pipeline_report()
        self.assertEqual(report.below_threshold_count, 1)
        flags = {p.idea_id: p.below_priority_threshold for p in report.ranked_ideas}
        self.assertEqual(flags, {"STRONG": False, "WEAK": True})
        self.assertAlmostEqual(report.ranked_ideas[1].priority_score, 0.12, places=12)

    def test_top_priority_ideas_is_a_shortlist_of_the_full_ranking(self):
        """REGRESSION: top_priority_ideas held every active idea while the docs
        described it as the top-ranked shortlist."""
        engine = make_engine(clock=self.clock, top_n=2)
        for index in range(5):
            engine.add_idea(make_idea(f"ID_{index}", f"idea {index}",
                                      sharpe=1.0 + index, capacity=1_000_000.0))
        report = engine.generate_pipeline_report()
        self.assertEqual(len(report.ranked_ideas), 5)
        self.assertEqual(len(report.top_priority_ideas), 2)
        self.assertEqual(report.top_priority_ideas, report.ranked_ideas[:2])
        self.assertEqual([p.idea_id for p in report.top_priority_ideas], ["ID_4", "ID_3"])

    def test_report_carries_generation_timestamp(self):
        self.assertEqual(self.engine.generate_pipeline_report().generated_at, EPOCH)

    def test_prioritized_idea_field_order_is_stable(self):
        item = PrioritizedIdea("X", "t", PipelineStage.PROPOSED, 1.0, 1)
        self.assertFalse(item.below_priority_threshold)


class TestStallDetection(unittest.TestCase):
    """The pipeline-bottleneck pitfall the skill documents, made measurable."""

    def setUp(self):
        self.clock = FrozenClock()
        self.engine = make_engine(clock=self.clock, max_stage_age_days=30.0)
        self.engine.add_idea(make_idea("A", "stuck in backtesting"))
        self.engine.update_stage("A", "BACKTESTING", reason="triaged")

    def test_not_stalled_at_or_below_threshold(self):
        self.clock.advance(days=30)
        self.assertEqual(self.engine.generate_pipeline_report().stalled_ideas, ())

    def test_stalled_just_past_threshold(self):
        self.clock.advance(days=30, seconds=1)
        stalled = self.engine.generate_pipeline_report().stalled_ideas
        self.assertEqual([s.idea_id for s in stalled], ["A"])
        self.assertIs(stalled[0].stage, PipelineStage.BACKTESTING)
        self.assertGreater(stalled[0].days_in_stage, 30.0)

    def test_stage_change_resets_the_clock(self):
        self.clock.advance(days=45)
        self.engine.update_stage("A", "PAPER_TRADING", reason="backtest passed")
        self.assertEqual(self.engine.generate_pipeline_report().stalled_ideas, ())

    def test_terminal_stages_are_never_stalled(self):
        self.engine.update_stage("A", "REJECTED", reason="no edge")
        self.engine.add_idea(make_idea("B", "shipped"))
        self.engine.update_stage("B", "BACKTESTING", reason="triaged")
        self.engine.update_stage("B", "PAPER_TRADING", reason="ok")
        self.engine.update_stage("B", "PRODUCTION_READY", reason="ok")
        self.clock.advance(days=400)
        self.assertEqual(self.engine.generate_pipeline_report().stalled_ideas, ())

    def test_backwards_clock_is_reported_not_hidden(self):
        self.clock.advance(days=45)
        self.engine.add_idea(make_idea("B", "registered in the future"))
        self.clock.now = EPOCH
        with self.assertLogs(
            "research_idea_pipeline_tracking_and_prioritization", level="WARNING"
        ) as captured:
            report = self.engine.generate_pipeline_report()
        self.assertEqual(report.stalled_ideas, ())
        self.assertTrue(any("stall detection is unreliable" in m for m in captured.output))

    def test_stalled_list_is_ordered_by_age_then_id(self):
        self.engine.add_idea(make_idea("B", "older"))
        self.clock.advance(days=100)
        self.engine.add_idea(make_idea("C", "newer"))
        self.clock.advance(days=40)
        stalled = self.engine.generate_pipeline_report().stalled_ideas
        self.assertEqual([s.idea_id for s in stalled], ["A", "B", "C"])


class TestEngineConfiguration(unittest.TestCase):
    def test_constructor_rejects_invalid_arguments(self):
        bad_kwargs = [
            {"min_priority_score": float("nan")},
            {"min_priority_score": -1.0},
            {"top_n": 0},
            {"top_n": 1.5},
            {"top_n": True},
            {"max_stage_age_days": 0.0},
            {"max_stage_age_days": -5.0},
            {"max_stage_age_days": float("inf")},
            {"clock": "not-callable"},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ResearchPipelineError):
                    ResearchIdeaPipelineTrackingAndPrioritizationEngine(**kwargs)

    def test_naive_clock_rejected(self):
        engine = make_engine(clock=lambda: datetime(2026, 1, 1))
        with self.assertRaises(ResearchPipelineError):
            engine.add_idea(make_idea("A"))

    def test_default_clock_is_timezone_aware(self):
        engine = ResearchIdeaPipelineTrackingAndPrioritizationEngine()
        engine.add_idea(make_idea("A"))
        generated_at = engine.generate_pipeline_report().generated_at
        self.assertIsNotNone(generated_at.tzinfo)


class TestCapacityUnitContract(unittest.TestCase):
    """
    The score takes log10 of a dimensional quantity, so the unit capacity is
    expressed in is part of the formula. This test pins that documented
    behaviour: the same backlog priced in thousands of dollars ranks differently.
    It is the reason the skill mandates whole US dollars.
    """

    def _ranking(self, scale):
        engine = make_engine()
        # A: 1.0 * log10(1e9) / 1 = 9    B: 6.0 * log10(1e4) / 2 = 12  -> B, A
        engine.add_idea(make_idea("A", "wide capacity", sharpe=1.0,
                                  capacity=1_000_000_000.0 / scale,
                                  complexity=1, data_cost=1))
        # scaled by 1000: A = 6, B = 6 * 1 / 2 = 3 -> A, B
        engine.add_idea(make_idea("B", "high sharpe", sharpe=6.0,
                                  capacity=10_000.0 / scale,
                                  complexity=2, data_cost=1))
        return [p.idea_id for p in engine.generate_pipeline_report().ranked_ideas]

    def test_ranking_is_unit_dependent(self):
        self.assertEqual(self._ranking(scale=1), ["B", "A"])
        self.assertEqual(self._ranking(scale=1_000), ["A", "B"])

    def test_log10_of_powers_of_ten_is_exact(self):
        # Underpins every hand-computed expected value in this file.
        for exponent in range(0, 10):
            self.assertEqual(math.log10(10.0 ** exponent), float(exponent))


if __name__ == "__main__":
    unittest.main()
