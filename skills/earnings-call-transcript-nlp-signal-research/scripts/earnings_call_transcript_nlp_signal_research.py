"""
Earnings Call Transcript NLP Signal Engine.

Quantitative NLP research engine for earnings call transcripts. Scores prepared
remarks and the analyst Q&A session separately with the Loughran-McDonald (LM)
financial lexicon, applies the LM negation rule to positive terms, and derives
Q&A tone divergence plus an uncertainty ratio for equity signal research
(post-earnings announcement drift / event-driven strategies).

Lexicon provenance
------------------
The DEFAULT_LM_* sets below are a *small illustrative subset* of the Loughran-
McDonald Master Dictionary, restricted to frequently occurring members of each
category in SEC filings. Every word was checked against the Master Dictionary
category flags (Negative / Positive / Uncertainty). They exist so the engine is
runnable out of the box; they are NOT a substitute for the full lists (LM
Negative ~2,355 words, Positive ~354, Uncertainty ~297). Production research
MUST load the current official dictionary and pass it to the constructor.

  Source: Loughran & McDonald, "When Is a Liability Not a Liability? Textual
  Analysis, Dictionaries, and 10-Ks", Journal of Finance 66(1), 2011.
  Master Dictionary: https://sraf.nd.edu/loughranmcdonald-master-dictionary/
  Licensing: free for academic research; commercial use requires a licence from
  the authors -- see the licensing terms on the Master Dictionary page above. The
  short word lists below are a runnable sample only; obtain and load the official
  dictionary under its own terms for any production or commercial use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import Iterable, List, Optional, Set

logger = logging.getLogger(__name__)

# --- Verified subsets of the Loughran-McDonald Master Dictionary -------------
# NOTE: "risk"/"risks" are LM *Uncertainty*, not LM Negative. Classifying them as
# negative is the exact general-purpose-lexicon error this skill warns against.
DEFAULT_LM_POSITIVE: Set[str] = {
    "able", "achieve", "achieved", "achievement", "advantage", "beneficial",
    "benefit", "best", "better", "collaboration", "effective", "efficiency",
    "enable", "enhance", "enhanced", "excellent", "exclusive", "favorable",
    "gain", "gains", "good", "greater", "highest", "improve", "improved",
    "improvement", "improvements", "leading", "opportunities", "opportunity",
    "outperform", "positive", "profitability", "profitable", "progress",
    "satisfactory", "strength", "strong", "stronger", "success", "successful",
    "successfully", "superior",
}
DEFAULT_LM_NEGATIVE: Set[str] = {
    "adverse", "adversely", "against", "bankruptcy", "breach", "claims",
    "complaint", "concern", "damage", "damages", "decline", "declined",
    "deficiencies", "deficit", "delay", "discontinued", "downturn", "fail",
    "failure", "fraud", "impaired", "impairment", "litigation", "loss",
    "losses", "misleading", "negative", "penalties", "restructuring",
    "shortfall", "slowdown", "terminate", "terminated", "termination",
    "unable", "violation", "volatility", "weaknesses", "worse",
}
DEFAULT_LM_UNCERTAINTY: Set[str] = {
    "anticipate", "anticipated", "approximate", "approximately", "assume",
    "assumed", "assumption", "assumptions", "believe", "believes",
    "contingencies", "contingent", "could", "depend", "dependent", "depending",
    "differ", "doubt", "doubtful", "exposure", "exposures", "fluctuate",
    "fluctuations", "may", "might", "pending", "possible", "possibly",
    "predict", "preliminary", "probable", "risk", "risks", "uncertain",
    "uncertainties", "uncertainty", "unclear", "unknown", "unpredictable",
    "variable", "vary", "volatility",
}

# Loughran & McDonald (2011) treat a positive word as negated when one of these
# negators appears within the three preceding words. The rule is applied to
# positive words only; the authors report that double negation of negative words
# is rare in financial disclosure.
DEFAULT_NEGATORS: Set[str] = {"no", "not", "none", "neither", "never", "nobody"}
NEGATION_WINDOW = 3

# Words, allowing intra-word apostrophes/hyphens so "don't" and "year-over-year"
# stay single tokens instead of inflating the denominator with fragments.
_TOKEN_RE = re.compile(r"[a-z]+(?:['’-][a-z]+)*")

PREPARED_REMARKS = "PREPARED_REMARKS"
QA_SESSION = "QA_SESSION"

SIGNAL_BEARISH = "BEARISH_QA_DIVERGENCE"
SIGNAL_BULLISH = "BULLISH_EARNINGS_TONE"
SIGNAL_NEUTRAL = "NEUTRAL"
SIGNAL_INSUFFICIENT = "INSUFFICIENT_DATA"


@dataclass
class TranscriptSectionScore:
    """
    Lexicon scores for one transcript section.

    Attributes:
        section_name: 'PREPARED_REMARKS' or 'QA_SESSION'.
        total_words: Token count of the section (denominator of the ratios).
        positive_count: Positive-lexicon hits remaining after negation flipping.
        negative_count: Negative-lexicon hits plus negated positive words.
        uncertainty_count: Hits against the LM Uncertainty list.
        net_sentiment: (pos - neg) / (pos + neg) in [-1.0, 1.0]; 0.0 when the
            section contains no polarity words at all — an absence of evidence,
            not a neutral tone. Check `has_sufficient_sample` before trading it.
        uncertainty_ratio_pct: uncertainty_count / total_words, in percent.
        negated_positive_count: Positive words reclassified as negative by the
            LM three-word negation rule (diagnostic only; already reflected in
            positive_count / negative_count).
        has_sufficient_sample: False when the section is shorter than
            `min_section_words` or carries fewer than `min_polarity_terms`
            polarity words, meaning its sentiment score is statistical noise.
    """

    section_name: str
    total_words: int
    positive_count: int
    negative_count: int
    uncertainty_count: int
    net_sentiment: float
    uncertainty_ratio_pct: float
    negated_positive_count: int = 0
    has_sufficient_sample: bool = True


@dataclass
class EarningsTranscriptAuditReport:
    """
    Signal output for one earnings call.

    Attributes:
        ticker: Instrument identifier supplied by the caller.
        quarter: Fiscal period label, e.g. 'Q1 2026'.
        overall_net_sentiment: Polarity computed on the pooled counts of both
            sections (not the average of the two section scores).
        prepared_remarks_sentiment: Net sentiment of the prepared remarks.
        qa_session_sentiment: Net sentiment of the Q&A session.
        qa_tone_divergence: qa_session_sentiment - prepared_remarks_sentiment.
            Negative means tone fell once management was questioned.
        signal: One of BULLISH_EARNINGS_TONE, BEARISH_QA_DIVERGENCE, NEUTRAL,
            INSUFFICIENT_DATA.
        sections: Per-section scores, prepared remarks first.
        summary_notes: Human-readable explanation of the emitted signal.
        overall_uncertainty_ratio_pct: Pooled uncertainty words as a percentage
            of pooled tokens.
        transcript_published_at: Timezone-aware timestamp at which the transcript
            became available. Carried through so backtests can align the signal
            to publication time rather than to the call date; naive datetimes are
            rejected because they are the usual source of look-ahead bias here.
    """

    ticker: str
    quarter: str
    overall_net_sentiment: float
    prepared_remarks_sentiment: float
    qa_session_sentiment: float
    qa_tone_divergence: float
    signal: str
    sections: List[TranscriptSectionScore]
    summary_notes: str
    overall_uncertainty_ratio_pct: float = 0.0
    transcript_published_at: Optional[datetime] = None


class EarningsTranscriptNlpEngine:
    """
    Scores earnings call transcripts with the Loughran-McDonald financial lexicon
    and emits a Q&A tone-divergence research signal.

    Thresholds are constructor parameters, not constants: the defaults are
    plausible starting points, NOT empirically calibrated values. Calibrate them
    on your own universe (sector, market cap, transcript vendor) before trading —
    tone distributions differ materially across those cuts.
    """

    def __init__(
        self,
        positive_words: Optional[Iterable[str]] = None,
        negative_words: Optional[Iterable[str]] = None,
        uncertainty_words: Optional[Iterable[str]] = None,
        negators: Optional[Iterable[str]] = None,
        *,
        bearish_divergence_threshold: float = -0.15,
        bullish_sentiment_threshold: float = 0.40,
        max_uncertainty_ratio_pct: float = 1.5,
        min_section_words: int = 50,
        min_polarity_terms: int = 5,
    ) -> None:
        self.positive_words = self._normalise_lexicon(positive_words, DEFAULT_LM_POSITIVE, "positive_words")
        self.negative_words = self._normalise_lexicon(negative_words, DEFAULT_LM_NEGATIVE, "negative_words")
        self.uncertainty_words = self._normalise_lexicon(uncertainty_words, DEFAULT_LM_UNCERTAINTY, "uncertainty_words")
        self.negators = self._normalise_lexicon(negators, DEFAULT_NEGATORS, "negators")

        if bearish_divergence_threshold >= 0.0:
            raise ValueError("bearish_divergence_threshold must be negative (it flags a tone drop in Q&A)")
        if not -1.0 < bullish_sentiment_threshold < 1.0:
            raise ValueError("bullish_sentiment_threshold must lie strictly inside (-1.0, 1.0)")
        if max_uncertainty_ratio_pct <= 0.0:
            raise ValueError("max_uncertainty_ratio_pct must be positive")
        if min_section_words < 0 or min_polarity_terms < 0:
            raise ValueError("min_section_words and min_polarity_terms must be non-negative")

        self.bearish_divergence_threshold = bearish_divergence_threshold
        self.bullish_sentiment_threshold = bullish_sentiment_threshold
        self.max_uncertainty_ratio_pct = max_uncertainty_ratio_pct
        self.min_section_words = min_section_words
        self.min_polarity_terms = min_polarity_terms

    @staticmethod
    def _normalise_lexicon(
        supplied: Optional[Iterable[str]],
        default: Set[str],
        param_name: str,
    ) -> Set[str]:
        """Lower-cases a caller-supplied lexicon; falls back to the default set."""
        if supplied is None:
            return set(default)
        if isinstance(supplied, str):
            raise TypeError(f"{param_name} must be an iterable of words, not a single string")
        words = {str(w).strip().lower() for w in supplied}
        words.discard("")
        if not words:
            raise ValueError(f"{param_name} was supplied but contains no usable words")
        return words

    def tokenize(self, text: str) -> List[str]:
        """Lower-cases and tokenizes text, keeping contractions and hyphenated terms intact."""
        if not isinstance(text, str):
            raise TypeError(f"transcript text must be str, got {type(text).__name__}")
        return _TOKEN_RE.findall(text.lower())

    def analyze_text_section(self, section_name: str, text: str) -> TranscriptSectionScore:
        """
        Scores one transcript section with the LM lexicon and the LM negation rule.

        A positive word preceded within three tokens by a negator ("no", "not",
        "none", "neither", "never", "nobody") is counted as negative, following
        Loughran & McDonald (2011).
        """
        words = self.tokenize(text)
        total = len(words)
        if total == 0:
            return TranscriptSectionScore(
                section_name=section_name,
                total_words=0,
                positive_count=0,
                negative_count=0,
                uncertainty_count=0,
                net_sentiment=0.0,
                uncertainty_ratio_pct=0.0,
                negated_positive_count=0,
                has_sufficient_sample=False,
            )

        pos_cnt = 0
        neg_cnt = 0
        uncert_cnt = 0
        negated_cnt = 0

        for idx, word in enumerate(words):
            # LM categories overlap by design (e.g. "volatility" is both Negative
            # and Uncertainty), so uncertainty is counted independently of polarity.
            if word in self.uncertainty_words:
                uncert_cnt += 1
            if word in self.positive_words:
                window = words[max(0, idx - NEGATION_WINDOW):idx]
                if any(w in self.negators for w in window):
                    neg_cnt += 1
                    negated_cnt += 1
                else:
                    pos_cnt += 1
            elif word in self.negative_words:
                neg_cnt += 1

        polarity_terms = pos_cnt + neg_cnt
        net_sent = (pos_cnt - neg_cnt) / polarity_terms if polarity_terms > 0 else 0.0
        uncert_pct = (uncert_cnt / total) * 100.0
        sufficient = total >= self.min_section_words and polarity_terms >= self.min_polarity_terms

        return TranscriptSectionScore(
            section_name=section_name,
            total_words=total,
            positive_count=pos_cnt,
            negative_count=neg_cnt,
            uncertainty_count=uncert_cnt,
            net_sentiment=round(net_sent, 4),
            uncertainty_ratio_pct=round(uncert_pct, 2),
            negated_positive_count=negated_cnt,
            has_sufficient_sample=sufficient,
        )

    def generate_transcript_signal(
        self,
        ticker: str,
        quarter: str,
        prepared_remarks_text: str,
        qa_session_text: str,
        transcript_published_at: Optional[datetime] = None,
    ) -> EarningsTranscriptAuditReport:
        """
        Scores prepared remarks against the Q&A session and emits a tone signal.

        Signal precedence, highest first:
          1. INSUFFICIENT_DATA — either section is too short or carries too few
             polarity words for its score to mean anything.
          2. BEARISH_QA_DIVERGENCE — divergence below `bearish_divergence_threshold`.
          3. BULLISH_EARNINGS_TONE — pooled sentiment above
             `bullish_sentiment_threshold` AND pooled uncertainty ratio below
             `max_uncertainty_ratio_pct`.
          4. NEUTRAL.

        Bearish divergence deliberately outranks bullish tone: a call whose
        prepared remarks are glowing but whose Q&A tone collapses is exactly the
        case this signal exists to catch.

        Raises:
            TypeError: if a text argument is not a str, or `transcript_published_at`
                is not a datetime.
            ValueError: if ticker/quarter are blank, or `transcript_published_at`
                is timezone-naive (a look-ahead hazard in backtests).
        """
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("ticker must be a non-empty string")
        if not isinstance(quarter, str) or not quarter.strip():
            raise ValueError("quarter must be a non-empty string")
        if transcript_published_at is not None:
            if not isinstance(transcript_published_at, datetime):
                raise TypeError("transcript_published_at must be a datetime")
            if (
                transcript_published_at.tzinfo is None
                or transcript_published_at.tzinfo.utcoffset(transcript_published_at) is None
            ):
                raise ValueError(
                    "transcript_published_at must be timezone-aware; naive timestamps "
                    "silently introduce look-ahead bias in transcript backtests"
                )

        ticker = ticker.strip()
        prep_score = self.analyze_text_section(PREPARED_REMARKS, prepared_remarks_text)
        qa_score = self.analyze_text_section(QA_SESSION, qa_session_text)

        divergence = round(qa_score.net_sentiment - prep_score.net_sentiment, 4)

        pooled_pos = prep_score.positive_count + qa_score.positive_count
        pooled_neg = prep_score.negative_count + qa_score.negative_count
        pooled_polarity = pooled_pos + pooled_neg
        overall_sent = round((pooled_pos - pooled_neg) / pooled_polarity, 4) if pooled_polarity > 0 else 0.0

        pooled_words = prep_score.total_words + qa_score.total_words
        pooled_uncert = prep_score.uncertainty_count + qa_score.uncertainty_count
        overall_uncert_pct = round((pooled_uncert / pooled_words) * 100.0, 2) if pooled_words > 0 else 0.0

        prep_polarity = prep_score.positive_count + prep_score.negative_count
        qa_polarity = qa_score.positive_count + qa_score.negative_count

        if not (prep_score.has_sufficient_sample and qa_score.has_sufficient_sample):
            signal = SIGNAL_INSUFFICIENT
            notes = (
                f"INSUFFICIENT DATA [{ticker} {quarter}]: prepared remarks "
                f"({prep_score.total_words} words, {prep_polarity} polarity terms) and/or Q&A "
                f"({qa_score.total_words} words, {qa_polarity} polarity terms) fall below the "
                f"minimum sample ({self.min_section_words} words, {self.min_polarity_terms} "
                "polarity terms). No tradable tone signal."
            )
            logger.warning(notes)
        elif divergence < self.bearish_divergence_threshold:
            signal = SIGNAL_BEARISH
            notes = (
                f"BEARISH SIGNAL [{ticker} {quarter}]: Q&A sentiment ({qa_score.net_sentiment:+.2f}) "
                f"dropped from prepared remarks ({prep_score.net_sentiment:+.2f}). Tone divergence = "
                f"{divergence:+.2f} (threshold {self.bearish_divergence_threshold:+.2f})."
            )
            logger.warning(notes)
        elif overall_sent > self.bullish_sentiment_threshold and overall_uncert_pct < self.max_uncertainty_ratio_pct:
            signal = SIGNAL_BULLISH
            notes = (
                f"BULLISH SIGNAL [{ticker} {quarter}]: pooled sentiment ({overall_sent:+.2f}) above "
                f"{self.bullish_sentiment_threshold:+.2f} with uncertainty ratio "
                f"{overall_uncert_pct:.2f}% below {self.max_uncertainty_ratio_pct:.2f}%."
            )
            logger.info(notes)
        else:
            signal = SIGNAL_NEUTRAL
            notes = (
                f"NEUTRAL SIGNAL [{ticker} {quarter}]: divergence {divergence:+.2f}, pooled sentiment "
                f"{overall_sent:+.2f}, uncertainty ratio {overall_uncert_pct:.2f}% — no threshold breached."
            )

        return EarningsTranscriptAuditReport(
            ticker=ticker,
            quarter=quarter,
            overall_net_sentiment=overall_sent,
            prepared_remarks_sentiment=prep_score.net_sentiment,
            qa_session_sentiment=qa_score.net_sentiment,
            qa_tone_divergence=divergence,
            signal=signal,
            sections=[prep_score, qa_score],
            summary_notes=notes,
            overall_uncertainty_ratio_pct=overall_uncert_pct,
            transcript_published_at=transcript_published_at,
        )
