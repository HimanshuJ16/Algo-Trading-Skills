import logging
import math
import re
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Set

logger = logging.getLogger(__name__)

# Institutional Loughran-McDonald Financial Sentiment Dictionaries
FINANCIAL_POSITIVE_WORDS: Set[str] = {
    "growth", "profit", "profitable", "gain", "gains", "surge", "outperform", "outperformance",
    "bullish", "rally", "exceed", "exceeded", "exceeding", "strong", "beat", "upbeat", "record",
    "expansion", "dividend", "revenue", "breakthrough", "upgrade", "upgraded", "momentum", "buy"
}

FINANCIAL_NEGATIVE_WORDS: Set[str] = {
    "loss", "losses", "decline", "declining", "drop", "slump", "downfall", "bearish", "plunge",
    "deficit", "default", "bankruptcy", "lawsuit", "investigation", "downgrade", "downgraded",
    "warning", "weak", "miss", "missed", "fraud", "scandal", "risk", "recession", "sell"
}


class SentimentPipelineError(Exception):
    """Base exception for Web-Scraped Sentiment Pipeline Engine errors."""
    pass


@dataclass
class RawScrapedItem:
    item_id: str
    source: str                                 # E.g. "NEWS", "REDDIT", "TWITTER", "STOCKTWITS"
    timestamp: datetime.datetime
    ticker: str
    text_content: str


@dataclass
class ScoredSentimentItem:
    item_id: str
    ticker: str
    timestamp: datetime.datetime
    positive_count: int
    negative_count: int
    raw_sentiment_score: float                  # Scaled [-1.0, +1.0]
    clean_text: str


@dataclass
class SentimentSignal:
    ticker: str
    signal_date: datetime.date
    current_sentiment_mean: float
    baseline_mean: float
    baseline_std: float
    sentiment_zscore: float
    direction: str                              # "LONG", "SHORT", "NEUTRAL"
    confidence_score: float                     # [0.0, 1.0]


class WebScrapedSentimentPipelineEngine:
    """
    Institutional Web-Scraped Sentiment Data Pipeline Engine.
    
    Cleans raw web-scraped text data (HTML stripping, URL removal, ticker normalization),
    scores text using Loughran-McDonald Financial Lexicon dictionary tokenization,
    computes volume-weighted sentiment metrics, derives 30-day baseline Z-scores,
    and outputs directional trading signals for quantitative strategies.
    """

    def __init__(self, zscore_threshold: float = 1.5):
        """
        :param zscore_threshold: Z-score standard deviation anomaly threshold to trigger LONG/SHORT signals.
        """
        self.zscore_threshold = zscore_threshold
        logger.info(f"Initialized Web-Scraped Sentiment Pipeline Engine (Z-Score Threshold = ±{zscore_threshold})")

    def clean_text(self, text: str) -> str:
        """
        Cleans and normalizes raw scraped text by removing HTML tags, URLs, special characters, and extra spaces.
        """
        if not text:
            return ""

        # Remove HTML tags
        clean = re.sub(r"<[^>]+>", " ", text)
        # Remove URLs
        clean = re.sub(r"http[s]?://\S+", " ", clean)
        # Remove cashtags/hashtags symbols but keep word
        clean = re.sub(r"[\$#]", "", clean)
        # Remove special characters keeping alphanumeric and spaces
        clean = re.sub(r"[^\w\s]", " ", clean)
        # Lowercase and normalize whitespace
        clean = " ".join(clean.lower().split())

        return clean

    def score_text(self, clean_text: str) -> Tuple[int, int, float]:
        """
        Scores text using Loughran-McDonald Financial Lexicon token matching.
        
        Returns (pos_count, neg_count, raw_sentiment_score [-1.0, +1.0]).
        """
        tokens = clean_text.split()
        if not tokens:
            return 0, 0, 0.0

        pos_count = sum(1 for token in tokens if token in FINANCIAL_POSITIVE_WORDS)
        neg_count = sum(1 for token in tokens if token in FINANCIAL_NEGATIVE_WORDS)

        total_words = pos_count + neg_count
        if total_words == 0:
            raw_score = 0.0
        else:
            raw_score = (pos_count - neg_count) / float(total_words)

        return pos_count, neg_count, round(raw_score, 4)

    def process_scraped_feed(self, items: List[RawScrapedItem]) -> List[ScoredSentimentItem]:
        """
        Processes a feed of raw scraped text items into scored sentiment records.
        """
        scored_results = []

        for item in items:
            clean = self.clean_text(item.text_content)
            pos_c, neg_c, score = self.score_text(clean)

            scored = ScoredSentimentItem(
                item_id=item.item_id,
                ticker=item.ticker.upper(),
                timestamp=item.timestamp,
                positive_count=pos_c,
                negative_count=neg_c,
                raw_sentiment_score=score,
                clean_text=clean,
            )
            scored_results.append(scored)

        logger.info(f"Processed {len(scored_results)} scraped sentiment items.")
        return scored_results

    def generate_ticker_signals(
        self,
        scored_items: List[ScoredSentimentItem],
        target_ticker: str,
        signal_date: datetime.date,
        historical_baseline_scores: List[float],
    ) -> SentimentSignal:
        """
        Generates directional sentiment signals for a ticker based on 30-day baseline Z-score anomalies.
        """
        ticker_items = [s for s in scored_items if s.ticker == target_ticker.upper()]
        if not ticker_items:
            return SentimentSignal(
                ticker=target_ticker.upper(),
                signal_date=signal_date,
                current_sentiment_mean=0.0,
                baseline_mean=0.0,
                baseline_std=1.0,
                sentiment_zscore=0.0,
                direction="NEUTRAL",
                confidence_score=0.0,
            )

        current_mean = sum(s.raw_sentiment_score for s in ticker_items) / len(ticker_items)

        if not historical_baseline_scores:
            base_mean = current_mean
            base_std = 1.0
        else:
            base_mean = sum(historical_baseline_scores) / len(historical_baseline_scores)
            var = sum((x - base_mean) ** 2 for x in historical_baseline_scores) / max(1, len(historical_baseline_scores) - 1)
            base_std = math.sqrt(var) if var > 0 else 1.0

        z_score = (current_mean - base_mean) / base_std
        direction = "NEUTRAL"
        confidence = round(min(1.0, abs(z_score) / (self.zscore_threshold * 2.0)), 2)

        if z_score >= self.zscore_threshold:
            direction = "LONG"
        elif z_score <= -self.zscore_threshold:
            direction = "SHORT"

        logger.info(
            f"Sentiment Signal [{target_ticker.upper()}]: CurrentMean={current_mean:+.4f}, "
            f"BaselineMean={base_mean:+.4f}, Z-Score={z_score:+.2f} -> Direction={direction}"
        )

        return SentimentSignal(
            ticker=target_ticker.upper(),
            signal_date=signal_date,
            current_sentiment_mean=round(current_mean, 4),
            baseline_mean=round(base_mean, 4),
            baseline_std=round(base_std, 4),
            sentiment_zscore=round(z_score, 2),
            direction=direction,
            confidence_score=confidence,
        )

