"""Tests for the web-scraped sentiment pipeline.

Expected values are derived by hand from the documented formulas rather than by
re-running the implementation. Baselines are built by :meth:`_baseline`, which
constructs a 21-observation sample whose mean is exactly ``centre`` and whose
*sample* standard deviation is exactly 0.125:

    ten values at ``centre - 0.125``, one at ``centre``, ten at ``centre + 0.125``
    sum of squared deviations = 10*(0.015625) + 0 + 10*(0.015625) = 0.3125
    sample variance = 0.3125 / (21 - 1) = 0.015625  ->  sigma = 0.125

so a document mean of ``m`` standardises to ``Z = (m - centre) / 0.125``. The
0.125 spread is an exact binary fraction, so the boundary tests below compare
against a Z that is exact rather than merely close.
"""

import datetime
import os
import tempfile
import unittest

from web_scraped_sentiment_data_pipeline import (
    FILING_SPECIFIC_TERMS,
    LM_NEGATIVE_WORDS,
    LM_POSITIVE_WORDS,
    NEGATION_WINDOW,
    RawScrapedItem,
    ScoredSentimentItem,
    SentimentPipelineError,
    SentimentSignal,
    VALID_SCORE_METRICS,
    WebScrapedSentimentPipelineEngine,
    load_lm_lexicon_from_master_dictionary,
)

UTC = datetime.timezone.utc
SIGNAL_DATE = datetime.date(2026, 3, 10)
MIDDAY = datetime.datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def _baseline(centre: float = 0.0):
    """21 observations with mean ``centre`` and sample standard deviation 0.125."""
    return [centre - 0.125] * 10 + [centre] + [centre + 0.125] * 10


class LexiconProvenanceTests(unittest.TestCase):
    """The bundled word lists must be Loughran-McDonald, as documented."""

    def test_word_lists_are_lowercase_and_disjoint(self):
        for word in LM_POSITIVE_WORDS | LM_NEGATIVE_WORDS:
            self.assertEqual(word, word.lower(), f"{word!r} is not lowercased")
            self.assertTrue(word.isalpha(), f"{word!r} is not a plain alphabetic token")
        self.assertEqual(set(), LM_POSITIVE_WORDS & LM_NEGATIVE_WORDS)

    def test_non_lm_market_vernacular_is_not_scored(self):
        """Regression: the previous lists attributed non-LM words to LM.

        None of these appear in the LM Positive or Negative categories.
        ``growth``, ``revenue``, ``dividend``, ``buy``, ``record``, ``bullish``,
        ``rally``, ``surge``, ``slump``, ``bearish``, ``plunge`` and ``sell`` are
        uncategorised in the Master Dictionary; scoring them as LM terms
        misattributes the dictionary.
        """
        for word in ("growth", "revenue", "dividend", "buy", "record", "momentum",
                     "bullish", "rally", "surge", "beat", "upbeat", "expansion",
                     "slump", "bearish", "plunge", "downfall", "scandal", "sell"):
            self.assertNotIn(word, LM_POSITIVE_WORDS, f"{word!r} is not LM-Positive")
            self.assertNotIn(word, LM_NEGATIVE_WORDS, f"{word!r} is not LM-Negative")

    def test_words_lm_classifies_outside_positive_negative_are_not_scored(self):
        """``risk`` is LM *Uncertainty*, ``lawsuit`` is *Litigious*, ``drop`` is
        *Interesting*. None of the three is LM-Negative, so none may be counted
        as negative sentiment."""
        for word in ("risk", "lawsuit", "drop"):
            self.assertNotIn(word, LM_NEGATIVE_WORDS)
            self.assertNotIn(word, LM_POSITIVE_WORDS)

    def test_engine_lexicons_are_immutable_snapshots(self):
        """Mutating the module-level sets must not reach an engine already built."""
        engine = WebScrapedSentimentPipelineEngine()
        self.assertIsInstance(engine.positive_words, frozenset)
        self.assertIsInstance(engine.negative_words, frozenset)
        snapshot = set(LM_POSITIVE_WORDS)
        try:
            LM_POSITIVE_WORDS.add("zzzinjected")
            self.assertNotIn("zzzinjected", engine.positive_words)
        finally:
            LM_POSITIVE_WORDS.clear()
            LM_POSITIVE_WORDS.update(snapshot)

    def test_filing_specific_terms_are_excluded_by_default(self):
        engine = WebScrapedSentimentPipelineEngine()
        for word in FILING_SPECIFIC_TERMS:
            self.assertNotIn(word, engine.positive_words)
            self.assertNotIn(word, engine.negative_words)
        # ...and are restored when the caller opts back in.
        permissive = WebScrapedSentimentPipelineEngine(exclude_filing_specific_terms=False)
        self.assertIn("despite", permissive.positive_words)
        self.assertIn("volatility", permissive.negative_words)


class CleanTextTests(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def test_strips_html_urls_and_cashtags(self):
        raw = ("<div><p>Check $AAPL results at https://example.com/aapl! "
               "Strong improvement &amp; profitable quarter.</p></div>")
        clean = self.engine.clean_text(raw)
        self.assertEqual("check aapl results at strong improvement profitable quarter", clean)

    def test_script_body_is_removed_not_just_its_tags(self):
        raw = "<p>profitable</p><script>var loss = 'failure failure';</script><p>strong</p>"
        clean = self.engine.clean_text(raw)
        self.assertEqual("profitable strong", clean)
        self.assertNotIn("failure", clean)

    def test_html_entities_do_not_leave_residue_tokens(self):
        self.assertEqual("profit loss", self.engine.clean_text("profit &amp; loss"))
        self.assertNotIn("amp", self.engine.clean_text("profit &amp; loss").split())

    def test_bare_www_urls_are_removed(self):
        self.assertEqual("see improvement", self.engine.clean_text("see www.spam.example/x improvement"))

    def test_double_escaped_entities_are_fully_unescaped(self):
        """A scraped page that escapes already-escaped content yields "&amp;amp;".
        A single unescape pass leaves the token "amp" behind."""
        self.assertEqual("profit loss", self.engine.clean_text("profit&amp;amp;loss"))
        self.assertEqual("profit loss", self.engine.clean_text("profit&amp;amp;amp;loss"))

    def test_unclosed_script_does_not_spill_its_body(self):
        """A truncated scrape can end mid-element. Stripping only the opening tag
        would leave the script body in the token stream."""
        self.assertEqual("", self.engine.clean_text("<script>loss loss"))
        self.assertEqual("strong", self.engine.clean_text("strong <style>loss"))

    def test_underscores_are_split_not_glued(self):
        self.assertEqual(["record", "loss"], self.engine.clean_text("record_loss").split())

    def test_empty_and_whitespace_input(self):
        self.assertEqual("", self.engine.clean_text(""))
        self.assertEqual("", self.engine.clean_text("   \n\t "))
        self.assertEqual("", self.engine.clean_text("<p></p> !!! ---"))

    def test_non_string_input_raises(self):
        for bad in (None, 123, b"bytes", ["loss"]):
            with self.assertRaises(SentimentPipelineError):
                self.engine.clean_text(bad)


class ScoreTextTests(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def test_all_positive_saturates_at_plus_one(self):
        # 3 positive, 0 negative -> (3-0)/3 = +1.0
        self.assertEqual((3, 0, 1.0), self.engine.score_text("strong improvement profitable"))

    def test_all_negative_saturates_at_minus_one(self):
        # 0 positive, 3 negative -> (0-3)/3 = -1.0
        self.assertEqual((0, 3, -1.0), self.engine.score_text("loss litigation bankruptcy"))

    def test_mixed_polarity_is_hand_computable(self):
        # 3 positive, 1 negative -> (3-1)/4 = +0.5
        self.assertEqual((3, 1, 0.5), self.engine.score_text("strong improvement profitable loss"))

    def test_no_lexicon_match_scores_zero_with_zero_evidence(self):
        self.assertEqual((0, 0, 0.0), self.engine.score_text("the quarterly filing was submitted"))
        self.assertEqual((0, 0, 0.0), self.engine.score_text(""))

    def test_negation_flips_polarity(self):
        # "not profitable": profitable is 1 token after the negator, within the window.
        self.assertEqual((0, 1, -1.0), self.engine.score_text("not profitable"))
        self.assertEqual((1, 0, 1.0), self.engine.score_text("no loss"))

    def test_negation_does_not_reach_beyond_the_window(self):
        distance = NEGATION_WINDOW + 1
        text = "not " + ("filler " * (distance - 1)) + "profitable"
        pos, neg, score = self.engine.score_text(text)
        self.assertEqual((1, 0, 1.0), (pos, neg, score))

    def test_negation_regression_against_unnegated_scoring(self):
        """The previous engine scored 'not profitable' as +1.0."""
        self.assertLess(self.engine.score_text("not profitable")[2], 0.0)

    def test_score_text_rejects_non_string(self):
        with self.assertRaises(SentimentPipelineError):
            self.engine.score_text(None)


class ProcessFeedTests(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def test_records_carry_the_evidence_behind_the_score(self):
        item = RawScrapedItem("1", "NEWS", MIDDAY, "aapl", "strong improvement and a loss")
        scored, = self.engine.process_scraped_feed([item])
        self.assertEqual("AAPL", scored.ticker)          # normalised to upper case
        self.assertEqual(2, scored.positive_count)
        self.assertEqual(1, scored.negative_count)
        self.assertEqual(3, scored.matched_word_count)

    def test_token_counts_and_lm_tone_are_hand_computable(self):
        # "strong improvement and a loss" -> 5 tokens, 2 positive, 1 negative
        # polarity = (2-1)/3 = 0.3333 ; lm_tone = (2-1)/5 = 0.2
        item = RawScrapedItem("1", "NEWS", MIDDAY, "AAPL", "strong improvement and a loss")
        scored, = self.engine.process_scraped_feed([item])
        self.assertEqual(5, scored.total_tokens)
        self.assertAlmostEqual(1 / 3, scored.raw_sentiment_score, places=4)
        self.assertAlmostEqual(0.2, scored.lm_tone, places=6)

    def test_lm_tone_separates_intensity_that_polarity_collapses(self):
        """Two documents both score polarity +1.0; only lm_tone distinguishes them."""
        short = RawScrapedItem("s", "NEWS", MIDDAY, "AAPL", "strong improvement")
        buried = RawScrapedItem(
            "b", "NEWS", MIDDAY, "AAPL",
            "strong improvement " + " ".join(["filler"] * 98),
        )
        a, b = self.engine.process_scraped_feed([short, buried])
        self.assertEqual(a.raw_sentiment_score, b.raw_sentiment_score)
        self.assertAlmostEqual(1.0, a.lm_tone, places=6)      # 2 / 2 tokens
        self.assertAlmostEqual(0.02, b.lm_tone, places=6)     # 2 / 100 tokens

    def test_syndicated_reposts_are_marked_as_duplicates(self):
        body = "<p>Acme reports strong improvement.</p>"
        items = [
            RawScrapedItem("wire-1", "NEWS", MIDDAY, "ACME", body),
            RawScrapedItem("wire-2", "NEWS", MIDDAY, "ACME", "Acme reports strong improvement."),
            RawScrapedItem("wire-3", "NEWS", MIDDAY, "ACME", "ACME REPORTS STRONG IMPROVEMENT"),
        ]
        scored = self.engine.process_scraped_feed(items)
        self.assertEqual([False, True, True], [s.is_duplicate for s in scored])
        self.assertEqual(["wire-1", "wire-1"], [s.duplicate_of for s in scored[1:]])

    def test_the_surviving_copy_is_the_earliest_regardless_of_list_order(self):
        """The survivor's timestamp decides which day the document lands on, so
        keeping whichever copy the caller listed first can push a document past
        the point-in-time cutoff."""
        early = RawScrapedItem("early", "NEWS", datetime.datetime(2026, 3, 10, 8, tzinfo=UTC),
                               "ACME", "Acme reports strong improvement.")
        late = RawScrapedItem("late", "NEWS", datetime.datetime(2026, 3, 10, 18, tzinfo=UTC),
                              "ACME", "Acme reports strong improvement.")
        for order in ([early, late], [late, early]):
            with self.subTest(order=[i.item_id for i in order]):
                survivors = [s for s in self.engine.process_scraped_feed(order) if not s.is_duplicate]
                self.assertEqual(["early"], [s.item_id for s in survivors])

    def test_a_next_day_repost_does_not_become_the_survivor(self):
        same_day = RawScrapedItem("d1", "NEWS", datetime.datetime(2026, 3, 10, 20, tzinfo=UTC),
                                  "ACME", "strong improvement gains")
        next_day = RawScrapedItem("d2", "NEWS", datetime.datetime(2026, 3, 11, 9, tzinfo=UTC),
                                  "ACME", "strong improvement gains")
        survivors = [s for s in self.engine.process_scraped_feed([next_day, same_day])
                     if not s.is_duplicate]
        self.assertEqual(["d1"], [s.item_id for s in survivors])
        self.assertEqual(datetime.date(2026, 3, 10), survivors[0].timestamp.date())

    def test_identical_text_for_different_tickers_is_not_a_duplicate(self):
        items = [
            RawScrapedItem("1", "NEWS", MIDDAY, "AAPL", "strong improvement"),
            RawScrapedItem("2", "NEWS", MIDDAY, "MSFT", "strong improvement"),
        ]
        self.assertEqual([False, False], [s.is_duplicate for s in self.engine.process_scraped_feed(items)])

    def test_empty_documents_are_not_collapsed_into_one_duplicate_group(self):
        items = [RawScrapedItem(str(i), "NEWS", MIDDAY, "AAPL", "") for i in range(3)]
        self.assertEqual([False, False, False], [s.is_duplicate for s in self.engine.process_scraped_feed(items)])

    def test_malformed_item_fails_the_batch(self):
        with self.assertRaises(SentimentPipelineError):
            self.engine.process_scraped_feed([RawScrapedItem("1", "NEWS", MIDDAY, "AAPL", "ok"), "not-an-item"])
        with self.assertRaises(SentimentPipelineError):
            self.engine.process_scraped_feed("a string is a sequence but not a batch")


class RawItemValidationTests(unittest.TestCase):
    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(SentimentPipelineError):
            RawScrapedItem("1", "NEWS", datetime.datetime(2026, 3, 10, 12, 0), "AAPL", "text")

    def test_implausible_ticker_is_rejected(self):
        for ticker in ("", "   ", "not a ticker", "$$$"):
            with self.assertRaises(SentimentPipelineError):
                RawScrapedItem("1", "NEWS", MIDDAY, ticker, "text")

    def test_non_string_text_is_rejected(self):
        with self.assertRaises(SentimentPipelineError):
            RawScrapedItem("1", "NEWS", MIDDAY, "AAPL", None)

    def test_blank_identifiers_are_rejected(self):
        with self.assertRaises(SentimentPipelineError):
            RawScrapedItem("  ", "NEWS", MIDDAY, "AAPL", "text")
        with self.assertRaises(SentimentPipelineError):
            RawScrapedItem("1", "", MIDDAY, "AAPL", "text")


class SignalGenerationTests(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def _docs(self, texts, ticker="NVDA", when=MIDDAY):
        items = [RawScrapedItem(f"i{n}", "NEWS", when, ticker, t) for n, t in enumerate(texts)]
        return self.engine.process_scraped_feed(items)

    def test_long_signal_z_is_hand_computable(self):
        # Three documents each scoring +1.0 -> mean +1.0.
        # Baseline centre 0.0, sigma 0.125 -> Z = (1.0 - 0.0) / 0.125 = 8.0
        scored = self._docs(["strong improvement"] * 1 + ["profitable gains", "successful benefit"])
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual("LONG", signal.direction)
        self.assertAlmostEqual(1.0, signal.current_sentiment_mean, places=4)
        self.assertAlmostEqual(0.0, signal.baseline_mean, places=6)
        self.assertAlmostEqual(0.125, signal.baseline_std, places=6)
        self.assertAlmostEqual(8.0, signal.sentiment_zscore, places=2)
        self.assertEqual(3, signal.items_considered)
        self.assertEqual(21, signal.baseline_observations)
        self.assertEqual(1.0, signal.confidence_score)

    def test_short_signal_z_is_hand_computable(self):
        # Three documents each scoring -1.0 -> mean -1.0 -> Z = -8.0
        scored = self._docs(["loss litigation", "bankruptcy fraud", "failure damages"])
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual("SHORT", signal.direction)
        self.assertAlmostEqual(-8.0, signal.sentiment_zscore, places=2)

    def test_neutral_when_inside_the_band(self):
        # Three balanced documents -> mean 0.0. Baseline centre -0.125 -> Z = 1.0 < 1.5
        scored = self._docs(["strong loss", "profitable litigation", "benefit damages"])
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(-0.125))
        self.assertEqual("NEUTRAL", signal.direction)
        self.assertAlmostEqual(1.0, signal.sentiment_zscore, places=2)

    def test_threshold_is_inclusive(self):
        # mean 0.0, baseline centre -0.1875, sigma 0.125 -> Z = 1.5 exactly -> LONG
        scored = self._docs(["strong loss", "profitable litigation", "benefit damages"])
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(-0.1875))
        self.assertEqual("LONG", signal.direction)

    def test_banding_uses_the_unrounded_zscore(self):
        """Z = 1.4951 rounds to 1.5 for display but must still band NEUTRAL."""
        scored = self._docs(["strong loss", "profitable litigation", "benefit damages"])
        # centre -0.1868875 / sigma 0.125 -> Z = 1.4951
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(-0.1868875))
        self.assertEqual(1.5, signal.sentiment_zscore)   # the reported figure is rounded
        self.assertEqual("NEUTRAL", signal.direction)    # the decision is not

    def test_other_tickers_are_ignored(self):
        scored = self._docs(["loss litigation"] * 3, ticker="INTC")
        scored += self._docs(["strong improvement", "profitable gains", "successful benefit"])
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(3, signal.items_considered)
        self.assertEqual("LONG", signal.direction)


class PointInTimeTests(unittest.TestCase):
    """Regression cover for the look-ahead defect: ``signal_date`` is a cutoff."""

    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def _feed(self, when, texts=("loss litigation", "bankruptcy fraud", "failure damages")):
        items = [RawScrapedItem(f"i{n}", "NEWS", when, "NVDA", t) for n, t in enumerate(texts)]
        return self.engine.process_scraped_feed(items)

    def test_documents_after_the_cutoff_are_excluded_and_counted(self):
        tomorrow = datetime.datetime(2026, 3, 11, 0, 0, tzinfo=UTC)
        scored = self._feed(tomorrow)
        signal = self.engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(3, signal.future_items_excluded)
        self.assertEqual(0, signal.items_considered)
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)

    def test_future_documents_do_not_contaminate_the_mean(self):
        """The old engine averaged the future documents in and emitted SHORT."""
        today = self._feed(MIDDAY, ("strong improvement", "profitable gains", "successful benefit"))
        tomorrow = datetime.datetime(2026, 3, 11, 9, 0, tzinfo=UTC)
        future = [
            ScoredSentimentItem(f"f{n}", "NVDA", tomorrow, 0, 2, -1.0, "loss litigation",
                                total_tokens=2, matched_word_count=2, lm_tone=-1.0)
            for n in range(20)
        ]
        signal = self.engine.generate_ticker_signals(today + future, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(20, signal.future_items_excluded)
        self.assertEqual(3, signal.items_considered)
        self.assertAlmostEqual(1.0, signal.current_sentiment_mean, places=4)
        self.assertEqual("LONG", signal.direction)

    def test_last_instant_of_the_signal_day_is_included(self):
        end_of_day = datetime.datetime(2026, 3, 10, 23, 59, 59, tzinfo=UTC)
        signal = self.engine.generate_ticker_signals(
            self._feed(end_of_day), "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(0, signal.future_items_excluded)
        self.assertEqual(3, signal.items_considered)

    def test_cutoff_is_resolved_in_the_session_timezone(self):
        """20:00 New York on the 10th is 00:00 UTC on the 11th: inside the NY day,
        outside the UTC day."""
        stamp = datetime.datetime(2026, 3, 11, 0, 0, tzinfo=UTC)
        scored = self._feed(stamp)
        utc_engine = WebScrapedSentimentPipelineEngine()
        self.assertEqual(3, utc_engine.generate_ticker_signals(
            scored, "NVDA", SIGNAL_DATE, _baseline(0.0)).future_items_excluded)

        ny_engine = WebScrapedSentimentPipelineEngine(
            session_timezone=datetime.timezone(datetime.timedelta(hours=-4)))
        ny_signal = ny_engine.generate_ticker_signals(scored, "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(0, ny_signal.future_items_excluded)
        self.assertEqual(3, ny_signal.items_considered)

    def test_documents_before_the_window_are_excluded_as_stale(self):
        last_week = datetime.datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
        signal = self.engine.generate_ticker_signals(
            self._feed(last_week), "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(3, signal.stale_items_excluded)
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)

    def test_wider_aggregation_window_retains_older_documents(self):
        engine = WebScrapedSentimentPipelineEngine(aggregation_window_days=30)
        last_week = datetime.datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
        items = [RawScrapedItem(f"i{n}", "NEWS", last_week, "NVDA", t)
                 for n, t in enumerate(("loss litigation", "bankruptcy fraud", "failure damages"))]
        signal = engine.generate_ticker_signals(
            engine.process_scraped_feed(items), "NVDA", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(0, signal.stale_items_excluded)
        self.assertEqual("SHORT", signal.direction)

    def test_naive_scored_timestamp_raises(self):
        naive = ScoredSentimentItem("x", "NVDA", datetime.datetime(2026, 3, 10, 12, 0),
                                    2, 0, 1.0, "strong improvement",
                                    total_tokens=2, matched_word_count=2)
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals([naive], "NVDA", SIGNAL_DATE, _baseline(0.0))


class InsufficientDataTests(unittest.TestCase):
    """Not-measurable must never be reported as a confident NEUTRAL."""

    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def _scored(self, texts, when=MIDDAY):
        items = [RawScrapedItem(f"i{n}", "NEWS", when, "MSFT", t) for n, t in enumerate(texts)]
        return self.engine.process_scraped_feed(items)

    def test_empty_feed_is_insufficient_not_neutral(self):
        signal = self.engine.generate_ticker_signals([], "MSFT", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)
        self.assertIsNone(signal.baseline_std)
        self.assertIsNone(signal.current_sentiment_mean)
        self.assertEqual(0.0, signal.confidence_score)
        self.assertTrue(signal.reason)

    def test_single_document_cannot_produce_a_direction(self):
        """One document scoring +1.0 against sigma 0.125 would be Z = +8."""
        signal = self.engine.generate_ticker_signals(
            self._scored(["strong improvement profitable"]), "MSFT", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)

    def test_documents_with_one_matched_word_are_excluded_as_low_evidence(self):
        """A single matched word saturates the polarity at +1.0 regardless of length."""
        signal = self.engine.generate_ticker_signals(
            self._scored([f"the company reported strong numbers in region {n}" for n in range(4)]),
            "MSFT", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(4, signal.low_evidence_items_excluded)
        self.assertEqual(0, signal.duplicate_items_excluded)
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)

    def test_low_evidence_gate_is_configurable(self):
        engine = WebScrapedSentimentPipelineEngine(min_matched_words=1)
        items = [RawScrapedItem(f"i{n}", "NEWS", MIDDAY, "MSFT",
                                f"the company reported strong numbers in region {n}")
                 for n in range(4)]
        signal = engine.generate_ticker_signals(
            engine.process_scraped_feed(items), "MSFT", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(0, signal.low_evidence_items_excluded)
        self.assertEqual("LONG", signal.direction)

    def test_duplicates_are_excluded_from_the_aggregate(self):
        scored = self._scored(["strong improvement profitable"] * 5)
        signal = self.engine.generate_ticker_signals(scored, "MSFT", SIGNAL_DATE, _baseline(0.0))
        self.assertEqual(4, signal.duplicate_items_excluded)
        self.assertEqual(1, signal.items_considered)
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)

    def test_short_baseline_is_insufficient(self):
        signal = self.engine.generate_ticker_signals(
            self._scored(["strong improvement", "profitable gains", "successful benefit"]),
            "MSFT", SIGNAL_DATE, [0.0, 0.1, -0.1, 0.0, 0.05])
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertEqual(5, signal.baseline_observations)
        self.assertIsNone(signal.sentiment_zscore)
        self.assertIsNone(signal.baseline_std)
        # the mean it did compute is still reported, for diagnosis
        self.assertAlmostEqual(1.0, signal.current_sentiment_mean, places=4)

    def test_degenerate_baseline_does_not_fabricate_a_sigma_of_one(self):
        """Regression: the old engine substituted sigma = 1.0 for a constant
        baseline and reported a Z-score against a denominator it invented."""
        signal = self.engine.generate_ticker_signals(
            self._scored(["strong improvement", "profitable gains", "successful benefit"]),
            "MSFT", SIGNAL_DATE, [0.25] * 21)
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)
        self.assertIsNone(signal.baseline_std)
        self.assertAlmostEqual(0.25, signal.baseline_mean, places=6)

    def test_near_zero_baseline_dispersion_is_insufficient(self):
        """Regression: a sigma of ~1e-160 is > 0 and finite, so a bare
        ``> 0`` guard passes it — then reports ``baseline_std`` rounded to 0.0
        alongside a Z of order 1e160 banded as a maximum-conviction LONG."""
        signal = self.engine.generate_ticker_signals(
            self._scored(["strong improvement", "profitable gains", "successful benefit"]),
            "MSFT", SIGNAL_DATE, [1e-160, -1e-160] * 10 + [0.0])
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)
        self.assertIsNone(signal.baseline_std)

    def test_min_baseline_std_is_configurable(self):
        engine = WebScrapedSentimentPipelineEngine(min_baseline_std=1e-200)
        signal = engine.generate_ticker_signals(
            self._scored(["strong improvement", "profitable gains", "successful benefit"]),
            "MSFT", SIGNAL_DATE, [1e-160, -1e-160] * 10 + [0.0])
        self.assertEqual("LONG", signal.direction)

    def test_empty_baseline_does_not_self_reference_into_a_zero_zscore(self):
        """Regression: the old engine set baseline_mean = current_mean when no
        baseline was supplied, yielding Z = 0 and a confident NEUTRAL."""
        signal = self.engine.generate_ticker_signals(
            self._scored(["strong improvement", "profitable gains", "successful benefit"]),
            "MSFT", SIGNAL_DATE, [])
        self.assertEqual("INSUFFICIENT_DATA", signal.direction)
        self.assertIsNone(signal.sentiment_zscore)


class ArgumentValidationTests(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine()

    def test_non_finite_baseline_values_raise(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(SentimentPipelineError):
                self.engine.generate_ticker_signals([], "MSFT", SIGNAL_DATE, _baseline(0.0) + [bad])

    def test_non_numeric_baseline_values_raise(self):
        for bad in ("0.5", None, True):
            with self.assertRaises(SentimentPipelineError):
                self.engine.generate_ticker_signals([], "MSFT", SIGNAL_DATE, [bad] * 21)

    def test_overflowing_baseline_dispersion_raises_rather_than_propagating(self):
        """Regression: squared deviations near the float ceiling raised a bare
        ``OverflowError`` out of the engine."""
        items = [RawScrapedItem(f"i{n}", "NEWS", MIDDAY, "MSFT", t) for n, t in
                 enumerate(("strong improvement", "profitable gains", "successful benefit"))]
        scored = self.engine.process_scraped_feed(items)
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals(
                scored, "MSFT", SIGNAL_DATE, [1e308, -1e308] * 10 + [0.0])

    def test_unrepresentable_window_boundary_raises_cleanly(self):
        """Regression: ``date.max + 1 day`` raised a bare ``OverflowError``."""
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals([], "MSFT", datetime.date.max, _baseline(0.0))
        wide = WebScrapedSentimentPipelineEngine(aggregation_window_days=30)
        with self.assertRaises(SentimentPipelineError):
            wide.generate_ticker_signals([], "MSFT", datetime.date.min, _baseline(0.0))
        # A 1-day window at the minimum date is representable and must not raise.
        self.assertEqual(
            "INSUFFICIENT_DATA",
            self.engine.generate_ticker_signals(
                [], "MSFT", datetime.date.min, _baseline(0.0)).direction,
        )

    def test_datetime_as_signal_date_raises(self):
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals([], "MSFT", MIDDAY, _baseline(0.0))

    def test_blank_target_ticker_raises(self):
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals([], "   ", SIGNAL_DATE, _baseline(0.0))

    def test_bad_scored_item_type_raises(self):
        with self.assertRaises(SentimentPipelineError):
            self.engine.generate_ticker_signals(["nope"], "MSFT", SIGNAL_DATE, _baseline(0.0))

    def test_invalid_configuration_raises(self):
        bad_kwargs = [
            {"zscore_threshold": 0},
            {"zscore_threshold": -1.5},
            {"zscore_threshold": float("nan")},
            {"zscore_threshold": "1.5"},
            {"min_matched_words": 0},
            {"min_items": 0},
            {"min_baseline_observations": 1},
            {"aggregation_window_days": 0},
            {"session_timezone": None},
            {"conviction_saturation_multiple": 0},
            {"positive_words": [], "negative_words": ["loss"]},
            {"positive_words": ["strong"], "negative_words": ["strong"]},
            {"min_baseline_std": 0},
            {"min_baseline_std": -1e-9},
            {"min_baseline_std": float("inf")},
            {"score_metric": "sentiment"},
            {"score_metric": None},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs), self.assertRaises(SentimentPipelineError):
                WebScrapedSentimentPipelineEngine(**kwargs)

    def test_sentiment_pipeline_error_is_a_value_error(self):
        self.assertTrue(issubclass(SentimentPipelineError, ValueError))
        with self.assertRaises(ValueError):
            self.engine.clean_text(None)


class MasterDictionaryLoaderTests(unittest.TestCase):
    HEADER = "Word,Negative,Positive\n"

    def _write(self, body, header=None):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             newline="", encoding="utf-8")
        handle.write((self.HEADER if header is None else header) + body)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_year_markers_are_read_as_membership(self):
        path = self._write("ACHIEVE,0,2009\nABANDON,2009,0\nAARDVARK,0,0\n")
        positive, negative = load_lm_lexicon_from_master_dictionary(path)
        self.assertEqual({"achieve"}, positive)
        self.assertEqual({"abandon"}, negative)

    def test_missing_columns_raise(self):
        path = self._write("ACHIEVE,1\n", header="Word,Sentiment\n")
        with self.assertRaises(SentimentPipelineError):
            load_lm_lexicon_from_master_dictionary(path)

    def test_an_empty_category_raises_rather_than_silently_disabling_scoring(self):
        path = self._write("ACHIEVE,0,2009\n")
        with self.assertRaises(SentimentPipelineError):
            load_lm_lexicon_from_master_dictionary(path)

    def test_loaded_lexicon_drives_the_engine(self):
        path = self._write("ACHIEVE,0,2009\nABANDON,2009,0\n")
        positive, negative = load_lm_lexicon_from_master_dictionary(path)
        engine = WebScrapedSentimentPipelineEngine(positive_words=positive, negative_words=negative)
        self.assertEqual((1, 1, 0.0), engine.score_text("achieve abandon"))
        self.assertEqual((0, 0, 0.0), engine.score_text("strong improvement"))


class ScoreMetricTests(unittest.TestCase):
    """The aggregate may be taken over polarity or over LM tone."""

    TEXTS = ("strong improvement", "profitable gains", "successful benefit")

    def _scored(self, engine):
        items = [RawScrapedItem(f"i{n}", "NEWS", MIDDAY, "AAPL", t)
                 for n, t in enumerate(self.TEXTS)]
        return engine.process_scraped_feed(items)

    def test_declared_metrics_are_the_supported_ones(self):
        self.assertEqual(("polarity", "lm_tone"), VALID_SCORE_METRICS)

    def test_polarity_is_the_default(self):
        self.assertEqual("polarity", WebScrapedSentimentPipelineEngine().score_metric)

    def test_lm_tone_aggregate_is_hand_computable(self):
        # Each document is two tokens, both positive: lm_tone = (2-0)/2 = 1.0.
        engine = WebScrapedSentimentPipelineEngine(score_metric="lm_tone")
        signal = engine.generate_ticker_signals(
            self._scored(engine), "AAPL", SIGNAL_DATE, _baseline(0.0))
        self.assertAlmostEqual(1.0, signal.current_sentiment_mean, places=6)
        self.assertAlmostEqual(8.0, signal.sentiment_zscore, places=2)

    def test_the_two_metrics_disagree_where_polarity_saturates(self):
        """Polarity collapses length; lm_tone does not. This is the whole point
        of offering the choice."""
        long_text = "strong improvement " + " ".join(["filler"] * 18)  # 20 tokens
        items = [RawScrapedItem(f"i{n}", "NEWS", MIDDAY, "AAPL", f"{long_text} {n}")
                 for n in range(3)]

        polar = WebScrapedSentimentPipelineEngine()
        tone = WebScrapedSentimentPipelineEngine(score_metric="lm_tone")
        polar_signal = polar.generate_ticker_signals(
            polar.process_scraped_feed(items), "AAPL", SIGNAL_DATE, _baseline(0.0))
        tone_signal = tone.generate_ticker_signals(
            tone.process_scraped_feed(items), "AAPL", SIGNAL_DATE, _baseline(0.0))

        self.assertAlmostEqual(1.0, polar_signal.current_sentiment_mean, places=6)
        # 2 positive of 21 tokens ("... filler 0" is 21 tokens) -> well under 1.0
        self.assertLess(tone_signal.current_sentiment_mean, 0.2)
        self.assertLess(tone_signal.sentiment_zscore, polar_signal.sentiment_zscore)


class SignalContractTests(unittest.TestCase):
    def test_every_direction_emitted_is_declared(self):
        from web_scraped_sentiment_data_pipeline import VALID_DIRECTIONS
        engine = WebScrapedSentimentPipelineEngine()
        cases = [
            ([], []),
            (["strong improvement", "profitable gains", "successful benefit"], _baseline(0.0)),
            (["loss litigation", "bankruptcy fraud", "failure damages"], _baseline(0.0)),
            (["strong loss", "profitable litigation", "benefit damages"], _baseline(-0.125)),
        ]
        seen = set()
        for texts, baseline in cases:
            items = [RawScrapedItem(f"i{n}", "NEWS", MIDDAY, "AAPL", t) for n, t in enumerate(texts)]
            signal = engine.generate_ticker_signals(
                engine.process_scraped_feed(items), "AAPL", SIGNAL_DATE, baseline)
            self.assertIsInstance(signal, SentimentSignal)
            self.assertIn(signal.direction, VALID_DIRECTIONS)
            seen.add(signal.direction)
        self.assertEqual({"LONG", "SHORT", "NEUTRAL", "INSUFFICIENT_DATA"}, seen)


if __name__ == "__main__":
    unittest.main()
