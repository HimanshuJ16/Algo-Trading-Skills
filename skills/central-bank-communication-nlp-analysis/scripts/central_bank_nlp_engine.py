import re
import logging
from dataclasses import dataclass
from typing import List, Set

logger = logging.getLogger(__name__)

@dataclass
class SentimentResult:
    hawkish_count: int
    dovish_count: int
    total_words: int
    net_score: float # Ranges from -1.0 (pure dovish) to 1.0 (pure hawkish)

class CentralBankNLPEngine:
    """
    Analyzes central bank text for Hawkish vs Dovish sentiment.
    Includes negation handling within an n-gram window.
    """
    def __init__(self):
        # Specialized macro-finance lexicons
        self.hawkish_words: Set[str] = {
            "tighten", "tightening", "hike", "hikes", "increase", "strong", 
            "inflationary", "taper", "tapering", "overheating", "hawkish"
        }
        self.dovish_words: Set[str] = {
            "ease", "easing", "accommodative", "cut", "cuts", "weak",
            "slowdown", "stimulus", "dovish", "supportive", "transitory"
        }
        self.negation_words: Set[str] = {
            "not", "no", "less", "without", "hardly", "rarely"
        }
        
        self.negation_window = 3 # Look up to 3 words back for a negation

    def _tokenize(self, text: str) -> List[str]:
        """Lowercases and extracts words."""
        text = text.lower()
        # Remove punctuation, split by whitespace
        words = re.findall(r'\b[a-z]+\b', text)
        return words

    def analyze_sentiment(self, text: str) -> SentimentResult:
        tokens = self._tokenize(text)
        total_words = len(tokens)
        
        if total_words == 0:
            return SentimentResult(0, 0, 0, 0.0)
            
        hawkish_count = 0
        dovish_count = 0
        
        for i, token in enumerate(tokens):
            is_hawkish = token in self.hawkish_words
            is_dovish = token in self.dovish_words
            
            if not is_hawkish and not is_dovish:
                continue
                
            # Check for negation in the preceding window
            start_idx = max(0, i - self.negation_window)
            preceding_tokens = tokens[start_idx:i]
            
            is_negated = any(neg in preceding_tokens for neg in self.negation_words)
            
            if is_negated:
                # Invert sentiment
                if is_hawkish:
                    dovish_count += 1
                else:
                    hawkish_count += 1
            else:
                if is_hawkish:
                    hawkish_count += 1
                else:
                    dovish_count += 1

        # Calculate normalized net score: (H - D) / (H + D)
        total_signals = hawkish_count + dovish_count
        if total_signals == 0:
            net_score = 0.0
        else:
            net_score = (hawkish_count - dovish_count) / total_signals
            
        return SentimentResult(
            hawkish_count=hawkish_count,
            dovish_count=dovish_count,
            total_words=total_words,
            net_score=net_score
        )
