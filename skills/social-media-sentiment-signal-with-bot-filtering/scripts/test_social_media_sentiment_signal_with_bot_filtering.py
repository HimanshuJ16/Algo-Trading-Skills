import unittest
import pandas as pd
from social_media_sentiment_signal_with_bot_filtering import (
    SocialMediaSentimentSignalWithBotFilteringEngine, SignalResult,
    SocialPost, BotFilteringResult, SocialSentimentSignal
)

class TestSocialMediaSentimentSignalLegacy(unittest.TestCase):
    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine()

    def test_empty_data(self):
        df = pd.DataFrame()
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 0)

    def test_valid_data(self):
        df = pd.DataFrame({
            "raw_val": [10.0, 20.0],
            "asset": ["AAPL", "MSFT"]
        })
        signals = self.engine.generate_signals(df)
        self.assertEqual(len(signals), 2)
        self.assertEqual(signals[0].signal_value, 15.0)
        self.assertEqual(signals[0].asset_id, "AAPL")

class TestSocialMediaSentimentEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SocialMediaSentimentSignalWithBotFilteringEngine(
            min_account_age_days=30,
            max_posts_per_hour=40,
            historical_baseline_mean=0.0,
            historical_baseline_std=0.2
        )

    def test_bot_and_spam_post_filtering(self):
        # Post 1: Valid user with bullish text
        p1 = SocialPost("P1", "NVDA", "U1", "2026-08-05", "NVDA huge breakout bullish rocket!", 120, 500, 5)
        res1 = self.engine.filter_post(p1)
        self.assertFalse(res1.is_bot_or_spam)
        self.assertGreater(res1.clean_sentiment_score, 0.0)

        # Post 2: Spammer account (<30 days old + telegram link)
        p2 = SocialPost("P2", "NVDA", "U2", "2026-08-05", "Join my channel t.me/free_crypto for guaranteed profit", 5, 10, 100)
        res2 = self.engine.filter_post(p2)
        self.assertTrue(res2.is_bot_or_spam)
        self.assertEqual(len(res2.rejection_reasons), 3) # Young account, burst, spam pattern

    def test_sentiment_signal_generation_with_z_score(self):
        posts = [
            SocialPost("P1", "TSLA", "U1", "2026-08-05", "TSLA breakout long buy moon!", 100, 1000, 2),
            SocialPost("P2", "TSLA", "U2", "2026-08-05", "TSLA bullish calls upgrade!", 200, 2000, 1),
            SocialPost("P3", "TSLA", "U3", "2026-08-05", "t.me/spam_channel buy cheap crypto", 2, 5, 80) # Bot! Filtered out!
        ]
        sig = self.engine.process_social_posts("TSLA", posts)

        self.assertEqual(sig.total_posts_analyzed, 3)
        self.assertEqual(sig.bot_posts_filtered_count, 1)
        self.assertEqual(sig.clean_posts_count, 2)
        self.assertGreater(sig.filtered_sentiment_mean, 0.5)
        self.assertIn(sig.directional_signal, ["BULLISH", "STRONG_BULLISH"])

if __name__ == '__main__':
    unittest.main()
