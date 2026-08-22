"""
Unit and regression tests for CentralBankNLPEngine.
Tests sentence-boundary-isolated negation, multi-word phrase matching,
statement diffing, uncertainty scoring, and edge cases.
"""

import unittest
from central_bank_nlp_engine import CentralBankNLPEngine, SentimentResult, StatementComparison


class TestCentralBankNLPEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CentralBankNLPEngine(negation_window=3)

    def test_pure_hawkish(self):
        text = "The committee decided to hike rates due to strong inflationary pressures."
        res = self.engine.analyze_sentiment(text)
        # "hike rates" (phrase) or "hike", "strong", "inflationary pressures" (phrase)
        self.assertGreater(res.hawkish_count, 0)
        self.assertEqual(res.dovish_count, 0)
        self.assertAlmostEqual(res.net_score, 1.0)
        self.assertGreater(res.hawkish_density, 0.0)

    def test_pure_dovish(self):
        text = "Policy will remain accommodative given the weak economic slowdown."
        res = self.engine.analyze_sentiment(text)
        self.assertEqual(res.hawkish_count, 0)
        self.assertGreater(res.dovish_count, 0)
        self.assertAlmostEqual(res.net_score, -1.0)
        self.assertGreater(res.dovish_density, 0.0)

    def test_negation_inversion(self):
        # "not" is within 3 words of "hike rates"
        # "less" is within 3 words of "accommodative"
        text = "We will not hike rates. The policy is less accommodative."
        res = self.engine.analyze_sentiment(text)
        
        # 'hike rates' is hawkish, but negated -> counts as dovish
        # 'accommodative' is dovish, but negated -> counts as hawkish
        self.assertEqual(res.hawkish_count, 1)  # from negated 'accommodative'
        self.assertEqual(res.dovish_count, 1)   # from negated 'hike rates'
        self.assertAlmostEqual(res.net_score, 0.0)

    def test_negation_out_of_window(self):
        # "not" is 4 words away from "hike" (window is 3)
        text = "We will not be looking to hike."
        res = self.engine.analyze_sentiment(text)
        
        self.assertEqual(res.hawkish_count, 1)
        self.assertEqual(res.dovish_count, 0)

    def test_sentence_boundary_isolation_for_negation(self):
        """
        CRITICAL TEST: Negation in sentence 1 must NOT cross over period/sentence boundary
        to negate words in sentence 2.
        """
        # Sentence 1 has "not". Sentence 2 has "strong labor market".
        # "not" at end of S1 must NOT negate "strong" in S2!
        text = "We will not ease policy. Economic growth remains strong and resilient."
        res = self.engine.analyze_sentiment(text)
        
        # S1: "not ease policy" -> ease is dovish, negated -> 1 hawkish
        # S2: "strong", "resilient" -> 2 hawkish (NOT negated!)
        # Total: 3 hawkish, 0 dovish -> net score = +1.0
        self.assertEqual(res.hawkish_count, 3)
        self.assertEqual(res.dovish_count, 0)
        self.assertAlmostEqual(res.net_score, 1.0)

    def test_multi_word_phrase_matching(self):
        """Verify that multi-word policy phrases are recognized atomically."""
        text = "The Federal Reserve initiated quantitative tightening to counter elevated inflation."
        res = self.engine.analyze_sentiment(text)
        self.assertIn("quantitative tightening", res.matched_hawkish)
        self.assertIn("elevated inflation", res.matched_hawkish)
        self.assertAlmostEqual(res.net_score, 1.0)

    def test_dovish_phrase_matching(self):
        """Verify dovish multi-word phrases like 'labor market slack' and 'downward pressure'."""
        text = "There is significant labor market slack creating downward pressure on wages."
        res = self.engine.analyze_sentiment(text)
        self.assertIn("labor market slack", res.matched_dovish)
        self.assertIn("downward pressure", res.matched_dovish)
        self.assertAlmostEqual(res.net_score, -1.0)

    def test_uncertainty_and_hedging_detection(self):
        """Verify detection of data-dependence and policy uncertainty terms."""
        text = "The economic outlook is highly uncertain and decisions will remain data-dependent."
        res = self.engine.analyze_sentiment(text)
        self.assertIn("uncertain", res.matched_uncertainty)
        self.assertIn("data-dependent", res.matched_uncertainty)
        self.assertEqual(res.uncertainty_count, 2)

    def test_statement_diff_and_sentiment_shock(self):
        """Verify sequential statement comparison and delta shock signal."""
        stmt_prev = "The Committee expects further firming of monetary policy to lower elevated inflation."
        stmt_curr = "The Committee decided to pause and maintain accommodation given cooling labor market conditions."
        
        comp = self.engine.compare_statements(stmt_prev, stmt_curr)
        
        self.assertGreater(comp.previous_score, 0.0)  # Hawkish prior
        self.assertLess(comp.current_score, 0.0)      # Dovish current
        self.assertLess(comp.score_delta, 0.0)        # Dovish shock / shift (negative delta)
        self.assertGreater(comp.jaccard_similarity, 0.0)
        self.assertLess(comp.jaccard_similarity, 1.0)
        self.assertIn("maintain accommodation", comp.added_dovish)
        self.assertIn("further firming", comp.removed_hawkish)

    def test_empty_and_whitespace_input(self):
        """Empty or neutral input returns safe zero scores."""
        res_empty = self.engine.analyze_sentiment("")
        self.assertEqual(res_empty.total_words, 0)
        self.assertEqual(res_empty.net_score, 0.0)

        res_neutral = self.engine.analyze_sentiment("The meeting took place on Wednesday in Washington.")
        self.assertEqual(res_neutral.hawkish_count, 0)
        self.assertEqual(res_neutral.dovish_count, 0)
        self.assertEqual(res_neutral.net_score, 0.0)

    def test_similarity_measures(self):
        """Test Jaccard and Cosine similarity functions."""
        t1 = "inflation remains elevated and rates must increase"
        t2 = "inflation remains elevated and rates must increase"
        t3 = "unemployment increased while growth slowed"

        self.assertAlmostEqual(self.engine.calculate_jaccard_similarity(t1, t2), 1.0)
        self.assertAlmostEqual(self.engine.calculate_cosine_similarity(t1, t2), 1.0)
        self.assertLess(self.engine.calculate_jaccard_similarity(t1, t3), 0.5)


    def test_decimal_number_is_not_a_sentence_boundary(self):
        """
        REGRESSION: a decimal such as "0.25" must not create a spurious sentence
        boundary. Before the fix the text below split into "...sees no 0" / "25
        percentage point hike...", which severed the negation cue and scored the
        sentence hawkish (+1.0) instead of dovish (-1.0).
        """
        engine = CentralBankNLPEngine(negation_window=6)
        text = "The Committee sees no 0.25 percentage point hike this year."

        self.assertEqual(len(engine._split_sentences(text)), 1)
        res = engine.analyze_sentiment(text)
        self.assertIn("negated(hike)", res.matched_dovish)
        self.assertEqual(res.hawkish_count, 0)
        self.assertAlmostEqual(res.net_score, -1.0)

    def test_dotted_abbreviation_is_not_a_sentence_boundary(self):
        """Dotted abbreviations ("U.S.", "e.g.") must not fragment a sentence."""
        text = "Growth in the U.S. is not strong. Inflation remains elevated."
        sentences = self.engine._split_sentences(text)

        self.assertEqual(sentences,
                         ["Growth in the U.S. is not strong", "Inflation remains elevated"])
        res = self.engine.analyze_sentiment(text)
        # "not strong" -> negated hawkish -> dovish; "elevated" in sentence 2 unaffected.
        self.assertIn("negated(strong)", res.matched_dovish)
        self.assertIn("elevated", res.matched_hawkish)

    def test_densities_including_uncertainty(self):
        """Densities are mentions per 1,000 words, uncertainty included."""
        text = "Inflation is elevated and the outlook is uncertain."
        res = self.engine.analyze_sentiment(text)

        # 8 tokens; 1 hawkish ("elevated"), 0 dovish, 1 uncertainty term.
        self.assertEqual(res.total_words, 8)
        self.assertAlmostEqual(res.hawkish_density, 125.0)
        self.assertAlmostEqual(res.dovish_density, 0.0)
        self.assertAlmostEqual(res.uncertainty_density, 125.0)

    def test_longest_phrase_match_wins_over_shorter_opposite_phrase(self):
        """
        Phrase matching is longest-first across BOTH lexicons, so a longer dovish
        collocation is not pre-empted by a shorter overlapping hawkish one.
        """
        self.engine.dovish_phrases.append("rate hike pause")
        res = self.engine.analyze_sentiment("The Committee signalled a rate hike pause.")

        self.assertIn("rate hike pause", res.matched_dovish)
        self.assertNotIn("rate hike", res.matched_hawkish)
        self.assertAlmostEqual(res.net_score, -1.0)

    def test_non_string_input_is_rejected(self):
        """
        Non-text input must raise, never be scored as a neutral 0.0 stance that a
        downstream macro strategy could mistake for a balanced statement.
        """
        with self.assertRaises(TypeError):
            self.engine.analyze_sentiment(["hike rates"])
        with self.assertRaises(TypeError):
            self.engine.analyze_sentiment(42)

        res_none = self.engine.analyze_sentiment(None)
        self.assertEqual(res_none.total_words, 0)
        self.assertEqual(res_none.net_score, 0.0)

    def test_invalid_negation_window_rejected(self):
        """An invalid negation window must fail loudly rather than be clamped."""
        with self.assertRaises(ValueError):
            CentralBankNLPEngine(negation_window=0)
        with self.assertRaises(ValueError):
            CentralBankNLPEngine(negation_window=-3)
        with self.assertRaises(TypeError):
            CentralBankNLPEngine(negation_window="3")


if __name__ == "__main__":
    unittest.main()
