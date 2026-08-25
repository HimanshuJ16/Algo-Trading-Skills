import unittest
from datetime import datetime, timedelta, timezone

from earnings_call_transcript_nlp_signal_research import (
    DEFAULT_LM_NEGATIVE,
    DEFAULT_LM_POSITIVE,
    DEFAULT_LM_UNCERTAINTY,
    EarningsTranscriptNlpEngine,
    PREPARED_REMARKS,
    SIGNAL_BEARISH,
    SIGNAL_BULLISH,
    SIGNAL_INSUFFICIENT,
    SIGNAL_NEUTRAL,
)

# --- Fixtures -----------------------------------------------------------------
# Expected polarity counts below are derived by hand-tagging each transcript
# against the Loughran-McDonald category flags, not by re-running the engine's
# own arithmetic.

# 6 LM positives (strong, improved, improvement, favorable, successful,
# profitability), 0 LM negatives, 0 LM uncertainty terms.
BEARISH_PREP = (
    "Our fourth quarter results were strong across every reporting segment, and margins "
    "improved sequentially for the third consecutive period. The improvement in operating "
    "leverage was broad based, pricing remained favorable in our two largest end markets, "
    "and the integration of the acquired platform has been successful. We continue to expect "
    "profitability to expand as the new capacity comes online later this year."
)
# 1 LM positive (benefit), 8 LM negatives (decline, shortfall, against, impairment,
# restructuring, delay, volatility, concern), 1 LM uncertainty term (volatility,
# which is flagged both Negative and Uncertainty in the LM master dictionary).
BEARISH_QA = (
    "To your question on the demand environment, we did see a decline in orders during the "
    "quarter and a shortfall against our internal plan in the industrial channel. Inventory "
    "levels remain elevated, we recorded an impairment on one legacy product line, and the "
    "restructuring of that business will delay some of the benefit we had previously "
    "outlined. Volatility in freight costs has been an ongoing concern for the team."
)

# 6 LM positives (improved, strong, exclusive, highest, successful, favorable),
# 1 LM negative (delay), 0 LM uncertainty terms.
BULLISH_PREP = (
    "Revenue growth accelerated in the quarter and gross margin improved by one hundred forty "
    "basis points. Our largest platform delivered strong unit volumes, the new distribution "
    "agreement is exclusive through the end of the decade, and customer retention reached its "
    "highest level since the merger. The pricing action we took last year has been successful. "
    "Free cash flow conversion was favorable despite a modest delay in one government program."
)
# 5 LM positives (stronger, efficiency, benefit, opportunity, improve), 1 LM
# negative (weaknesses), 0 LM uncertainty terms.
BULLISH_QA = (
    "On your question about the second half, order books are stronger than they were entering "
    "the year, and the efficiency programme we started in January is already delivering "
    "benefit ahead of plan. We see continued opportunity in the enterprise channel. The one "
    "area of weaknesses is the legacy hardware line, which we intend to improve through the "
    "product refresh scheduled for next quarter."
)
# Same polarity profile as BULLISH_QA, plus 5 LM uncertainty terms (may, vary,
# dependent, assumptions, could) and no additional polarity words.
BULLISH_QA_HEDGED = BULLISH_QA + (
    " Visibility beyond the next two quarters may vary and remains dependent on assumptions "
    "we could revisit."
)


class TestLexiconIntegrity(unittest.TestCase):
    """The default lexicons must stay inside their Loughran-McDonald categories."""

    def test_risk_is_uncertainty_not_negative(self):
        # LM classifies RISK/RISKS as Uncertainty only. Treating them as negative
        # is the general-purpose-lexicon error this skill exists to prevent.
        for word in ("risk", "risks"):
            self.assertIn(word, DEFAULT_LM_UNCERTAINTY)
            self.assertNotIn(word, DEFAULT_LM_NEGATIVE)

    def test_non_lm_business_jargon_is_not_treated_as_positive(self):
        # None of these are members of the LM Positive list, however bullish they
        # sound in an earnings call.
        for word in ("growth", "record", "momentum", "robust", "expansion", "profit", "exceed"):
            self.assertNotIn(word, DEFAULT_LM_POSITIVE)

    def test_headwind_is_not_an_lm_negative(self):
        self.assertNotIn("headwind", DEFAULT_LM_NEGATIVE)

    def test_lm_category_overlap_is_preserved(self):
        # "volatility" is flagged both Negative and Uncertainty by LM.
        self.assertIn("volatility", DEFAULT_LM_NEGATIVE)
        self.assertIn("volatility", DEFAULT_LM_UNCERTAINTY)


class TestSectionScoring(unittest.TestCase):

    def setUp(self):
        self.engine = EarningsTranscriptNlpEngine()
        self.permissive = EarningsTranscriptNlpEngine(min_section_words=0, min_polarity_terms=0)

    def test_hand_tagged_section_counts(self):
        score = self.engine.analyze_text_section(PREPARED_REMARKS, BEARISH_PREP)
        self.assertEqual(score.positive_count, 6)
        self.assertEqual(score.negative_count, 0)
        self.assertEqual(score.uncertainty_count, 0)
        self.assertEqual(score.net_sentiment, 1.0)
        self.assertTrue(score.has_sufficient_sample)

    def test_uncertainty_counted_independently_of_polarity(self):
        score = self.engine.analyze_text_section("QA_SESSION", BEARISH_QA)
        self.assertEqual(score.positive_count, 1)
        self.assertEqual(score.negative_count, 8)
        self.assertEqual(score.uncertainty_count, 1)  # "volatility"
        self.assertEqual(score.net_sentiment, round(-7 / 9, 4))

    def test_lm_negation_rule_flips_positive_words(self):
        # Both positives sit within three tokens of a negator -> counted negative.
        score = self.permissive.analyze_text_section("QA_SESSION", "The quarter was not strong and demand did not improve.")
        self.assertEqual(score.positive_count, 0)
        self.assertEqual(score.negative_count, 2)
        self.assertEqual(score.negated_positive_count, 2)
        self.assertEqual(score.net_sentiment, -1.0)

    def test_negator_outside_three_word_window_does_not_flip(self):
        # "not" is five tokens ahead of "strong": outside the LM window.
        text = "We are not going to pretend results were strong."
        score = self.permissive.analyze_text_section("QA_SESSION", text)
        self.assertEqual(score.positive_count, 1)
        self.assertEqual(score.negated_positive_count, 0)

    def test_contractions_and_hyphenated_terms_are_single_tokens(self):
        self.assertEqual(self.engine.tokenize("we don't see a decline"), ["we", "don't", "see", "a", "decline"])
        self.assertEqual(self.engine.tokenize("year-over-year"), ["year-over-year"])

    def test_uncertainty_ratio_denominator_is_token_count(self):
        # 2 uncertainty terms ("may", "risk") in 9 tokens -> 22.22%.
        score = self.permissive.analyze_text_section("QA_SESSION", "margins may compress if the execution risk persists here")
        self.assertEqual(score.total_words, 9)
        self.assertEqual(score.uncertainty_count, 2)
        self.assertEqual(score.uncertainty_ratio_pct, round(200 / 9, 2))

    def test_empty_section_is_flagged_insufficient(self):
        score = self.engine.analyze_text_section("QA_SESSION", "")
        self.assertEqual(score.total_words, 0)
        self.assertEqual(score.net_sentiment, 0.0)
        self.assertFalse(score.has_sufficient_sample)

    def test_custom_lexicon_is_case_normalised(self):
        engine = EarningsTranscriptNlpEngine(
            positive_words=["Beat", " RAISED "],
            negative_words=["Missed"],
            min_section_words=0,
            min_polarity_terms=0,
        )
        score = engine.analyze_text_section("QA_SESSION", "We beat and raised but missed on units.")
        self.assertEqual(score.positive_count, 2)
        self.assertEqual(score.negative_count, 1)


class TestSignalGeneration(unittest.TestCase):

    def setUp(self):
        self.engine = EarningsTranscriptNlpEngine()

    def test_bearish_qa_tone_divergence_signal(self):
        report = self.engine.generate_transcript_signal("NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA)
        self.assertEqual(report.signal, SIGNAL_BEARISH)
        self.assertEqual(report.prepared_remarks_sentiment, 1.0)
        self.assertEqual(report.qa_session_sentiment, round(-7 / 9, 4))
        self.assertEqual(report.qa_tone_divergence, round(round(-7 / 9, 4) - 1.0, 4))

    def test_overall_sentiment_pools_counts_rather_than_averaging_sections(self):
        # Pooled: 7 positive, 8 negative -> -1/15. The mean of the two section
        # scores would instead be about +0.11, so this pins the documented method.
        report = self.engine.generate_transcript_signal("NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA)
        self.assertEqual(report.overall_net_sentiment, round(-1 / 15, 4))

    def test_bullish_earnings_tone_signal(self):
        report = self.engine.generate_transcript_signal("AAPL", "Q1 2026", BULLISH_PREP, BULLISH_QA)
        self.assertEqual(report.signal, SIGNAL_BULLISH)
        self.assertEqual(report.overall_net_sentiment, round(9 / 13, 4))
        self.assertEqual(report.overall_uncertainty_ratio_pct, 0.0)

    def test_uncertainty_gate_blocks_bullish_signal(self):
        # Identical polarity to the bullish fixture, but hedged language pushes the
        # uncertainty ratio above the 1.5% ceiling: the documented uncertainty
        # condition must veto the bullish signal.
        report = self.engine.generate_transcript_signal("AAPL", "Q1 2026", BULLISH_PREP, BULLISH_QA_HEDGED)
        self.assertGreater(report.overall_uncertainty_ratio_pct, 1.5)
        self.assertGreater(report.overall_net_sentiment, 0.40)
        self.assertGreaterEqual(report.qa_tone_divergence, -0.15)
        self.assertEqual(report.signal, SIGNAL_NEUTRAL)

        # Raising only the uncertainty ceiling restores the bullish signal, which
        # pins the veto on that gate rather than on sentiment or divergence.
        tolerant = EarningsTranscriptNlpEngine(max_uncertainty_ratio_pct=10.0)
        self.assertEqual(
            tolerant.generate_transcript_signal("AAPL", "Q1 2026", BULLISH_PREP, BULLISH_QA_HEDGED).signal,
            SIGNAL_BULLISH,
        )

    def test_short_sections_yield_insufficient_data_not_a_trade(self):
        # Regression: a one-word "section" pair used to produce a full-strength
        # BEARISH_QA_DIVERGENCE (divergence -2.0) off two lexicon hits.
        report = self.engine.generate_transcript_signal("XYZ", "Q1 2026", "strong", "decline")
        self.assertEqual(report.signal, SIGNAL_INSUFFICIENT)

    def test_missing_qa_section_yields_insufficient_data(self):
        report = self.engine.generate_transcript_signal("XYZ", "Q1 2026", BEARISH_PREP, "")
        self.assertEqual(report.signal, SIGNAL_INSUFFICIENT)

    def test_divergence_exactly_at_threshold_is_not_bearish(self):
        # prepared = +1.0 (6 pos), Q&A = +0.6923 pooled? Use explicit thresholds:
        # observed divergence for these fixtures is far below -0.15, so pin the
        # strict inequality by moving the threshold onto the observed value.
        report = self.engine.generate_transcript_signal("NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA)
        observed = report.qa_tone_divergence
        at_threshold = EarningsTranscriptNlpEngine(bearish_divergence_threshold=observed)
        just_below = EarningsTranscriptNlpEngine(bearish_divergence_threshold=observed + 0.0001)
        self.assertNotEqual(at_threshold.generate_transcript_signal("NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA).signal, SIGNAL_BEARISH)
        self.assertEqual(just_below.generate_transcript_signal("NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA).signal, SIGNAL_BEARISH)

    def test_bearish_divergence_outranks_bullish_tone(self):
        # Prepared remarks strong enough for the bullish gate, Q&A tone collapses.
        report = self.engine.generate_transcript_signal("AAPL", "Q1 2026", BULLISH_PREP, BEARISH_QA)
        self.assertLess(report.qa_tone_divergence, -0.15)
        self.assertEqual(report.signal, SIGNAL_BEARISH)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = EarningsTranscriptNlpEngine()

    def test_blank_ticker_and_quarter_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_transcript_signal("   ", "Q1 2026", BEARISH_PREP, BEARISH_QA)
        with self.assertRaises(ValueError):
            self.engine.generate_transcript_signal("NVDA", "", BEARISH_PREP, BEARISH_QA)

    def test_non_string_transcript_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.generate_transcript_signal("NVDA", "Q1 2026", None, BEARISH_QA)

    def test_naive_publication_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.generate_transcript_signal(
                "NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA,
                transcript_published_at=datetime(2026, 2, 26, 21, 30),
            )

    def test_aware_publication_timestamp_is_carried_through(self):
        stamp = datetime(2026, 2, 26, 21, 30, tzinfo=timezone(timedelta(hours=-5)))
        report = self.engine.generate_transcript_signal(
            "NVDA", "Q1 2026", BEARISH_PREP, BEARISH_QA, transcript_published_at=stamp,
        )
        self.assertEqual(report.transcript_published_at, stamp)

    def test_invalid_thresholds_rejected(self):
        with self.assertRaises(ValueError):
            EarningsTranscriptNlpEngine(bearish_divergence_threshold=0.15)
        with self.assertRaises(ValueError):
            EarningsTranscriptNlpEngine(bullish_sentiment_threshold=1.5)
        with self.assertRaises(ValueError):
            EarningsTranscriptNlpEngine(max_uncertainty_ratio_pct=0.0)

    def test_string_lexicon_rejected(self):
        with self.assertRaises(TypeError):
            EarningsTranscriptNlpEngine(positive_words="strong")

    def test_empty_lexicon_rejected(self):
        with self.assertRaises(ValueError):
            EarningsTranscriptNlpEngine(negative_words=["", "  "])


if __name__ == "__main__":
    unittest.main()
