import unittest
import datetime
from web_scraped_sentiment_data_pipeline import (
    RawScrapedItem,
    ScoredSentimentItem,
    SentimentSignal,
    WebScrapedSentimentPipelineEngine,
)


class TestWebScrapedSentimentPipelineEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WebScrapedSentimentPipelineEngine(zscore_threshold=1.5)
        self.now = datetime.datetime.utcnow()
        self.today = self.now.date()

    def test_text_cleaning_html_and_urls(self):
        raw = "<div><p>Check $AAPL earnings at https://example.com/aapl! Massive growth & profit surge!</p></div>"
        clean = self.engine.clean_text(raw)

        self.assertNotIn("<div>", clean)
        self.assertNotIn("https://", clean)
        self.assertNotIn("$", clean)
        self.assertIn("massive growth profit surge", clean)

    def test_loughran_mcdonald_sentiment_scoring(self):
        pos_text = "strong revenue growth profit beat record upgrade"
        pos_c, neg_c, score = self.engine.score_text(pos_text)

        self.assertTrue(pos_c > 0)
        self.assertEqual(neg_c, 0)
        self.assertEqual(score, 1.0)

        neg_text = "heavy loss default bankruptcy scandal warning"
        p_c, n_c, neg_score = self.engine.score_text(neg_text)

        self.assertEqual(p_c, 0)
        self.assertTrue(n_c > 0)
        self.assertEqual(neg_score, -1.0)

    def test_process_scraped_feed(self):
        items = [
            RawScrapedItem("1", "NEWS", self.now, "AAPL", "AAPL quarterly revenue growth surpassed expectations."),
            RawScrapedItem("2", "TWITTER", self.now, "TSLA", "TSLA stock slump after delivery miss and loss warning."),
        ]
        scored = self.engine.process_scraped_feed(items)

        self.assertEqual(len(scored), 2)
        self.assertEqual(scored[0].ticker, "AAPL")
        self.assertTrue(scored[0].raw_sentiment_score > 0.0)
        self.assertEqual(scored[1].ticker, "TSLA")
        self.assertTrue(scored[1].raw_sentiment_score < 0.0)

    def test_ticker_long_signal_on_positive_zscore(self):
        items = [
            RawScrapedItem("1", "NEWS", self.now, "NVDA", "NVDA bullish growth surge profit record beat."),
        ]
        scored = self.engine.process_scraped_feed(items)

        # Baseline: mean 0.0, std 0.2 -> Current score = +1.0 -> Z = +5.0 >= +1.5 -> LONG
        signal = self.engine.generate_ticker_signals(
            scored_items=scored,
            target_ticker="NVDA",
            signal_date=self.today,
            historical_baseline_scores=[0.0, 0.1, -0.1, 0.0, 0.05],
        )

        self.assertEqual(signal.direction, "LONG")
        self.assertTrue(signal.sentiment_zscore > 1.5)

    def test_ticker_short_signal_on_negative_zscore(self):
        items = [
            RawScrapedItem("1", "NEWS", self.now, "INTC", "INTC loss slump downgrade default warning."),
        ]
        scored = self.engine.process_scraped_feed(items)

        signal = self.engine.generate_ticker_signals(
            scored_items=scored,
            target_ticker="INTC",
            signal_date=self.today,
            historical_baseline_scores=[0.0, 0.1, -0.1, 0.0, 0.05],
        )

        self.assertEqual(signal.direction, "SHORT")
        self.assertTrue(signal.sentiment_zscore < -1.5)

    def test_empty_feed_returns_neutral(self):
        signal = self.engine.generate_ticker_signals(
            scored_items=[],
            target_ticker="MSFT",
            signal_date=self.today,
            historical_baseline_scores=[],
        )

        self.assertEqual(signal.direction, "NEUTRAL")
        self.assertEqual(signal.sentiment_zscore, 0.0)


if __name__ == "__main__":
    unittest.main()

