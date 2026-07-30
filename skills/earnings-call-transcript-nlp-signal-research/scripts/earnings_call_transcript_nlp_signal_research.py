import re
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Sample Loughran-McDonald Financial Lexicon Words
DEFAULT_LM_POSITIVE = {"strong", "growth", "record", "exceed", "outperform", "profit", "robust", "expansion", "momentum", "gain"}
DEFAULT_LM_NEGATIVE = {"decline", "loss", "headwind", "deficit", "slowdown", "impairment", "drop", "failure", "risk", "adversely"}
DEFAULT_LM_UNCERTAINTY = {"uncertain", "might", "could", "possibly", "unclear", "contingent", "risk", "fluctuate", "unpredictable"}

@dataclass
class TranscriptSectionScore:
    section_name: str                   # 'PREPARED_REMARKS' or 'QA_SESSION'
    total_words: int
    positive_count: int
    negative_count: int
    uncertainty_count: int
    net_sentiment: float                # -1.0 to +1.0
    uncertainty_ratio_pct: float

@dataclass
class EarningsTranscriptAuditReport:
    ticker: str
    quarter: str                        # e.g. 'Q1 2026'
    overall_net_sentiment: float
    prepared_remarks_sentiment: float
    qa_session_sentiment: float
    qa_tone_divergence: float           # QA_Sentiment - Prepared_Sentiment
    signal: str                         # 'BULLISH_EARNINGS_TONE', 'BEARISH_QA_DIVERGENCE', 'NEUTRAL'
    sections: List[TranscriptSectionScore]
    summary_notes: str

class EarningsTranscriptNlpEngine:
    """
    Quantitative NLP research engine for analyzing earnings call transcripts,
    computing Loughran-McDonald financial sentiment, Q&A tone divergence, and executive uncertainty ratios for equity trading signals.
    """
    def __init__(
        self,
        positive_words: Optional[Set[str]] = None,
        negative_words: Optional[Set[str]] = None,
        uncertainty_words: Optional[Set[str]] = None
    ):
        self.positive_words = positive_words or DEFAULT_LM_POSITIVE
        self.negative_words = negative_words or DEFAULT_LM_NEGATIVE
        self.uncertainty_words = uncertainty_words or DEFAULT_LM_UNCERTAINTY

    def analyze_text_section(self, section_name: str, text: str) -> TranscriptSectionScore:
        """
        Analyzes a section of text using Loughran-McDonald financial sentiment lexicon.
        """
        words = re.findall(r"\b[a-zA-Z]+\b", text.lower())
        total = len(words)
        if total == 0:
            return TranscriptSectionScore(section_name, 0, 0, 0, 0, 0.0, 0.0)

        pos_cnt = sum(1 for w in words if w in self.positive_words)
        neg_cnt = sum(1 for w in words if w in self.negative_words)
        uncert_cnt = sum(1 for w in words if w in self.uncertainty_words)

        denom = pos_cnt + neg_cnt
        net_sent = round((pos_cnt - neg_cnt) / float(denom), 4) if denom > 0 else 0.0
        uncert_pct = round((uncert_cnt / float(total)) * 100.0, 2)

        return TranscriptSectionScore(
            section_name=section_name,
            total_words=total,
            positive_count=pos_cnt,
            negative_count=neg_cnt,
            uncertainty_count=uncert_cnt,
            net_sentiment=net_sent,
            uncertainty_ratio_pct=uncert_pct
        )

    def generate_transcript_signal(
        self,
        ticker: str,
        quarter: str,
        prepared_remarks_text: str,
        qa_session_text: str
    ) -> EarningsTranscriptAuditReport:
        """
        Parses prepared remarks vs Q&A session text, computes Q&A tone divergence, and emits trading signals.
        """
        prep_score = self.analyze_text_section("PREPARED_REMARKS", prepared_remarks_text)
        qa_score = self.analyze_text_section("QA_SESSION", qa_session_text)

        divergence = round(qa_score.net_sentiment - prep_score.net_sentiment, 4)
        overall_denom = (prep_score.positive_count + qa_score.positive_count) + (prep_score.negative_count + qa_score.negative_count)
        overall_sent = round(
            ((prep_score.positive_count + qa_score.positive_count) - (prep_score.negative_count + qa_score.negative_count)) / float(overall_denom), 4
        ) if overall_denom > 0 else 0.0

        # Signal Logic:
        # If Q&A tone drops sharply relative to prepared remarks (Divergence < -0.15) -> BEARISH_QA_DIVERGENCE
        # If Overall Sentiment > 0.40 and QA Sentiment > 0.30 -> BULLISH_EARNINGS_TONE
        if divergence < -0.15:
            signal = "BEARISH_QA_DIVERGENCE"
            notes = f"BEARISH SIGNAL [{ticker}]: Q&A Sentiment ({qa_score.net_sentiment:+.2f}) dropped sharply from Prepared Remarks ({prep_score.net_sentiment:+.2f}). Tone Divergence = {divergence:+.2f}."
            logger.warning(notes)
        elif overall_sent > 0.30 and qa_score.net_sentiment > 0.20:
            signal = "BULLISH_EARNINGS_TONE"
            notes = f"BULLISH SIGNAL [{ticker}]: Strong overall sentiment ({overall_sent:+.2f}) and positive Q&A tone ({qa_score.net_sentiment:+.2f})."
            logger.info(notes)
        else:
            signal = "NEUTRAL"
            notes = f"NEUTRAL SIGNAL [{ticker}]: Balanced tone across transcript sections."

        return EarningsTranscriptAuditReport(
            ticker=ticker,
            quarter=quarter,
            overall_net_sentiment=overall_sent,
            prepared_remarks_sentiment=prep_score.net_sentiment,
            qa_session_sentiment=qa_score.net_sentiment,
            qa_tone_divergence=divergence,
            signal=signal,
            sections=[prep_score, qa_score],
            summary_notes=notes
        )
