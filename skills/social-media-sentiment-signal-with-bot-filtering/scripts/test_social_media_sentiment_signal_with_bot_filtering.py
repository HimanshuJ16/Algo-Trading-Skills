"""
Unit tests for social-media-sentiment-signal-with-bot-filtering.

Expected values are derived by hand in the comments, never by re-running the
implementation's own formula. Tests marked REGRESSION fail against the previous
version of this engine and pass against the current one.
"""
import datetime
import logging
import unittest

from social_media_sentiment_signal_with_bot_filtering import (
    SIGNAL_BEARISH,
    SIGNAL_BULLISH,
    SIGNAL_INSUFFICIENT,
    SIGNAL_NEUTRAL,
    SIGNAL_STRONG_BULLISH,
    BotFilteringResult,
    SocialMediaSentimentSignalWithBotFilteringEngine,
    SocialPost,
    SocialSentimentSignal,
)

MODULE_LOGGER = "social_media_sentiment_signal_with_bot_filtering"

# Keep test output clean without globally disabling logging, which would break the
# assertLogs assertion below.
logging.getLogger(MODULE_LOGGER).addHandler(logging.NullHandler())
logging.getLogger(MODULE_LOGGER).propagate = False

UTC = datetime.timezone.utc
BASE_TS = "2026-08-05T10:00:00Z"


def make_post(
    post_id: str,
    user_id: str,
    text: str,
    asset_id: str = "TSLA",
    created_at_iso: str = BASE_TS,
    account_age_days: int = 400,
    followers: int = 900,
    posts_last_hour: int = 2,
    verified: bool = False,
) -> SocialPost:
    """An established, low-rate account by default, so only the tested field varies."""
    return SocialPost(
        post_id=post_id,
        asset_id=asset_id,
        user_id=user_id,
        created_at_iso=created_at_iso,
        text=text,
        user_account_age_days=account_age_days,
        user_follower_count=followers,
        user_posts_last_hour=posts_last_hour,
        is_verified_user=verified,
    )


class TestLexiconScoring(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine()

    def test_pure_bullish_and_pure_bearish(self):
        # {breakout, bullish, rocket} -> (3 - 0) / 3 = +1.0
        self.assertEqual(self.engine._score_text_sentiment("TSLA huge breakout bullish rocket!"), 1.0)
        # {bearish, crash, dump} -> (0 - 3) / 3 = -1.0
        self.assertEqual(self.engine._score_text_sentiment("bearish crash incoming, dump it"), -1.0)

    def test_mixed_terms_cancel(self):
        # {bullish} vs {bearish} -> (1 - 1) / 2 = 0.0
        self.assertEqual(self.engine._score_text_sentiment("some say bullish, others say bearish"), 0.0)

    def test_no_lexicon_hit_scores_zero(self):
        self.assertEqual(self.engine._score_text_sentiment("earnings are on Thursday"), 0.0)

    def test_repetition_cannot_amplify(self):
        # Distinct terms are counted once: a post cannot buy intensity by repeating.
        self.assertEqual(
            self.engine._score_text_sentiment("moon moon moon moon"),
            self.engine._score_text_sentiment("moon"),
        )

    def test_negation_flips_polarity(self):
        # REGRESSION: set-membership scoring read "not bullish" as +1.0.
        self.assertEqual(self.engine._score_text_sentiment("this is not bullish"), -1.0)
        self.assertEqual(self.engine._score_text_sentiment("this is not bearish"), 1.0)

    def test_negation_window_is_bounded_and_that_is_a_known_limitation(self):
        # 'not' sits 7 tokens before 'bullish', outside the 3-token window, so this
        # scores +1.0 even though the sentence means the opposite. Documented in the
        # module docstring: the scorer reads tokens, not meaning.
        self.assertEqual(
            self.engine._score_text_sentiment("not a single one of them is bullish"), 1.0)

    def test_pump_is_not_scored_bullish(self):
        # REGRESSION: 'pump' used to sit in BULLISH_KEYWORDS, so a surviving
        # pump-and-dump post pushed the signal the way the campaign intended.
        self.assertEqual(self.engine._score_text_sentiment("pump it to the sky"), 0.0)

    def test_non_string_text_rejected(self):
        with self.assertRaises(ValueError):
            self.engine._score_text_sentiment(None)


class TestPerPostScreens(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine()

    def test_established_account_with_clean_text_passes(self):
        result = self.engine.filter_post(make_post("P1", "U1", "TSLA huge breakout bullish rocket!"))
        self.assertIsInstance(result, BotFilteringResult)
        self.assertFalse(result.is_bot_or_spam)
        self.assertEqual(result.rejection_reasons, [])
        self.assertEqual(result.clean_sentiment_score, 1.0)

    def test_spammer_trips_all_three_screens(self):
        post = make_post(
            "P2", "U2", "Join my channel t.me/free_crypto for guaranteed profit",
            account_age_days=5, posts_last_hour=100,
        )
        result = self.engine.filter_post(post)
        self.assertTrue(result.is_bot_or_spam)
        # young account + burst + one spam-pattern reason (first match only)
        self.assertEqual(len(result.rejection_reasons), 3)
        self.assertEqual(result.clean_sentiment_score, 0.0)

    def test_dot_obfuscated_link_still_matches(self):
        for text in ("join t (dot) me/x now", "join t dot me/x now", "join t․me/x now"):
            result = self.engine.filter_post(make_post("P3", "U3", text))
            self.assertTrue(result.is_bot_or_spam, text)

    def test_account_age_boundary(self):
        # Strictly less than the threshold is rejected; exactly the threshold passes.
        self.assertTrue(self.engine.filter_post(
            make_post("P4", "U4", "bullish", account_age_days=29)).is_bot_or_spam)
        self.assertFalse(self.engine.filter_post(
            make_post("P5", "U5", "bullish", account_age_days=30)).is_bot_or_spam)

    def test_posting_rate_boundary(self):
        # Strictly above the threshold is rejected; exactly the threshold passes.
        self.assertFalse(self.engine.filter_post(
            make_post("P6", "U6", "bullish", posts_last_hour=40)).is_bot_or_spam)
        self.assertTrue(self.engine.filter_post(
            make_post("P7", "U7", "bullish", posts_last_hour=41)).is_bot_or_spam)

    def test_verified_badge_does_not_exempt_a_young_account_by_default(self):
        # REGRESSION: the badge used to bypass the age screen unconditionally. A paid
        # checkmark is not identity verification (EC decision IP/25/2934, 5 Dec 2025).
        young_verified = make_post("P8", "U8", "bullish", account_age_days=1, verified=True)
        self.assertTrue(self.engine.filter_post(young_verified).is_bot_or_spam)

        trusting = SocialMediaSentimentSignalWithBotFilteringEngine(trust_verified_accounts=True)
        self.assertFalse(trusting.filter_post(young_verified).is_bot_or_spam)

    def test_malformed_posts_are_rejected(self):
        cases = {
            "empty text": make_post("P9", "U9", "   "),
            "naive timestamp": make_post("P9", "U9", "bullish", created_at_iso="2026-08-05"),
            "unparseable timestamp": make_post("P9", "U9", "bullish", created_at_iso="not-a-date"),
            "negative age": make_post("P9", "U9", "bullish", account_age_days=-1),
            "negative rate": make_post("P9", "U9", "bullish", posts_last_hour=-3),
            "blank user id": make_post("P9", "  ", "bullish"),
        }
        for label, post in cases.items():
            with self.subTest(label):
                with self.assertRaises(ValueError):
                    self.engine.filter_post(post)
        with self.assertRaises(ValueError):
            self.engine.filter_post("not a post")


class TestConfigurationValidation(unittest.TestCase):
    def test_degenerate_baseline_std_is_a_configuration_error(self):
        # REGRESSION: a non-positive sigma used to be swallowed into Z = 0.0 / NEUTRAL.
        for bad in (0.0, -0.2, float("nan"), float("inf")):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    SocialMediaSentimentSignalWithBotFilteringEngine(historical_baseline_std=bad)

    def test_other_invalid_configuration(self):
        bad_kwargs = [
            {"historical_baseline_mean": float("nan")},
            {"min_effective_sample": 0},
            {"max_posts_per_hour": 0},
            {"min_account_age_days": -1},
            {"signal_z": 2.0, "strong_signal_z": 2.0},   # STRONG band unreachable
            {"signal_z": 3.0, "strong_signal_z": 2.0},
            {"signal_z": 0.0},
            {"lookback_window_minutes": 0},
            {"one_vote_per_author": "yes"},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    SocialMediaSentimentSignalWithBotFilteringEngine(**kwargs)


class TestSampleGate(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.2, min_effective_sample=20)

    def test_single_post_produces_no_signal(self):
        # REGRESSION: one clean post scored +1.0 produced a full-conviction
        # STRONG_BULLISH at Z = (1.0 - 0.0) / 0.2 = 5.0 off a sample of one.
        signal = self.engine.process_social_posts(
            "TSLA", [make_post("P1", "U1", "TSLA breakout bullish moon")])
        self.assertIsInstance(signal, SocialSentimentSignal)
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)
        self.assertIsNone(signal.sentiment_z_score)
        self.assertFalse(signal.is_signal_measurable)
        self.assertEqual(signal.effective_sample_size, 1)

    def test_empty_batch_is_insufficient_not_neutral(self):
        # REGRESSION: an empty batch used to report NEUTRAL, which reads as a
        # measured flat market rather than an absence of evidence.
        signal = self.engine.process_social_posts("TSLA", [])
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)
        self.assertIsNone(signal.sentiment_z_score)
        self.assertEqual(signal.total_posts_analyzed, 0)

    def test_z_score_is_none_never_zero_when_unmeasurable(self):
        # One contributor short of the gate: the suppression must also be logged,
        # so a silent INSUFFICIENT_DATA never disappears from the audit trail.
        posts = [make_post(f"P{i}", f"U{i}", f"bullish idea {i}") for i in range(19)]
        with self.assertLogs(MODULE_LOGGER, level=logging.WARNING):
            signal = self.engine.process_social_posts("TSLA", posts)
        self.assertEqual(signal.effective_sample_size, 19)
        self.assertIsNone(signal.sentiment_z_score)


class TestCoordinationScreens(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.2, min_effective_sample=20)

    def test_identical_text_campaign_collapses_to_one_contributor(self):
        # REGRESSION: 30 established, low-rate accounts posting the same line pass
        # every per-post screen. The old engine averaged 30 identical +1.0 scores
        # into Z = 5.0 / STRONG_BULLISH -- exactly the campaign this skill exists
        # to defend against.
        campaign = [make_post(f"P{i}", f"U{i}", "TSLA breakout bullish moon buy now") for i in range(30)]
        signal = self.engine.process_social_posts("TSLA", campaign)
        self.assertEqual(signal.bot_posts_filtered_count, 0)
        self.assertEqual(signal.duplicate_posts_filtered_count, 29)
        self.assertEqual(signal.effective_sample_size, 1)
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)

    def test_one_prolific_author_cannot_carry_the_batch(self):
        # 24 distinct bullish posts from one author + 3 neutral authors.
        # one_vote_per_author -> 4 contributors, below the gate of 20.
        posts = [make_post(f"A{i}", "LOUD", f"bullish breakout number {i}") for i in range(24)]
        posts += [make_post(f"B{i}", f"QUIET{i}", f"earnings on thursday note {i}") for i in range(3)]
        signal = self.engine.process_social_posts("TSLA", posts)
        self.assertEqual(signal.clean_posts_count, 27)
        self.assertEqual(signal.distinct_authors_count, 4)
        self.assertEqual(signal.effective_sample_size, 4)
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)

    def test_one_vote_per_author_disabled_counts_posts(self):
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.2,
            min_effective_sample=20, one_vote_per_author=False)
        posts = [make_post(f"A{i}", "LOUD", f"bullish breakout number {i}") for i in range(24)]
        signal = engine.process_social_posts("TSLA", posts)
        self.assertEqual(signal.effective_sample_size, 24)
        self.assertEqual(signal.distinct_authors_count, 1)
        # 24 posts each scoring +1.0 -> mean +1.0 -> Z = (1.0 - 0.0) / 0.2 = 5.0
        self.assertEqual(signal.filtered_sentiment_mean, 1.0)
        self.assertEqual(signal.sentiment_z_score, 5.0)

    def test_duplicate_collapse_can_be_disabled(self):
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.2,
            min_effective_sample=20, collapse_duplicate_text=False)
        campaign = [make_post(f"P{i}", f"U{i}", "TSLA breakout bullish moon buy now") for i in range(30)]
        signal = engine.process_social_posts("TSLA", campaign)
        self.assertEqual(signal.duplicate_posts_filtered_count, 0)
        self.assertEqual(signal.effective_sample_size, 30)
        self.assertEqual(signal.directional_signal, SIGNAL_STRONG_BULLISH)

    def test_non_latin_campaign_is_still_collapsed(self):
        # An ASCII-only fingerprint reduces every non-Latin post to "", which exempts
        # the whole campaign from duplicate collapse. The class must be Unicode-aware.
        text = "特斯拉 看涨 突破!!!"
        campaign = [make_post(f"P{i}", f"U{i}", text) for i in range(30)]
        signal = self.engine.process_social_posts("TSLA", campaign)
        self.assertEqual(signal.duplicate_posts_filtered_count, 29)
        self.assertEqual(signal.effective_sample_size, 1)

    def test_contentless_posts_are_not_treated_as_duplicates(self):
        # Both normalise to "" (cashtag and link only), so there is nothing to compare.
        posts = [
            make_post("P1", "U1", "$TSLA"),
            make_post("P2", "U2", "$TSLA https://example.com/a"),
        ]
        signal = self.engine.process_social_posts("TSLA", posts)
        self.assertEqual(signal.duplicate_posts_filtered_count, 0)
        self.assertEqual(signal.effective_sample_size, 2)


class TestSignalGeneration(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.1, min_effective_sample=20)

    def _twenty_authors(self, bullish: int, bearish: int):
        posts = [make_post(f"B{i}", f"BULL{i}", f"bullish breakout note {i}") for i in range(bullish)]
        posts += [make_post(f"S{i}", f"BEAR{i}", f"bearish crash note {i}") for i in range(bearish)]
        return posts

    def test_strong_bullish_at_the_inclusive_boundary(self):
        # 12 authors at +1.0, 8 at -1.0 -> mean = (12 - 8) / 20 = +0.2
        # Z = (0.2 - 0.0) / 0.1 = 2.0, which is exactly strong_signal_z (inclusive).
        signal = self.engine.process_social_posts("TSLA", self._twenty_authors(12, 8))
        self.assertEqual(signal.effective_sample_size, 20)
        self.assertEqual(signal.filtered_sentiment_mean, 0.2)
        self.assertEqual(signal.sentiment_z_score, 2.0)
        self.assertEqual(signal.directional_signal, SIGNAL_STRONG_BULLISH)
        self.assertTrue(signal.is_signal_measurable)

    def test_bearish_band(self):
        # 9 at +1.0, 11 at -1.0 -> mean = -0.1; Z = -1.0 -> between -2.0 and -0.75.
        signal = self.engine.process_social_posts("TSLA", self._twenty_authors(9, 11))
        self.assertEqual(signal.sentiment_z_score, -1.0)
        self.assertEqual(signal.directional_signal, SIGNAL_BEARISH)

    def test_balanced_batch_is_neutral_and_measurable(self):
        # 10 at +1.0, 10 at -1.0 -> mean 0.0 -> Z = 0.0. NEUTRAL is a measurement,
        # unlike INSUFFICIENT_DATA.
        signal = self.engine.process_social_posts("TSLA", self._twenty_authors(10, 10))
        self.assertEqual(signal.sentiment_z_score, 0.0)
        self.assertEqual(signal.directional_signal, SIGNAL_NEUTRAL)
        self.assertTrue(signal.is_signal_measurable)

    def test_classification_uses_the_unrounded_z_score(self):
        # REGRESSION: the old engine rounded Z to 2 dp *before* banding, so
        # round(1.9951, 2) == 2.0 promoted this batch to STRONG_BULLISH.
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=-1.9951, historical_baseline_std=1.0,
            min_effective_sample=20)
        # 10 at +1.0, 10 at -1.0 -> mean 0.0; Z = (0.0 + 1.9951) / 1.0 = 1.9951 < 2.0.
        signal = engine.process_social_posts("TSLA", self._twenty_authors(10, 10))
        self.assertEqual(signal.sentiment_z_score, 1.9951)
        self.assertEqual(signal.directional_signal, SIGNAL_BULLISH)

    def test_bots_are_excluded_from_the_filtered_mean_but_not_the_raw_mean(self):
        posts = self._twenty_authors(20, 0)
        posts += [make_post(f"X{i}", f"BOT{i}", f"bearish crash dump {i}",
                            account_age_days=2, posts_last_hour=90) for i in range(10)]
        signal = self.engine.process_social_posts("TSLA", posts)
        self.assertEqual(signal.bot_posts_filtered_count, 10)
        self.assertEqual(signal.effective_sample_size, 20)
        self.assertEqual(signal.filtered_sentiment_mean, 1.0)
        # raw mean over all 30 in-window posts: (20 * (+1.0) + 10 * (-1.0)) / 30 = 0.3333
        self.assertEqual(signal.raw_sentiment_mean, 0.3333)

    def test_results_are_deterministic(self):
        posts = self._twenty_authors(12, 8)
        self.assertEqual(
            self.engine.process_social_posts("TSLA", posts),
            self.engine.process_social_posts("TSLA", posts),
        )


class TestPointInTimeWindow(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.1, min_effective_sample=5)

    def test_posts_after_as_of_are_excluded(self):
        posts = [make_post(f"P{i}", f"U{i}", f"bullish breakout {i}",
                           created_at_iso="2026-08-05T09:00:00Z") for i in range(6)]
        posts += [make_post(f"F{i}", f"V{i}", f"bearish crash {i}",
                            created_at_iso="2026-08-05T23:00:00Z") for i in range(4)]
        signal = self.engine.process_social_posts(
            "TSLA", posts, as_of=datetime.datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
        self.assertEqual(signal.future_posts_excluded_count, 4)
        self.assertEqual(signal.effective_sample_size, 6)
        self.assertEqual(signal.filtered_sentiment_mean, 1.0)

    def test_stale_posts_are_excluded_when_a_window_is_configured(self):
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.1,
            min_effective_sample=5, lookback_window_minutes=60)
        recent = [make_post(f"R{i}", f"U{i}", f"bullish breakout {i}",
                            created_at_iso="2026-08-05T11:30:00Z") for i in range(5)]
        old = [make_post(f"O{i}", f"W{i}", f"bearish crash {i}",
                         created_at_iso="2026-08-05T06:00:00Z") for i in range(3)]
        signal = engine.process_social_posts(
            "TSLA", recent + old, as_of=datetime.datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
        self.assertEqual(signal.stale_posts_excluded_count, 3)
        self.assertEqual(signal.effective_sample_size, 5)

    def test_window_boundaries_are_inclusive(self):
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            historical_baseline_mean=0.0, historical_baseline_std=0.1,
            min_effective_sample=1, lookback_window_minutes=60)
        as_of = datetime.datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        posts = [
            make_post("EDGE_START", "U1", "bullish one", created_at_iso="2026-08-05T11:00:00Z"),
            make_post("EDGE_END", "U2", "bullish two", created_at_iso="2026-08-05T12:00:00Z"),
        ]
        signal = engine.process_social_posts("TSLA", posts, as_of=as_of)
        self.assertEqual(signal.future_posts_excluded_count, 0)
        self.assertEqual(signal.stale_posts_excluded_count, 0)
        self.assertEqual(signal.effective_sample_size, 2)

    def test_offset_timestamps_are_compared_correctly(self):
        # 08:30-04:00 is 12:30Z, which is after the 12:00Z cutoff.
        posts = [make_post("P1", "U1", "bullish", created_at_iso="2026-08-05T08:30:00-04:00")]
        signal = self.engine.process_social_posts(
            "TSLA", posts, as_of=datetime.datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
        self.assertEqual(signal.future_posts_excluded_count, 1)
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)

    def test_naive_as_of_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_social_posts(
                "TSLA", [make_post("P1", "U1", "bullish")],
                as_of=datetime.datetime(2026, 8, 5, 12, 0))

    def test_configured_window_without_as_of_is_rejected(self):
        engine = SocialMediaSentimentSignalWithBotFilteringEngine(lookback_window_minutes=60)
        with self.assertRaises(ValueError):
            engine.process_social_posts("TSLA", [make_post("P1", "U1", "bullish")])


class TestBatchIntegrity(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(min_effective_sample=1)

    def test_asset_id_mismatch_is_rejected(self):
        # REGRESSION: another asset's posts used to be aggregated silently under
        # whatever label the caller passed.
        posts = [make_post("P1", "U1", "bullish", asset_id="NVDA")]
        with self.assertRaises(ValueError):
            self.engine.process_social_posts("TSLA", posts)

    def test_blank_asset_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_social_posts("   ", [])

    def test_all_posts_outside_the_window_reports_counts(self):
        posts = [make_post(f"P{i}", f"U{i}", "bullish",
                           created_at_iso="2026-08-05T23:00:00Z") for i in range(3)]
        signal = self.engine.process_social_posts(
            "TSLA", posts, as_of=datetime.datetime(2026, 8, 5, 12, 0, tzinfo=UTC))
        self.assertEqual(signal.directional_signal, SIGNAL_INSUFFICIENT)
        self.assertEqual(signal.total_posts_analyzed, 3)
        self.assertEqual(signal.future_posts_excluded_count, 3)
        self.assertIsNone(signal.sentiment_z_score)


if __name__ == "__main__":
    unittest.main()
