"""
Central Bank Communication NLP Engine.

Quantitative NLP engine for extracting hawkish, dovish, and policy uncertainty
sentiment from central bank communications (FOMC, ECB, BOE, RBI statements/minutes).
Features sentence-boundary-isolated negation handling, multi-word phrase matching,
lexicon scoring (Apel-Grimaldi & Hansen-McMahon frameworks), and sequential statement diffing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import logging
import math
import re
from typing import List, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SentimentResult:
    """
    Quantitative sentiment results from central bank communication analysis.

    Attributes:
        hawkish_count: Total count of hawkish terms (including inverted dovish terms).
        dovish_count: Total count of dovish terms (including inverted hawkish terms).
        total_words: Total word count of the analyzed text.
        net_score: Normalized stance score in [-1.0, 1.0], where +1.0 is pure hawkish and -1.0 is pure dovish.
        uncertainty_count: Count of uncertainty and data-dependence hedging terms.
        hawkish_density: Hawkish mentions per 1,000 words.
        dovish_density: Dovish mentions per 1,000 words.
        uncertainty_density: Uncertainty/hedging mentions per 1,000 words.
        matched_hawkish: List of matched hawkish terms/phrases.
        matched_dovish: List of matched dovish terms/phrases.
        matched_uncertainty: List of matched uncertainty terms.
    """
    hawkish_count: int
    dovish_count: int
    total_words: int
    net_score: float
    uncertainty_count: int = 0
    hawkish_density: float = 0.0
    dovish_density: float = 0.0
    uncertainty_density: float = 0.0
    matched_hawkish: List[str] = field(default_factory=list)
    matched_dovish: List[str] = field(default_factory=list)
    matched_uncertainty: List[str] = field(default_factory=list)


@dataclass
class StatementComparison:
    """
    Sequential statement comparison metrics (e.g., FOMC Statement Redline).

    Attributes:
        previous_score: Net sentiment score of prior statement.
        current_score: Net sentiment score of current statement.
        score_delta: Change in sentiment (positive = hawkish shift, negative = dovish shift).
        jaccard_similarity: Lexical overlap between consecutive statements.
        cosine_similarity: Bag-of-words term-frequency cosine similarity (no IDF weighting).
        added_hawkish: New hawkish terms introduced in current statement.
        removed_hawkish: Hawkish terms dropped from previous statement.
        added_dovish: New dovish terms introduced in current statement.
        removed_dovish: Dovish terms dropped from previous statement.
    """
    previous_score: float
    current_score: float
    score_delta: float
    jaccard_similarity: float
    cosine_similarity: float
    added_hawkish: List[str] = field(default_factory=list)
    removed_hawkish: List[str] = field(default_factory=list)
    added_dovish: List[str] = field(default_factory=list)
    removed_dovish: List[str] = field(default_factory=list)


class CentralBankNLPEngine:
    """
    Analyzes central bank statements, minutes, and transcripts for monetary policy stance.
    Implements multi-word phrase matching, sentence-isolated negation windows, and statement diffing.
    """

    # Dotted patterns that must not be read as sentence boundaries.
    _DECIMAL_RE = re.compile(r'\d+\.\d+')
    _ABBREV_RE = re.compile(r'\b(?:[A-Za-z]\.){2,}')
    _DOT_MASK = "\x00"

    def __init__(self, negation_window: int = 3) -> None:
        """
        Args:
            negation_window: Number of preceding in-sentence tokens inspected for a
                negation cue. Must be an int >= 1.

        Raises:
            TypeError: If negation_window is not an int.
            ValueError: If negation_window is less than 1.
        """
        if isinstance(negation_window, bool) or not isinstance(negation_window, int):
            raise TypeError("negation_window must be an int")
        if negation_window < 1:
            raise ValueError("negation_window must be >= 1")
        self.negation_window = negation_window

        # Multi-word phrases checked before single words (longest match first)
        self.hawkish_phrases: List[str] = [
            "tighten monetary policy", "tightening monetary policy", "hike rates",
            "raise rates", "rate hike", "rate hikes", "further firming",
            "quantitative tightening", "balance sheet reduction", "inflationary pressures",
            "elevated inflation", "restrictive stance", "overheating economy",
            "tight labor market", "labor shortage", "upside inflation risks",
            "price pressures", "excess demand", "curb inflation", "rein in inflation",
        ]

        self.dovish_phrases: List[str] = [
            "ease monetary policy", "easing monetary policy", "cut rates",
            "lower rates", "rate cut", "rate cuts", "quantitative easing",
            "asset purchase program", "economic slowdown", "downward pressure",
            "labor market slack", "subdued inflation", "downside growth risks",
            "support economic activity", "patient approach", "maintain accommodation",
            "transitory inflation", "cooling labor market", "softening demand",
        ]

        # Single word dictionaries based on Apel-Grimaldi (2014) & Hansen-McMahon (2016)
        self.hawkish_words: Set[str] = {
            "tighten", "tightening", "hike", "hikes", "increase", "strong",
            "inflationary", "taper", "tapering", "overheating", "hawkish",
            "restrictive", "accelerate", "robust", "resilient", "firming",
            "elevated", "pressures", "boom"
        }

        self.dovish_words: Set[str] = {
            "ease", "easing", "accommodative", "cut", "cuts", "weak",
            "slowdown", "stimulus", "dovish", "supportive", "transitory",
            "subdued", "softening", "sluggish", "deteriorating", "cooling",
            "slack", "fragile", "stagnation", "pause", "patience"
        }

        # Central bank uncertainty / data-dependence terms
        self.uncertainty_terms: Set[str] = {
            "uncertain", "uncertainty", "uncertainties", "conditional",
            "data-dependent", "flexible", "headwinds", "unpredictable",
            "crosscurrents", "ambiguous", "monitoring", "cautious"
        }

        self.negation_words: Set[str] = {
            "not", "no", "less", "without", "hardly", "rarely", "scarcely", "neither", "nor", "never"
        }

    def _split_sentences(self, text: str) -> List[str]:
        """
        Splits text into sentences on `.`, `!`, `?`, `;` and newlines.

        Decimal numbers ("0.25 percentage point", "2.5 percent") and dotted
        abbreviations ("U.S.", "e.g.") are masked before splitting: a spurious
        boundary inside them truncates the negation window and can invert the stance
        of a sentence (e.g. "no 0.25 percentage point hike"). Single-token
        abbreviations such as "Mr." or "No." are not protected and remain a known
        limitation.
        """
        # Drop any pre-existing mask characters so masking cannot corrupt input text.
        text = text.replace(self._DOT_MASK, "")
        masked = self._DECIMAL_RE.sub(
            lambda m: m.group(0).replace(".", self._DOT_MASK), text)
        masked = self._ABBREV_RE.sub(
            lambda m: m.group(0).replace(".", self._DOT_MASK), masked)
        sentences = re.split(r'[.!?;\n]+', masked)
        return [s.replace(self._DOT_MASK, ".").strip() for s in sentences if s.strip()]

    def _compiled_phrases(self) -> List[Tuple[List[str], str, str]]:
        """
        Builds the phrase-match table from the current lexicons, longest match first.

        Compiled per call so callers may extend `hawkish_phrases` / `dovish_phrases`
        at runtime. Sorting by token length (then alphabetically, for determinism)
        makes matching genuinely longest-first and prevents the hawkish list from
        pre-empting a longer overlapping dovish phrase purely by list order.
        """
        compiled: List[Tuple[List[str], str, str]] = []
        for phrase in self.hawkish_phrases:
            compiled.append((self._tokenize(phrase), phrase, "hawkish"))
        for phrase in self.dovish_phrases:
            compiled.append((self._tokenize(phrase), phrase, "dovish"))
        compiled = [c for c in compiled if c[0]]
        compiled.sort(key=lambda c: (-len(c[0]), c[1]))
        return compiled

    @staticmethod
    def _empty_result() -> SentimentResult:
        """Neutral, signal-free result used for empty input."""
        return SentimentResult(
            hawkish_count=0,
            dovish_count=0,
            total_words=0,
            net_score=0.0,
            uncertainty_count=0,
            hawkish_density=0.0,
            dovish_density=0.0,
            uncertainty_density=0.0,
        )


    def _tokenize(self, text: str) -> List[str]:
        """Extracts lowercase word tokens, stripping punctuation."""
        return re.findall(r'\b[a-z0-9]+(?:-[a-z0-9]+)?\b', text.lower())

    def analyze_sentiment(self, text: str) -> SentimentResult:
        """
        Analyzes text for Hawkish, Dovish, and Uncertainty sentiment.

        Multi-word policy phrases are matched first (longest match first), then
        remaining single tokens are scored. Negation is resolved only against
        preceding tokens of the SAME sentence, so a negation cue cannot leak across a
        sentence boundary.

        Args:
            text: Raw central bank text. `None` or an empty/whitespace string yields a
                signal-free neutral result.

        Returns:
            SentimentResult containing counts, densities, and the normalized net score.

        Raises:
            TypeError: If `text` is neither `None` nor a `str`. Non-text input is
                rejected rather than scored as neutral, so a caller cannot mistake a
                type error for a genuine 0.0 (balanced) policy stance.
        """
        if text is None:
            return self._empty_result()
        if not isinstance(text, str):
            raise TypeError(
                "analyze_sentiment expects str or None, got " + type(text).__name__)

        all_tokens = self._tokenize(text)
        total_words = len(all_tokens)
        if total_words == 0:
            logger.debug("analyze_sentiment: no scorable tokens in input")
            return self._empty_result()

        sentences = self._split_sentences(text)
        phrase_table = self._compiled_phrases()
        hawkish_count = 0
        dovish_count = 0
        uncertainty_count = 0
        matched_hawkish: List[str] = []
        matched_dovish: List[str] = []
        matched_uncertainty: List[str] = []

        for sentence in sentences:
            tokens = self._tokenize(sentence)
            if not tokens:
                continue

            # Token indices consumed by multi-word phrases, to avoid double counting
            consumed_indices: Set[int] = set()

            # 1. Match multi-word phrases, longest first, hawkish and dovish together
            for phrase_tokens, phrase, polarity in phrase_table:
                p_len = len(phrase_tokens)
                for i in range(len(tokens) - p_len + 1):
                    if tokens[i:i + p_len] != phrase_tokens:
                        continue
                    if any(idx in consumed_indices for idx in range(i, i + p_len)):
                        continue

                    # Negation cue within the window preceding the phrase start
                    preceding = tokens[max(0, i - self.negation_window):i]
                    is_negated = any(w in self.negation_words for w in preceding)

                    label = "negated(" + phrase + ")" if is_negated else phrase
                    effective = polarity
                    if is_negated:
                        effective = "dovish" if polarity == "hawkish" else "hawkish"

                    if effective == "hawkish":
                        hawkish_count += 1
                        matched_hawkish.append(label)
                    else:
                        dovish_count += 1
                        matched_dovish.append(label)

                    consumed_indices.update(range(i, i + p_len))

            # 2. Match individual words (skipping tokens consumed by phrases)
            for i, token in enumerate(tokens):
                if i in consumed_indices:
                    continue

                if token in self.uncertainty_terms:
                    uncertainty_count += 1
                    matched_uncertainty.append(token)

                is_hawkish = token in self.hawkish_words
                is_dovish = token in self.dovish_words

                if not is_hawkish and not is_dovish:
                    continue

                # Check negation strictly within this sentence's preceding window
                preceding_tokens = tokens[max(0, i - self.negation_window):i]
                is_negated = any(neg in preceding_tokens for neg in self.negation_words)

                if is_negated:
                    if is_hawkish:
                        dovish_count += 1
                        matched_dovish.append("negated(" + token + ")")
                    else:
                        hawkish_count += 1
                        matched_hawkish.append("negated(" + token + ")")
                else:
                    if is_hawkish:
                        hawkish_count += 1
                        matched_hawkish.append(token)
                    else:
                        dovish_count += 1
                        matched_dovish.append(token)

        # Normalized net stance score in [-1.0, 1.0]
        total_signals = hawkish_count + dovish_count
        net_score = (hawkish_count - dovish_count) / total_signals if total_signals else 0.0

        # Density per 1,000 words
        hawkish_density = (hawkish_count / total_words) * 1000.0
        dovish_density = (dovish_count / total_words) * 1000.0
        uncertainty_density = (uncertainty_count / total_words) * 1000.0

        logger.debug(
            "analyze_sentiment: words=%d hawkish=%d dovish=%d uncertainty=%d net=%.4f",
            total_words, hawkish_count, dovish_count, uncertainty_count, net_score)

        return SentimentResult(
            hawkish_count=hawkish_count,
            dovish_count=dovish_count,
            total_words=total_words,
            net_score=net_score,
            uncertainty_count=uncertainty_count,
            hawkish_density=hawkish_density,
            dovish_density=dovish_density,
            uncertainty_density=uncertainty_density,
            matched_hawkish=matched_hawkish,
            matched_dovish=matched_dovish,
            matched_uncertainty=matched_uncertainty,
        )


    def calculate_jaccard_similarity(self, text1: str, text2: str) -> float:
        """Computes lexical Jaccard similarity (|A ∩ B| / |A ∪ B|) between two texts."""
        set1 = set(self._tokenize(text1))
        set2 = set(self._tokenize(text2))
        if not set1 and not set2:
            return 1.0
        if not set1 or not set2:
            return 0.0
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        return intersection / union if union > 0 else 0.0

    def calculate_cosine_similarity(self, text1: str, text2: str) -> float:
        """Computes bag-of-words term frequency Cosine Similarity between two texts."""
        tokens1 = self._tokenize(text1)
        tokens2 = self._tokenize(text2)
        if not tokens1 and not tokens2:
            return 1.0
        if not tokens1 or not tokens2:
            return 0.0

        vec1 = Counter(tokens1)
        vec2 = Counter(tokens2)

        all_words = set(vec1.keys()).union(set(vec2.keys()))
        dot_product = sum(vec1[w] * vec2[w] for w in all_words)
        norm1 = math.sqrt(sum(v ** 2 for v in vec1.values()))
        norm2 = math.sqrt(sum(v ** 2 for v in vec2.values()))

        if norm1 == 0.0 or norm2 == 0.0:
            return 0.0
        return dot_product / (norm1 * norm2)

    def compare_statements(self, previous_text: str, current_text: str) -> StatementComparison:
        """
        Compares sequential central bank communications (e.g. consecutive FOMC statements).
        Computes sentiment delta (hawkish/dovish surprise), lexical similarity, and phrase diffs.

        Args:
            previous_text: Text of prior meeting statement.
            current_text: Text of current meeting statement.

        Returns:
            StatementComparison containing surprise delta, similarity, and added/removed stance phrases.
        """
        prev_res = self.analyze_sentiment(previous_text)
        curr_res = self.analyze_sentiment(current_text)

        score_delta = curr_res.net_score - prev_res.net_score
        jaccard = self.calculate_jaccard_similarity(previous_text, current_text)
        cosine = self.calculate_cosine_similarity(previous_text, current_text)

        prev_hawk_set = set(prev_res.matched_hawkish)
        curr_hawk_set = set(curr_res.matched_hawkish)
        added_hawkish = sorted(list(curr_hawk_set - prev_hawk_set))
        removed_hawkish = sorted(list(prev_hawk_set - curr_hawk_set))

        prev_dove_set = set(prev_res.matched_dovish)
        curr_dove_set = set(curr_res.matched_dovish)
        added_dovish = sorted(list(curr_dove_set - prev_dove_set))
        removed_dovish = sorted(list(prev_dove_set - curr_dove_set))

        return StatementComparison(
            previous_score=prev_res.net_score,
            current_score=curr_res.net_score,
            score_delta=score_delta,
            jaccard_similarity=jaccard,
            cosine_similarity=cosine,
            added_hawkish=added_hawkish,
            removed_hawkish=removed_hawkish,
            added_dovish=added_dovish,
            removed_dovish=removed_dovish,
        )
