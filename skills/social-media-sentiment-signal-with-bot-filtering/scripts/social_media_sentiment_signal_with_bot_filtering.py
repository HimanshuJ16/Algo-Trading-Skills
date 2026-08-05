import logging
import re
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set
import pandas as pd

logger = logging.getLogger(__name__)

@dataclass
class SignalResult:
    """Legacy SignalResult container for backward compatibility."""
    timestamp: str
    signal_value: float
    asset_id: str

@dataclass
class SocialPost:
    post_id: str
    asset_id: str
    user_id: str
    created_at_iso: str
    text: str
    user_account_age_days: int
    user_follower_count: int
    user_posts_last_hour: int
    is_verified_user: bool = False

@dataclass
class BotFilteringResult:
    post_id: str
    is_bot_or_spam: bool
    rejection_reasons: List[str]
    clean_sentiment_score: float         # [-1.0, +1.0]

@dataclass
class SocialSentimentSignal:
    asset_id: str
    total_posts_analyzed: int
    bot_posts_filtered_count: int
    clean_posts_count: int
    raw_sentiment_mean: float
    filtered_sentiment_mean: float
    sentiment_z_score: float             # Standardized score vs 30-day baseline
    directional_signal: str              # 'STRONG_BULLISH', 'BULLISH', 'NEUTRAL', 'BEARISH', 'STRONG_BEARISH'
    audit_notes: str

class SocialMediaSentimentSignalWithBotFilteringEngine:
    """
    Production-grade Social Media Sentiment Engine featuring multi-layered bot/spam filtering
    (account age, posting frequency, spam regex patterns), financial NLP sentiment scoring, and Z-score signal generation.
    """
    # Financial Bullish vs Bearish Lexicon
    BULLISH_KEYWORDS = {"bullish", "moon", "breakout", "call", "calls", "long", "buy", "upgrade", "outperform", "rocket", "pump"}
    BEARISH_KEYWORDS = {"bearish", "dump", "crash", "put", "puts", "short", "sell", "downgrade", "underperform", "scam", "drop"}
    SPAM_PATTERNS = [
        r"t\.me/", r"bit\.ly/", r"join my channel", r"guaranteed profit", r"free crypto",
        r"cashapp", r"whatsapp", r"signals link", r"\$pump"
    ]

    def __init__(
        self,
        config: Optional[dict] = None,
        min_account_age_days: int = 30,
        max_posts_per_hour: int = 40,
        historical_baseline_mean: float = 0.05,
        historical_baseline_std: float = 0.15
    ):
        self.config = config or {}
        self.min_account_age_days = min_account_age_days
        self.max_posts_per_hour = max_posts_per_hour
        self.baseline_mean = historical_baseline_mean
        self.baseline_std = historical_baseline_std

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        """Legacy generate_signals method retained for 100% backward compatibility."""
        if raw_data.empty:
            return []
        results = []
        for i, row in raw_data.iterrows():
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=float(row.get("raw_val", 0) * 1.5),
                asset_id=row.get("asset", "unknown")
            ))
        return results

    def _score_text_sentiment(self, text: str) -> float:
        """Evaluates financial lexicon sentiment score in range [-1.0, +1.0]."""
        words = set(re.findall(r"\b\w+\b", text.lower()))
        bull_count = len(words.intersection(self.BULLISH_KEYWORDS))
        bear_count = len(words.intersection(self.BEARISH_KEYWORDS))

        total = bull_count + bear_count
        if total == 0:
            return 0.0
        return (bull_count - bear_count) / float(total)

    def filter_post(self, post: SocialPost) -> BotFilteringResult:
        """
        Applies multi-layered bot & spam filtering to a single post:
        1. Account Age Check (< 30 days).
        2. High-Frequency Posting Burst Check (> 40 posts/hr).
        3. Spam Regex Pattern Matching.
        """
        rejections: List[str] = []

        # 1. Young Account Check
        if post.user_account_age_days < self.min_account_age_days and not post.is_verified_user:
            rejections.append(f"YOUNG_ACCOUNT: Account age ({post.user_account_age_days}d) < {self.min_account_age_days}d.")

        # 2. High Frequency Spammer Check
        if post.user_posts_last_hour > self.max_posts_per_hour:
            rejections.append(f"HIGH_FREQUENCY_BURST: {post.user_posts_last_hour} posts/hr > Max {self.max_posts_per_hour}.")

        # 3. Spam Pattern Check
        text_lower = post.text.lower()
        for pat in self.SPAM_PATTERNS:
            if re.search(pat, text_lower):
                rejections.append(f"SPAM_PATTERN_MATCH: Matched pattern '{pat}'.")
                break

        is_bot = len(rejections) > 0
        sentiment = 0.0 if is_bot else self._score_text_sentiment(post.text)

        return BotFilteringResult(
            post_id=post.post_id,
            is_bot_or_spam=is_bot,
            rejection_reasons=rejections,
            clean_sentiment_score=sentiment
        )

    def process_social_posts(self, asset_id: str, posts: List[SocialPost]) -> SocialSentimentSignal:
        """
        Filters raw posts, computes clean sentiment mean, and generates a standardized Z-score signal.
        """
        if not posts:
            return SocialSentimentSignal(
                asset_id=asset_id, total_posts_analyzed=0, bot_posts_filtered_count=0,
                clean_posts_count=0, raw_sentiment_mean=0.0, filtered_sentiment_mean=0.0,
                sentiment_z_score=0.0, directional_signal="NEUTRAL", audit_notes="No posts to analyze."
            )

        bot_count = 0
        clean_scores: List[float] = []
        raw_scores: List[float] = []

        for p in posts:
            raw_scores.append(self._score_text_sentiment(p.text))
            res = self.filter_post(p)
            if res.is_bot_or_spam:
                bot_count += 1
            else:
                clean_scores.append(res.clean_sentiment_score)

        clean_count = len(clean_scores)
        raw_mean = sum(raw_scores) / len(raw_scores) if raw_scores else 0.0
        filtered_mean = sum(clean_scores) / clean_count if clean_count > 0 else 0.0

        # Calculate Z-Score relative to 30-day historical baseline
        z_score = (filtered_mean - self.baseline_mean) / self.baseline_std if self.baseline_std > 0 else 0.0
        z_score = round(z_score, 2)

        # Directional Signal Classifier
        if z_score >= 2.0:
            signal = "STRONG_BULLISH"
        elif z_score >= 0.75:
            signal = "BULLISH"
        elif z_score <= -2.0:
            signal = "STRONG_BEARISH"
        elif z_score <= -0.75:
            signal = "BEARISH"
        else:
            signal = "NEUTRAL"

        notes = (
            f"SOCIAL SENTIMENT [{asset_id}]: Total Posts = {len(posts)}, Bots Filtered = {bot_count}, "
            f"Filtered Sentiment = {filtered_mean:+.3f} (Raw {raw_mean:+.3f}), Z-Score = {z_score:+.2f}, Signal = {signal}."
        )

        logger.info(notes)

        return SocialSentimentSignal(
            asset_id=asset_id,
            total_posts_analyzed=len(posts),
            bot_posts_filtered_count=bot_count,
            clean_posts_count=clean_count,
            raw_sentiment_mean=round(raw_mean, 4),
            filtered_sentiment_mean=round(filtered_mean, 4),
            sentiment_z_score=z_score,
            directional_signal=signal,
            audit_notes=notes
        )
