"""Web-scraped financial text sentiment pipeline.

Cleans raw scraped web text, scores it against the Loughran-McDonald (LM)
financial sentiment word lists, aggregates a point-in-time daily mean per
ticker, and standardises that mean against a caller-supplied baseline to
produce a directional signal.

Lexicon provenance
------------------
The word sets below are a **verified subset** of the Loughran-McDonald Master
Dictionary (Software Repository for Accounting and Finance, University of Notre
Dame), which classifies 354 words as Positive and 2,355 as Negative in the 2014
release. Every term bundled here was checked for membership of the corresponding
LM category; no term was added by hand. The subset is the highest
document-frequency slice of each list and exists so the module runs standalone --
it is **not** the full dictionary. Production deployments should load the real
dictionary with :func:`load_lm_lexicon_from_master_dictionary`.

Licence note: the LM materials are published free for use in academic research;
commercial use requires contacting the authors. Confirm entitlement before
building a production trading signal on them.

Reference: Loughran, T. and McDonald, B. (2011), "When Is a Liability Not a
Liability? Textual Analysis, Dictionaries, and 10-Ks", Journal of Finance 66(1),
35-65.
"""

from __future__ import annotations

import csv
import datetime
import html
import logging
import math
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

LEXICON_SOURCE = (
    "Loughran-McDonald Master Dictionary, Software Repository for Accounting and "
    "Finance, University of Notre Dame "
    "(https://sraf.nd.edu/loughranmcdonald-master-dictionary/)"
)

# Verified members of the LM *Positive* category (highest document frequency).
LM_POSITIVE_WORDS: Set[str] = {
    "able", "accomplish", "accomplished", "achieve", "achieved", "achievement",
    "achievements", "achieving", "adequately", "advances", "advantage",
    "advantages", "alliances", "assure", "assured", "attractive", "beneficial",
    "beneficially", "benefit", "best", "better", "conclusive", "desirable",
    "desired", "despite", "effective", "efficiencies", "efficiency",
    "efficient", "efficiently", "enable", "enables", "enabling", "enhance",
    "enhanced", "enhancement", "enhancements", "enhancing", "exclusive",
    "exclusively", "favorable", "favorably", "gain", "gains", "good", "great",
    "greater", "highest", "improve", "improved", "improvement", "improvements",
    "improving", "innovative", "integrity", "leadership", "leading",
    "opportunities", "opportunity", "positive", "profitability", "profitable",
    "progress", "resolve", "satisfaction", "satisfactory", "satisfied",
    "satisfy", "stability", "stable", "strength", "strengthen", "strong",
    "succeed", "succeeding", "success", "successful", "successfully",
    "superior", "valuable",
}

# Verified members of the LM *Negative* category (highest document frequency).
LM_NEGATIVE_WORDS: Set[str] = {
    "absence", "adverse", "adversely", "against", "alleged", "alleging", "bad",
    "bankruptcy", "breach", "cancellation", "cancelled", "cautionary",
    "cautioned", "cease", "ceased", "challenges", "claims", "closed", "closing",
    "complaint", "concern", "concerns", "conflict", "conflicts", "contrary",
    "costly", "criminal", "critical", "damage", "damages", "decline",
    "declined", "declines", "declining", "default", "defaults", "defend",
    "defendant", "defendants", "defending", "defer", "deficiencies",
    "deficiency", "deficit", "delay", "delayed", "delays", "delinquent",
    "deteriorate", "difficult", "difficulties", "difficulty", "disagreements",
    "disclose", "disclosed", "discontinued", "dismissed", "dispose", "dispute",
    "disputes", "disruption", "disruptions", "dissolution", "doubt", "doubtful",
    "error", "errors", "exposed", "fail", "failed", "fails", "failure",
    "failures", "fines", "force", "forced", "forfeiture", "fraud", "harm",
    "hazardous", "impair", "impaired", "impairment", "impairments", "inability",
    "inadequate", "infringement", "injury", "insolvency", "insufficient",
    "interruption", "invalid", "investigation", "investigations", "lack",
    "late", "limitation", "limitations", "liquidation", "litigation", "lose",
    "loss", "losses", "lost", "misleading", "misstatement", "misstatements",
    "negative", "negatively", "obsolete", "omit", "omitted", "penalties",
    "penalty", "plaintiffs", "poor", "resignation", "restated", "restructuring",
    "suffer", "suspended", "terminate", "terminated", "terminates",
    "termination", "threatened", "unable", "unanticipated", "unauthorized",
    "uncollectible", "undue", "unexpected", "unfavorable", "unpaid",
    "violation", "violations", "volatile", "volatility", "weakness",
    "weaknesses",
}

# LM was induced from 10-K filings, so some of its terms carry a filing-specific
# polarity that does not survive transfer to news or social text. "Despite" is
# LM-Positive; "closed", "disclose", "claims", "critical", "force", "against",
# "late", "volatility" and "restructuring" are LM-Negative. In a headline
# ("shares closed higher", "record volatility", "beat despite headwinds") those
# readings are wrong. They are dropped by default, and the exclusion is an
# explicit, reversible constant rather than a silent edit to the word lists.
FILING_SPECIFIC_TERMS: Set[str] = {
    "against", "claims", "closed", "closing", "critical", "despite", "disclose",
    "disclosed", "force", "late", "restructuring", "volatile", "volatility",
}

# A lexicon term appearing within NEGATION_WINDOW tokens after one of these has
# its polarity flipped, so "not profitable" scores negative. Kept to purely
# syntactic negators: a word that is itself a lexicon term (e.g. "failed") is
# left to score on its own polarity rather than suppressed.
NEGATORS: Set[str] = {
    "no", "not", "none", "never", "neither", "nor", "cannot", "cant", "wont",
    "isnt", "arent", "wasnt", "werent", "dont", "doesnt", "didnt", "without",
    "hardly", "barely", "unlikely",
}
NEGATION_WINDOW = 3

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
# A truncated scrape can end mid-element, leaving an opening tag with no
# close. Stripping only the tag would spill the script body into the tokens.
_UNCLOSED_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w\s]")
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")
_MAX_UNESCAPE_PASSES = 4

VALID_DIRECTIONS = ("LONG", "SHORT", "NEUTRAL", "INSUFFICIENT_DATA")

# Which per-document statistic the daily mean is taken over.
#   "polarity" -- (P-N)/(P+N). Bounded and comparable across sources, but it
#                 saturates at +/-1.0 as soon as the matched terms share a sign,
#                 which on short news text is most of the time.
#   "lm_tone"  -- (P-N)/tokens, the Loughran-McDonald normalisation. Retains
#                 intensity and document length, but its scale depends on
#                 typical document length and so differs between a headline feed
#                 and a filings feed.
# The two are different units. A baseline built for one is invalid for the other.
VALID_SCORE_METRICS = ("polarity", "lm_tone")


class SentimentPipelineError(ValueError):
    """Raised on malformed input or an unusable configuration.

    Subclasses :class:`ValueError` so existing ``except ValueError`` handlers
    keep working.
    """


def load_lm_lexicon_from_master_dictionary(
    csv_path: str,
    word_column: str = "Word",
    positive_column: str = "Positive",
    negative_column: str = "Negative",
) -> Tuple[Set[str], Set[str]]:
    """Load the full LM positive/negative word lists from the Master Dictionary CSV.

    The Master Dictionary encodes category membership as the year the word
    entered that category, so any non-zero value means "in this category".

    :param csv_path: Path to ``Loughran-McDonald_MasterDictionary_*.csv``.
    :returns: ``(positive_words, negative_words)``, lowercased.
    :raises SentimentPipelineError: if the expected columns are absent or either
        list comes back empty.
    """
    positive: Set[str] = set()
    negative: Set[str] = set()
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = {word_column, positive_column, negative_column} - set(reader.fieldnames or ())
        if missing:
            raise SentimentPipelineError(
                f"Master Dictionary at {csv_path!r} is missing column(s): {sorted(missing)}"
            )
        for row in reader:
            word = (row[word_column] or "").strip().lower()
            if not word:
                continue
            if (row[positive_column] or "0").strip() not in ("0", ""):
                positive.add(word)
            if (row[negative_column] or "0").strip() not in ("0", ""):
                negative.add(word)
    if not positive or not negative:
        raise SentimentPipelineError(
            f"Master Dictionary at {csv_path!r} yielded {len(positive)} positive and "
            f"{len(negative)} negative words; expected both to be non-empty."
        )
    logger.info(
        "Loaded LM lexicon from %s: %d positive, %d negative words.",
        csv_path, len(positive), len(negative),
    )
    return positive, negative


@dataclass
class RawScrapedItem:
    """One scraped document awaiting cleaning and scoring.

    :param timestamp: Publication time. **Must be timezone-aware** -- a naive
        datetime cannot be placed on a trading day without guessing an offset,
        and guessing is how future data leaks into a backtest.
    """

    item_id: str
    source: str                                 # e.g. "NEWS", "REDDIT", "TWITTER", "STOCKTWITS"
    timestamp: datetime.datetime
    ticker: str
    text_content: str

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise SentimentPipelineError("item_id must be a non-empty string.")
        if not isinstance(self.source, str) or not self.source.strip():
            raise SentimentPipelineError(f"[{self.item_id}] source must be a non-empty string.")
        if not isinstance(self.timestamp, datetime.datetime):
            raise SentimentPipelineError(f"[{self.item_id}] timestamp must be a datetime.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise SentimentPipelineError(
                f"[{self.item_id}] timestamp {self.timestamp!r} is timezone-naive. "
                "Supply a timezone-aware datetime; the engine will not assume UTC."
            )
        if not isinstance(self.ticker, str) or not _TICKER_RE.match(self.ticker.strip().upper()):
            raise SentimentPipelineError(
                f"[{self.item_id}] ticker {self.ticker!r} is not a plausible symbol."
            )
        if not isinstance(self.text_content, str):
            raise SentimentPipelineError(
                f"[{self.item_id}] text_content must be a string, "
                f"got {type(self.text_content).__name__}."
            )


@dataclass
class ScoredSentimentItem:
    """A scored document, carrying the evidence behind its score."""

    item_id: str
    ticker: str
    timestamp: datetime.datetime
    positive_count: int
    negative_count: int
    raw_sentiment_score: float                  # polarity (P-N)/(P+N), in [-1.0, +1.0]
    clean_text: str
    total_tokens: int = 0
    matched_word_count: int = 0                 # P+N; the evidence behind the polarity
    lm_tone: float = 0.0                        # (P-N)/total_tokens, the LM normalisation
    negated_matches: int = 0
    is_duplicate: bool = False                  # near-duplicate of an earlier item in the batch
    duplicate_of: Optional[str] = None


@dataclass
class SentimentSignal:
    """Directional output plus the exclusion counters that justify it.

    ``sentiment_zscore`` is ``None`` when the batch was not measurable. ``None``
    means "not measurable" and must be rendered as such, never coerced to 0.
    """

    ticker: str
    signal_date: datetime.date
    current_sentiment_mean: Optional[float]
    baseline_mean: Optional[float]
    baseline_std: Optional[float]
    sentiment_zscore: Optional[float]
    direction: str                              # one of VALID_DIRECTIONS
    confidence_score: float                     # bounded conviction ratio [0,1]; NOT a probability
    items_considered: int = 0
    baseline_observations: int = 0
    future_items_excluded: int = 0
    stale_items_excluded: int = 0
    duplicate_items_excluded: int = 0
    low_evidence_items_excluded: int = 0
    reason: str = ""


class WebScrapedSentimentPipelineEngine:
    """Clean, score and standardise web-scraped financial text into a signal.

    The engine refuses to publish a Z-score it cannot support: too few
    documents, too little lexicon evidence per document, or too short or
    degenerate a baseline all yield ``INSUFFICIENT_DATA`` with
    ``sentiment_zscore=None`` rather than a confident ``NEUTRAL``.
    """

    def __init__(
        self,
        zscore_threshold: float = 1.5,
        min_matched_words: int = 2,
        min_items: int = 3,
        min_baseline_observations: int = 20,
        min_baseline_std: float = 1e-9,
        aggregation_window_days: int = 1,
        session_timezone: datetime.tzinfo = datetime.timezone.utc,
        exclude_filing_specific_terms: bool = True,
        conviction_saturation_multiple: float = 2.0,
        score_metric: str = "polarity",
        positive_words: Optional[Iterable[str]] = None,
        negative_words: Optional[Iterable[str]] = None,
    ) -> None:
        """
        :param zscore_threshold: ``|Z|`` at or beyond which a direction is emitted.
        :param min_matched_words: Documents matching fewer than this many lexicon
            terms are excluded from the aggregate. At one matched word the
            polarity ``(P-N)/(P+N)`` saturates at exactly +/-1.0, so a one-word
            match is indistinguishable from a fifty-word match. House default,
            not a standard.
        :param min_items: Minimum surviving documents required to compute a mean.
        :param min_baseline_observations: Minimum baseline observations required
            for a usable sigma.
        :param min_baseline_std: Floor on baseline dispersion. Both supported
            metrics live in ``[-1, +1]``, so a sigma below this is arithmetic
            noise rather than variation: dividing by it yields an arbitrarily
            large Z that reports as a maximum-conviction signal while
            ``baseline_std`` rounds to 0.0 in the output.
        :param aggregation_window_days: Calendar-day window ending on
            ``signal_date`` (inclusive) from which documents are aggregated. 1
            reproduces the documented daily mean.
        :param session_timezone: Timezone in which ``signal_date`` boundaries are
            resolved. Must be a concrete tzinfo.
        :param exclude_filing_specific_terms: Drop LM terms whose polarity is
            specific to 10-K filings. See :data:`FILING_SPECIFIC_TERMS`.
        :param conviction_saturation_multiple: ``confidence_score`` reaches 1.0 at
            ``|Z| = zscore_threshold * multiple``. A presentation scale, not a
            calibrated probability.
        :param score_metric: Which per-document statistic the daily mean is taken
            over -- ``"polarity"`` or ``"lm_tone"``. See
            :data:`VALID_SCORE_METRICS`. Changing it changes the units of the
            aggregate, so the baseline must be rebuilt.
        :param positive_words: Override the bundled positive list, e.g. with the
            full dictionary from :func:`load_lm_lexicon_from_master_dictionary`.
        :param negative_words: Override the bundled negative list.
        :raises SentimentPipelineError: on any invalid configuration.
        """
        if isinstance(zscore_threshold, bool) or not isinstance(zscore_threshold, (int, float)):
            raise SentimentPipelineError("zscore_threshold must be a number.")
        if not math.isfinite(zscore_threshold) or zscore_threshold <= 0:
            raise SentimentPipelineError(
                f"zscore_threshold must be finite and > 0, got {zscore_threshold!r}."
            )
        if (isinstance(min_matched_words, bool)
                or not isinstance(min_matched_words, int)
                or min_matched_words < 1):
            raise SentimentPipelineError("min_matched_words must be an integer >= 1.")
        if isinstance(min_items, bool) or not isinstance(min_items, int) or min_items < 1:
            raise SentimentPipelineError("min_items must be an integer >= 1.")
        if (isinstance(min_baseline_observations, bool)
                or not isinstance(min_baseline_observations, int)
                or min_baseline_observations < 2):
            raise SentimentPipelineError(
                "min_baseline_observations must be an integer >= 2; a sample standard "
                "deviation is undefined for a single observation."
            )
        if (isinstance(min_baseline_std, bool)
                or not isinstance(min_baseline_std, (int, float))
                or not math.isfinite(min_baseline_std)
                or min_baseline_std <= 0):
            raise SentimentPipelineError("min_baseline_std must be finite and > 0.")
        if (isinstance(aggregation_window_days, bool)
                or not isinstance(aggregation_window_days, int)
                or aggregation_window_days < 1):
            raise SentimentPipelineError("aggregation_window_days must be an integer >= 1.")
        if not isinstance(session_timezone, datetime.tzinfo):
            raise SentimentPipelineError("session_timezone must be a datetime.tzinfo instance.")
        if (isinstance(conviction_saturation_multiple, bool)
                or not isinstance(conviction_saturation_multiple, (int, float))
                or not math.isfinite(conviction_saturation_multiple)
                or conviction_saturation_multiple <= 0):
            raise SentimentPipelineError("conviction_saturation_multiple must be finite and > 0.")
        if score_metric not in VALID_SCORE_METRICS:
            raise SentimentPipelineError(
                f"score_metric must be one of {VALID_SCORE_METRICS}, got {score_metric!r}."
            )

        self.zscore_threshold = float(zscore_threshold)
        self.min_matched_words = min_matched_words
        self.min_items = min_items
        self.min_baseline_observations = min_baseline_observations
        self.min_baseline_std = float(min_baseline_std)
        self.aggregation_window_days = aggregation_window_days
        self.session_timezone = session_timezone
        self.conviction_saturation_multiple = float(conviction_saturation_multiple)
        self.score_metric = score_metric

        positive = set(positive_words) if positive_words is not None else set(LM_POSITIVE_WORDS)
        negative = set(negative_words) if negative_words is not None else set(LM_NEGATIVE_WORDS)
        if exclude_filing_specific_terms:
            positive -= FILING_SPECIFIC_TERMS
            negative -= FILING_SPECIFIC_TERMS
        overlap = positive & negative
        if overlap:
            raise SentimentPipelineError(
                f"A word cannot be both positive and negative: {sorted(overlap)}"
            )
        if not positive or not negative:
            raise SentimentPipelineError("Both positive and negative word lists must be non-empty.")
        self.positive_words = frozenset(word.lower() for word in positive)
        self.negative_words = frozenset(word.lower() for word in negative)

        logger.info(
            "Initialized sentiment pipeline: |Z|>=%.2f, min_matched_words=%d, min_items=%d, "
            "min_baseline_observations=%d, window=%dd, metric=%s, lexicon=%d pos / %d neg.",
            self.zscore_threshold, self.min_matched_words, self.min_items,
            self.min_baseline_observations, self.aggregation_window_days,
            self.score_metric, len(self.positive_words), len(self.negative_words),
        )

    # ------------------------------------------------------------------ text

    def clean_text(self, text: str) -> str:
        """Strip markup, URLs and punctuation; lowercase; collapse whitespace.

        ``<script>``/``<style>`` bodies are removed before tag stripping so their
        contents do not survive as tokens, and HTML entities are unescaped before
        punctuation removal so ``&amp;`` does not leave the token ``amp``.

        :raises SentimentPipelineError: if ``text`` is not a string.
        """
        if not isinstance(text, str):
            raise SentimentPipelineError(
                f"clean_text expects a string, got {type(text).__name__}."
            )
        if not text:
            return ""

        clean = _SCRIPT_STYLE_RE.sub(" ", text)
        clean = _UNCLOSED_SCRIPT_STYLE_RE.sub(" ", clean)
        clean = _TAG_RE.sub(" ", clean)
        # Scraped pages are routinely double-escaped ("&amp;amp;"), and a
        # single pass would leave the token "amp" once punctuation is
        # stripped. Unescape to a fixed point, bounded so a pathological
        # input cannot spin.
        for _ in range(_MAX_UNESCAPE_PASSES):
            unescaped = html.unescape(clean)
            if unescaped == clean:
                break
            clean = unescaped
        # Unescaping can reveal entity-encoded markup; strip once more.
        clean = _SCRIPT_STYLE_RE.sub(" ", clean)
        clean = _UNCLOSED_SCRIPT_STYLE_RE.sub(" ", clean)
        clean = _TAG_RE.sub(" ", clean)
        clean = _URL_RE.sub(" ", clean)
        # \w keeps underscores, so split them explicitly rather than gluing
        # "record_loss" into a single unmatched token.
        clean = _NON_WORD_RE.sub(" ", clean).replace("_", " ")
        return " ".join(clean.lower().split())

    def score_text(self, clean_text: str) -> Tuple[int, int, float]:
        """Score cleaned text against the lexicon.

        A term within :data:`NEGATION_WINDOW` tokens after a negator is counted
        with its polarity flipped, so "not profitable" scores negative.

        :returns: ``(positive_count, negative_count, polarity)`` where polarity is
            ``(P-N)/(P+N)`` in ``[-1.0, +1.0]``, or ``0.0`` when nothing matched.
            **Polarity is not the LM tone measure** -- Loughran and McDonald
            normalise word-list counts by the total number of words in the
            document. Callers needing intensity should use
            :meth:`process_scraped_feed`, whose records also carry ``lm_tone``
            and ``matched_word_count``.
        :raises SentimentPipelineError: if ``clean_text`` is not a string.
        """
        pos_count, neg_count, _, matched, _ = self._score_tokens(clean_text)
        polarity = 0.0 if matched == 0 else (pos_count - neg_count) / float(matched)
        return pos_count, neg_count, round(polarity, 4)

    def _score_tokens(self, clean_text: str) -> Tuple[int, int, int, int, int]:
        """:returns: ``(positive, negative, total_tokens, matched, negated)``."""
        if not isinstance(clean_text, str):
            raise SentimentPipelineError(
                f"score_text expects a string, got {type(clean_text).__name__}."
            )
        tokens = clean_text.split()
        if not tokens:
            return 0, 0, 0, 0, 0

        pos_count = 0
        neg_count = 0
        negated = 0
        last_negator_at: Optional[int] = None
        for index, token in enumerate(tokens):
            if token in NEGATORS:
                last_negator_at = index
                continue
            is_pos = token in self.positive_words
            is_neg = token in self.negative_words
            if not (is_pos or is_neg):
                continue
            if last_negator_at is not None and (index - last_negator_at) <= NEGATION_WINDOW:
                negated += 1
                is_pos, is_neg = is_neg, is_pos
            if is_pos:
                pos_count += 1
            else:
                neg_count += 1

        return pos_count, neg_count, len(tokens), pos_count + neg_count, negated

    # ------------------------------------------------------------------ feed

    def process_scraped_feed(
        self, items: Sequence[RawScrapedItem]
    ) -> List[ScoredSentimentItem]:
        """Clean, score and duplicate-mark a batch of raw items.

        Near-duplicates (same ticker, identical cleaned text) are **marked**, not
        dropped, so the batch stays auditable at one record per input;
        :meth:`generate_ticker_signals` excludes them from the aggregate. The
        copy left unmarked is the **earliest** by timestamp regardless of the
        order the caller supplied, because the survivor's timestamp decides
        which day the document lands on.
        Syndicated wire copy reposted across PR Newswire, Business Wire and a
        dozen aggregators is one opinion, and counting it a dozen times inflates
        both the mean and the apparent sample size.

        :raises SentimentPipelineError: if ``items`` is not a sequence of
            :class:`RawScrapedItem`. A malformed item fails the batch rather than
            being silently dropped.
        """
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise SentimentPipelineError(
                "process_scraped_feed expects a sequence of RawScrapedItem."
            )

        # Two passes: the copy that survives deduplication must be the
        # *earliest* one, not whichever the caller happened to list first. The
        # survivor's timestamp decides which day the document lands on, so
        # keeping a later repost can push it past the point-in-time cutoff.
        cleaned: List[Tuple[RawScrapedItem, str, str]] = []
        for position, item in enumerate(items):
            if not isinstance(item, RawScrapedItem):
                raise SentimentPipelineError(
                    f"items[{position}] is {type(item).__name__}, expected RawScrapedItem."
                )
            cleaned.append(
                (item, item.ticker.strip().upper(), self.clean_text(item.text_content))
            )

        originals: Dict[Tuple[str, str], str] = {}
        ranked: Dict[Tuple[str, str], Tuple[datetime.datetime, int]] = {}
        for position, (item, ticker, clean) in enumerate(cleaned):
            if not clean:
                continue
            key = (ticker, clean)
            rank = (item.timestamp, position)
            if key not in ranked or rank < ranked[key]:
                ranked[key] = rank
                originals[key] = item.item_id

        scored_results: List[ScoredSentimentItem] = []

        for item, ticker, clean in cleaned:
            pos_c, neg_c, total_tokens, matched, negated = self._score_tokens(clean)
            polarity = 0.0 if matched == 0 else (pos_c - neg_c) / float(matched)
            tone = 0.0 if total_tokens == 0 else (pos_c - neg_c) / float(total_tokens)

            original_id: Optional[str] = None
            if clean:
                kept = originals[(ticker, clean)]
                if kept != item.item_id:
                    original_id = kept

            scored_results.append(
                ScoredSentimentItem(
                    item_id=item.item_id,
                    ticker=ticker,
                    timestamp=item.timestamp,
                    positive_count=pos_c,
                    negative_count=neg_c,
                    raw_sentiment_score=round(polarity, 4),
                    clean_text=clean,
                    total_tokens=total_tokens,
                    matched_word_count=matched,
                    lm_tone=round(tone, 6),
                    negated_matches=negated,
                    is_duplicate=original_id is not None,
                    duplicate_of=original_id,
                )
            )

        duplicates = sum(1 for scored in scored_results if scored.is_duplicate)
        logger.info(
            "Processed %d scraped items (%d near-duplicates marked).",
            len(scored_results), duplicates,
        )
        return scored_results

    # ---------------------------------------------------------------- signal

    def generate_ticker_signals(
        self,
        scored_items: Sequence[ScoredSentimentItem],
        target_ticker: str,
        signal_date: datetime.date,
        historical_baseline_scores: Sequence[float],
    ) -> SentimentSignal:
        """Standardise the point-in-time daily mean against the baseline.

        The aggregation window ends at the close of ``signal_date`` in
        ``session_timezone`` and spans ``aggregation_window_days``. Documents
        stamped after that boundary are **excluded and counted**, never averaged
        in: ``signal_date`` is a cutoff, not a label.

        ``historical_baseline_scores`` must be past values of *this same daily
        aggregate* for this ticker, under the same ``score_metric`` -- not
        individual document scores. The
        standard deviation of a mean of ``n`` observations is smaller than the
        per-document standard deviation by roughly ``sqrt(n)``, so a per-document
        sigma in the denominator understates every Z by that factor. The engine
        cannot detect which one it was handed.

        :raises SentimentPipelineError: on invalid arguments, a timezone-naive
            document timestamp, or a baseline containing non-finite values.
        """
        if isinstance(scored_items, (str, bytes)) or not isinstance(scored_items, Sequence):
            raise SentimentPipelineError(
                "scored_items must be a sequence of ScoredSentimentItem."
            )
        if not isinstance(target_ticker, str) or not target_ticker.strip():
            raise SentimentPipelineError("target_ticker must be a non-empty string.")
        if not isinstance(signal_date, datetime.date) or isinstance(signal_date, datetime.datetime):
            raise SentimentPipelineError(
                "signal_date must be a datetime.date (not a datetime); the trading day is "
                "resolved in session_timezone."
            )
        if isinstance(historical_baseline_scores, (str, bytes)) or not isinstance(
            historical_baseline_scores, Sequence
        ):
            raise SentimentPipelineError(
                "historical_baseline_scores must be a sequence of numbers."
            )

        baseline = list(historical_baseline_scores)
        for position, value in enumerate(baseline):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SentimentPipelineError(
                    f"historical_baseline_scores[{position}] is {type(value).__name__}, "
                    "expected a number."
                )
            if not math.isfinite(value):
                raise SentimentPipelineError(
                    f"historical_baseline_scores[{position}] is {value!r}; NaN and infinity "
                    "have no Z-score interpretation."
                )

        ticker = target_ticker.strip().upper()
        try:
            window_end = datetime.datetime.combine(
                signal_date + datetime.timedelta(days=1),
                datetime.time.min,
                tzinfo=self.session_timezone,
            )
            window_start = datetime.datetime.combine(
                signal_date - datetime.timedelta(days=self.aggregation_window_days - 1),
                datetime.time.min,
                tzinfo=self.session_timezone,
            )
        except (OverflowError, ValueError) as exc:
            raise SentimentPipelineError(
                f"Cannot build a {self.aggregation_window_days}-day window ending "
                f"{signal_date}: the boundary falls outside the representable date "
                f"range ({exc})."
            ) from exc

        future = stale = duplicates = low_evidence = 0
        eligible: List[ScoredSentimentItem] = []
        for position, scored in enumerate(scored_items):
            if not isinstance(scored, ScoredSentimentItem):
                raise SentimentPipelineError(
                    f"scored_items[{position}] is {type(scored).__name__}, "
                    "expected ScoredSentimentItem."
                )
            if scored.ticker != ticker:
                continue
            if scored.timestamp.tzinfo is None or scored.timestamp.utcoffset() is None:
                raise SentimentPipelineError(
                    f"[{scored.item_id}] timestamp is timezone-naive; a point-in-time cutoff "
                    "cannot be applied to it."
                )
            if scored.timestamp >= window_end:
                future += 1
                continue
            if scored.timestamp < window_start:
                stale += 1
                continue
            if scored.is_duplicate:
                duplicates += 1
                continue
            if scored.matched_word_count < self.min_matched_words:
                low_evidence += 1
                continue
            eligible.append(scored)

        counters = dict(
            future_items_excluded=future,
            stale_items_excluded=stale,
            duplicate_items_excluded=duplicates,
            low_evidence_items_excluded=low_evidence,
        )
        if future:
            logger.warning(
                "[%s] Excluded %d document(s) stamped at or after %s; including them would "
                "have been look-ahead.",
                ticker, future, window_end.isoformat(),
            )

        if len(eligible) < self.min_items:
            return self._insufficient(
                ticker, signal_date,
                reason=(
                    f"{len(eligible)} eligible document(s) < min_items={self.min_items}; "
                    "a mean over fewer documents is not interpretable as sentiment."
                ),
                items_considered=len(eligible),
                baseline_observations=len(baseline),
                **counters,
            )

        current_mean = sum(self._metric(scored) for scored in eligible) / len(eligible)

        if len(baseline) < self.min_baseline_observations:
            return self._insufficient(
                ticker, signal_date,
                reason=(
                    f"{len(baseline)} baseline observation(s) < "
                    f"min_baseline_observations={self.min_baseline_observations}."
                ),
                items_considered=len(eligible),
                current_sentiment_mean=round(current_mean, 4),
                baseline_observations=len(baseline),
                **counters,
            )

        try:
            base_mean = sum(baseline) / len(baseline)
            variance = sum((x - base_mean) ** 2 for x in baseline) / (len(baseline) - 1)
            base_std = math.sqrt(variance)
        except OverflowError as exc:
            raise SentimentPipelineError(
                "Baseline dispersion overflowed. Both supported metrics live in [-1, +1]; "
                f"values of this magnitude are not sentiment aggregates ({exc})."
            ) from exc
        if not math.isfinite(base_std) or base_std < self.min_baseline_std:
            return self._insufficient(
                ticker, signal_date,
                reason=(
                    f"Baseline standard deviation is {base_std!r}, below min_baseline_std="
                    f"{self.min_baseline_std!r}; a constant or near-constant baseline has no "
                    "Z-score interpretation, and substituting sigma=1.0 would fabricate the "
                    "denominator."
                ),
                items_considered=len(eligible),
                current_sentiment_mean=round(current_mean, 4),
                baseline_mean=round(base_mean, 4),
                baseline_observations=len(baseline),
                **counters,
            )

        z_score = (current_mean - base_mean) / base_std

        # Band on the unrounded Z: rounding first would promote 1.4951 to 1.50
        # and emit a LONG the data does not support.
        if z_score >= self.zscore_threshold:
            direction = "LONG"
        elif z_score <= -self.zscore_threshold:
            direction = "SHORT"
        else:
            direction = "NEUTRAL"

        saturation = self.zscore_threshold * self.conviction_saturation_multiple
        confidence = round(min(1.0, abs(z_score) / saturation), 2)

        logger.info(
            "[%s] %s: mean=%+.4f baseline=%+.4f/%.4f (n=%d) Z=%+.2f from %d document(s) "
            "(excluded: %d future, %d stale, %d duplicate, %d low-evidence)",
            ticker, direction, current_mean, base_mean, base_std, len(baseline),
            z_score, len(eligible), future, stale, duplicates, low_evidence,
        )

        return SentimentSignal(
            ticker=ticker,
            signal_date=signal_date,
            current_sentiment_mean=round(current_mean, 4),
            baseline_mean=round(base_mean, 4),
            baseline_std=round(base_std, 4),
            sentiment_zscore=round(z_score, 2),
            direction=direction,
            confidence_score=confidence,
            items_considered=len(eligible),
            baseline_observations=len(baseline),
            reason="",
            **counters,
        )

    def _metric(self, scored: ScoredSentimentItem) -> float:
        """The per-document statistic the daily mean is taken over."""
        if self.score_metric == "lm_tone":
            return scored.lm_tone
        return scored.raw_sentiment_score

    @staticmethod
    def _insufficient(
        ticker: str,
        signal_date: datetime.date,
        reason: str,
        items_considered: int = 0,
        current_sentiment_mean: Optional[float] = None,
        baseline_mean: Optional[float] = None,
        baseline_observations: int = 0,
        **counters: int,
    ) -> SentimentSignal:
        """Build an ``INSUFFICIENT_DATA`` signal.

        Every statistic that was not actually computed stays ``None``. Reporting
        ``baseline_std=1.0`` for a baseline that was never usable would present a
        fabricated denominator as a measurement.
        """
        logger.warning("[%s] INSUFFICIENT_DATA on %s: %s", ticker, signal_date, reason)
        return SentimentSignal(
            ticker=ticker,
            signal_date=signal_date,
            current_sentiment_mean=current_sentiment_mean,
            baseline_mean=baseline_mean,
            baseline_std=None,
            sentiment_zscore=None,
            direction="INSUFFICIENT_DATA",
            confidence_score=0.0,
            items_considered=items_considered,
            baseline_observations=baseline_observations,
            reason=reason,
            **counters,
        )
