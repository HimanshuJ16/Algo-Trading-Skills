"""
social-media-sentiment-signal-with-bot-filtering: coarse bot/spam screening for
stock social-media posts, financial-lexicon sentiment scoring, and a baseline
Z-score signal that refuses to fire on a sample too small to interpret.

Purpose
-------
Social-media post streams for a ticker are contaminated. Cresci, Lillo, Regoli,
Tardelli & Tesconi (2019), "Cashtag Piggybacking", ACM Transactions on the Web
13(2), studied 9M stock-related tweets across the 5 main US markets and found
coordinated bot groups promoting low-value stocks by piggybacking on the
cashtags of high-value ones; as much as 71% of the authors of suspicious
financial tweets were classified as bots. Aggregating such a stream unfiltered
produces a sentiment mean that measures a campaign, not an opinion.

This module applies three screens and then standardizes:

  per-post  : account age, posting-rate burst, spam-pattern match
  per-batch : near-duplicate text collapse, one-vote-per-author aggregation
  gate      : no Z-score at all below `min_effective_sample` contributors
  signal    : Z = (filtered_mean - baseline_mean) / baseline_std

Baseline units (read before wiring this up)
-------------------------------------------
`historical_baseline_mean` and `historical_baseline_std` MUST describe the
distribution of *this same filtered aggregate statistic* over the trailing
baseline period -- e.g. the population of past daily filtered means for this
asset, computed with the same filters and the same aggregation window.

They must NOT be the mean and standard deviation of individual post scores. The
standard deviation of a mean of n observations is smaller than the per-post
standard deviation by a factor of roughly sqrt(n); feeding a per-post sigma into
the denominator therefore *understates* Z by that factor and mutes every signal.
The engine cannot detect which one it was handed -- this is a caller contract.

What the bot screen is and is not
---------------------------------
It is a coarse hygiene filter over the metadata a public API returns. It is not
bot detection. Cresci, Di Pietro, Petrocchi, Spognardi & Tesconi (2017), "The
Paradigm-Shift of Social Spambots", WWW '17 Companion, 963-972, benchmarked
Twitter itself, human annotators and state-of-the-art tools against modern
social spambots and found that none of them detect these accounts accurately
(human annotators scored under 24%). Assume a determined operator passes every
rule here. The screens raise the cost of a campaign; they do not close it.

Verified accounts
-----------------
`is_verified_user` is NOT identity verification and by default buys no
exemption from any rule. X's published criteria for the blue checkmark are an
active Premium subscription plus a display name, profile photo, confirmed phone
number and activity in the past 30 days (help.x.com, "About X Blue Checkmark");
X states the checkmark does not mean the account has been ID verified. On
5 December 2025 the European Commission fined X EUR 120 million under the
Digital Services Act, finding the blue checkmark deceptive because "anyone can
pay to obtain the 'verified' status without the company meaningfully verifying
who is behind the account" (IP/25/2934). Set `trust_verified_accounts=True`
only for a platform whose badge you have independently established to mean
identity verification.

Look-ahead
----------
Timestamps must be timezone-aware; naive input is rejected. Pass `as_of` to
apply a point-in-time cutoff -- posts stamped after it are excluded and counted
in `future_posts_excluded_count`. Without `as_of` no cutoff is applied at all
and preventing look-ahead is entirely the caller's problem.

A second, subtler leak the engine cannot fix: `user_account_age_days`,
`user_follower_count` and `user_posts_last_hour` are as-of-query values. Fetched
today for a two-year-old post they describe the account now, not at posting
time, and every account passes the age screen in hindsight. A defensible
backtest needs these fields snapshotted at ingestion, not backfilled.

Limitations (documented, deliberate)
------------------------------------
- **Lexicon scoring is shallow.** Word matching with a 3-token negation window;
  no sarcasm, no sentence boundaries, no dependency parse. "Not exactly bullish
  after the crash" is scored on tokens, not meaning.
- **`call`/`calls`/`put`/`puts` are instrument names, not directions.** "Sold
  calls" is a bearish position scored bullish here. Strip or reweight them for
  an options-heavy venue.
- **`moon`, `rocket`, `breakout` are also the vocabulary of the campaigns this
  module screens for.** A pump that clears the filters scores strongly bullish.
- **Distinct terms are counted once per post.** "moon moon moon moon" scores the
  same +1.0 as "moon", so one post cannot buy intensity by repetition.
- **Neutral posts count.** A post with no lexicon hit contributes 0.0 to the
  mean, so a flood of unrelated chatter dilutes the signal rather than being
  ignored. That is intended: it measures the balance of opinion, not its peak.
- **The lexicon is English-only.** A post in another language matches nothing and
  contributes 0.0, so a non-English venue is measured as permanently neutral.
- **Duplicate collapse is deliberately aggressive** and can merge genuinely
  independent short posts ("bullish"). It only ever removes contributions, so it
  biases toward INSUFFICIENT_DATA and never inflates a signal.
- **Duplicate collapse is exact-fingerprint, not fuzzy.** Appending a counter or
  swapping one word per copy defeats it; near-duplicate clustering (MinHash,
  shingling) is the next escalation and is out of scope here.
- **Spam obfuscation is adaptive.** `_normalise_for_spam` undoes a handful of
  known dot-obfuscations; a rewritten payload defeats it the same day.
- **The spam list over-rejects.** Patterns such as `whatsapp` match ordinary
  posts that merely mention the app. The bias is deliberate -- a false rejection
  costs one contribution, a false acceptance admits a campaign -- but it means
  the rejection counts are not a measurement of how much spam the feed carries.
- **No cross-asset context.** The batch is one asset. Cashtag piggybacking is
  visible across assets, which this module never sees.
- **Thresholds are house defaults, not standards.** See references/standards.md.
"""
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

SIGNAL_STRONG_BULLISH = "STRONG_BULLISH"
SIGNAL_BULLISH = "BULLISH"
SIGNAL_NEUTRAL = "NEUTRAL"
SIGNAL_BEARISH = "BEARISH"
SIGNAL_STRONG_BEARISH = "STRONG_BEARISH"
SIGNAL_INSUFFICIENT = "INSUFFICIENT_DATA"

REASON_YOUNG_ACCOUNT = "YOUNG_ACCOUNT"
REASON_HIGH_FREQUENCY_BURST = "HIGH_FREQUENCY_BURST"
REASON_SPAM_PATTERN_MATCH = "SPAM_PATTERN_MATCH"


def _parse_iso_timestamp(value: str, context: str) -> datetime:
    """
    Parses an ISO-8601 timestamp and rejects timezone-naive values.

    A naive post timestamp carries no time base, so comparing it against an
    `as_of` cutoff silently mixes clocks -- the exact shape of a look-ahead leak
    in a backtest. X's API v2 stamps `created_at` in ISO-8601/RFC 3339 UTC with a
    trailing 'Z' (X API v2 data dictionary), so real feed data already satisfies
    this; a bare "2026-08-05" does not and is refused rather than assumed UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: created_at_iso must be a non-empty ISO-8601 string")
    raw = value.strip()
    # datetime.fromisoformat does not accept a trailing 'Z' before Python 3.11.
    normalised = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(f"{context}: '{raw}' is not a valid ISO-8601 timestamp ({exc})") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"{context}: '{raw}' is timezone-naive. Post timestamps must carry an explicit "
            "offset (e.g. '2026-08-05T14:30:00Z') so the point-in-time cutoff compares "
            "against a known clock."
        )
    return parsed


def _require_non_negative_int(value: object, name: str, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{context}: {name} must be an int, got {value!r}")
    if value < 0:
        raise ValueError(f"{context}: {name} must be non-negative, got {value}")
    return value


@dataclass
class SocialPost:
    """
    One post as returned by a social API.

    The three user counters must be snapshotted at ingestion time. Backfilling
    them from a later query makes every historical account look established.
    `user_follower_count` is carried for audit and downstream weighting and is
    deliberately not used as a filter: follower counts are cheaply purchased and
    a threshold on them would be an invented number.
    """
    post_id: str
    asset_id: str
    user_id: str
    created_at_iso: str                  # ISO-8601, timezone-aware (naive is rejected)
    text: str
    user_account_age_days: int
    user_follower_count: int
    user_posts_last_hour: int
    is_verified_user: bool = False


@dataclass
class BotFilteringResult:
    """Outcome of the per-post screens only. Batch-level screens are not visible here."""
    post_id: str
    is_bot_or_spam: bool
    rejection_reasons: List[str]
    clean_sentiment_score: float         # [-1.0, +1.0]; forced to 0.0 for rejected posts


@dataclass
class SocialSentimentSignal:
    """
    Aggregate result for one asset over one batch.

    `sentiment_z_score` is None -- never 0.0 -- when the batch did not clear
    `min_effective_sample`. None means "not measurable" and must be rendered as
    such, not coerced to a number. `directional_signal` is then
    INSUFFICIENT_DATA, which is a different statement from NEUTRAL: NEUTRAL means
    the balance of opinion was measured and was flat.
    """
    asset_id: str
    total_posts_analyzed: int
    future_posts_excluded_count: int     # stamped after `as_of` -- look-ahead exclusions
    stale_posts_excluded_count: int      # older than the lookback window
    bot_posts_filtered_count: int
    duplicate_posts_filtered_count: int
    clean_posts_count: int
    distinct_authors_count: int
    effective_sample_size: int           # contributions the mean was actually taken over
    raw_sentiment_mean: float            # unfiltered, in-window; for comparison only
    filtered_sentiment_mean: float
    sentiment_z_score: Optional[float]   # vs the baseline of this same statistic
    directional_signal: str
    is_signal_measurable: bool
    audit_notes: str


class SocialMediaSentimentSignalWithBotFilteringEngine:
    """
    Coarse bot/spam screening, financial-lexicon sentiment scoring, and a gated
    baseline Z-score signal for one asset's social-media post stream.

    Screens are split by what they can see. `filter_post` applies the rules that
    are decidable from a single post -- account age, posting rate, spam patterns.
    Coordination is a property of the *batch* and is invisible to a per-post
    rule, so near-duplicate collapse and one-vote-per-author aggregation are
    applied in `process_social_posts`.
    """

    # Financial bullish vs bearish lexicon.
    # 'pump' is deliberately absent from BULLISH: in stock social media it is the
    # vocabulary of the manipulation this module exists to screen out, and scoring
    # it bullish makes a surviving pump post push the signal the way the campaign
    # intends. It is also matched as a cashtag spam pattern below.
    BULLISH_KEYWORDS = {
        "bullish", "moon", "breakout", "call", "calls", "long", "buy", "upgrade",
        "outperform", "rocket",
    }
    BEARISH_KEYWORDS = {
        "bearish", "dump", "crash", "put", "puts", "short", "sell", "downgrade",
        "underperform", "scam", "drop",
    }

    # Syntactic negators only. A lexicon hit within NEGATION_WINDOW tokens after
    # one of these has its polarity flipped ("not bullish" -> -1).
    NEGATION_TERMS = {
        "not", "no", "never", "none", "neither", "nor", "without", "hardly", "barely", "nope",
        "cannot", "cant", "can't", "dont", "don't", "doesnt", "doesn't", "didnt", "didn't",
        "isnt", "isn't", "arent", "aren't", "wasnt", "wasn't", "wont", "won't", "aint", "ain't",
    }
    NEGATION_WINDOW = 3

    SPAM_PATTERNS = [
        r"t\.me/", r"bit\.ly/", r"join my channel", r"guaranteed profit", r"free crypto",
        r"cashapp", r"whatsapp", r"signals link", r"\$pump",
    ]

    def __init__(
        self,
        config: Optional[dict] = None,
        min_account_age_days: int = 30,
        max_posts_per_hour: int = 40,
        historical_baseline_mean: float = 0.05,
        historical_baseline_std: float = 0.15,
        min_effective_sample: int = 20,
        one_vote_per_author: bool = True,
        collapse_duplicate_text: bool = True,
        trust_verified_accounts: bool = False,
        strong_signal_z: float = 2.0,
        signal_z: float = 0.75,
        lookback_window_minutes: Optional[int] = None,
    ) -> None:
        """
        Args:
            min_account_age_days: unverified accounts younger than this are rejected.
            max_posts_per_hour: accounts above this posting rate are rejected.
            historical_baseline_mean: mean of the *filtered aggregate statistic* over
                the baseline period. Not the mean of individual post scores -- see
                the module docstring.
            historical_baseline_std: standard deviation of that same statistic. Must
                be finite and strictly positive; a degenerate baseline is a
                configuration error, not a reason to emit Z = 0.
            min_effective_sample: below this many contributors the batch yields
                INSUFFICIENT_DATA and no Z-score. The default of 20 is a house
                floor, not a validated constant; calibrate it against the asset's
                normal post volume.
            one_vote_per_author: aggregate each author's posts to their mean before
                averaging across authors, so a single prolific account cannot carry
                the batch. Effective sample size is then the author count.
            collapse_duplicate_text: drop repeats of normalised post text within the
                batch, keeping the first occurrence.
            trust_verified_accounts: allow a platform verification badge to exempt an
                account from the age screen. Default False -- see the module
                docstring on what a paid checkmark does and does not establish.
            strong_signal_z / signal_z: |Z| thresholds for the STRONG_* and plain
                directional bands. House defaults, not validated constants.
            lookback_window_minutes: if set, posts older than `as_of` minus this are
                excluded. Requires `as_of` to be supplied per call.

        Raises:
            ValueError: on any out-of-range or non-finite configuration value.
        """
        self.config = config or {}
        self.min_account_age_days = _require_non_negative_int(
            min_account_age_days, "min_account_age_days", "config")

        if isinstance(max_posts_per_hour, bool) or not isinstance(max_posts_per_hour, int) \
                or max_posts_per_hour < 1:
            raise ValueError(f"config: max_posts_per_hour must be an int >= 1, got {max_posts_per_hour!r}")
        self.max_posts_per_hour = max_posts_per_hour

        if isinstance(historical_baseline_mean, bool) \
                or not isinstance(historical_baseline_mean, (int, float)) \
                or not math.isfinite(float(historical_baseline_mean)):
            raise ValueError(
                f"config: historical_baseline_mean must be finite, got {historical_baseline_mean!r}")
        self.baseline_mean = float(historical_baseline_mean)

        if isinstance(historical_baseline_std, bool) \
                or not isinstance(historical_baseline_std, (int, float)) \
                or not math.isfinite(float(historical_baseline_std)) \
                or float(historical_baseline_std) <= 0.0:
            raise ValueError(
                f"config: historical_baseline_std must be finite and strictly positive, got "
                f"{historical_baseline_std!r}. A zero, negative or NaN baseline sigma has no "
                "Z-score interpretation and must not be silently absorbed into Z = 0.")
        self.baseline_std = float(historical_baseline_std)

        if isinstance(min_effective_sample, bool) or not isinstance(min_effective_sample, int) \
                or min_effective_sample < 1:
            raise ValueError(
                f"config: min_effective_sample must be an int >= 1, got {min_effective_sample!r}")
        self.min_effective_sample = min_effective_sample

        for flag, name in ((one_vote_per_author, "one_vote_per_author"),
                           (collapse_duplicate_text, "collapse_duplicate_text"),
                           (trust_verified_accounts, "trust_verified_accounts")):
            if not isinstance(flag, bool):
                raise ValueError(f"config: {name} must be a bool, got {flag!r}")
        self.one_vote_per_author = one_vote_per_author
        self.collapse_duplicate_text = collapse_duplicate_text
        self.trust_verified_accounts = trust_verified_accounts

        for z_value, name in ((signal_z, "signal_z"), (strong_signal_z, "strong_signal_z")):
            if isinstance(z_value, bool) or not isinstance(z_value, (int, float)) \
                    or not math.isfinite(float(z_value)) or float(z_value) <= 0.0:
                raise ValueError(f"config: {name} must be finite and strictly positive, got {z_value!r}")
        if float(signal_z) >= float(strong_signal_z):
            raise ValueError(
                f"config: signal_z ({signal_z}) must be strictly less than strong_signal_z "
                f"({strong_signal_z}); otherwise the STRONG band is unreachable.")
        self.signal_z = float(signal_z)
        self.strong_signal_z = float(strong_signal_z)

        if lookback_window_minutes is not None:
            if isinstance(lookback_window_minutes, bool) \
                    or not isinstance(lookback_window_minutes, int) \
                    or lookback_window_minutes < 1:
                raise ValueError(
                    f"config: lookback_window_minutes must be an int >= 1 or None, got "
                    f"{lookback_window_minutes!r}")
        self.lookback_window_minutes = lookback_window_minutes

        # Compiled once per engine; read from self so a subclass may override SPAM_PATTERNS.
        self._spam_regexes: List[Tuple[str, "re.Pattern"]] = [
            (pattern, re.compile(pattern)) for pattern in self.SPAM_PATTERNS
        ]

    # ------------------------------------------------------------------ text

    @staticmethod
    def _normalise_for_spam(text: str) -> str:
        """
        Lowercases and undoes a few known link obfuscations before spam matching.

        Handles 't(dot)me', 't [dot] me', 't dot me' and the Unicode period
        look-alikes U+2024/U+FF0E/U+3002. This is a partial countermeasure: an
        operator who changes the payload defeats it immediately. It exists so the
        cheapest evasions do not pass, not because the list can ever be complete.
        """
        normalised = text.lower()
        for look_alike in ("․", "．", "。"):
            normalised = normalised.replace(look_alike, ".")
        normalised = re.sub(r"[​‌‍﻿]", "", normalised)
        normalised = re.sub(r"\s*[\(\[\{]\s*dot\s*[\)\]\}]\s*", ".", normalised)
        normalised = re.sub(r"\s+dot\s+", ".", normalised)
        return normalised

    @staticmethod
    def _normalise_for_fingerprint(text: str) -> str:
        """
        Reduces a post to the text that would have to differ for it to be a
        genuinely different message: links, cashtags, mentions, hashtag markers
        and punctuation removed, whitespace collapsed.

        Returns "" for a post that is nothing but links and tickers; such posts
        are never treated as duplicates of each other because there is no content
        left to compare.

        Punctuation is stripped with a Unicode-aware class rather than an ASCII
        one so that a non-Latin-script campaign still produces a comparable
        fingerprint. Dropping non-ASCII characters here would reduce every such
        post to "" and exempt the whole campaign from duplicate collapse.
        """
        normalised = text.lower()
        normalised = re.sub(r"https?://\S+", " ", normalised)
        normalised = re.sub(r"\bwww\.\S+", " ", normalised)
        normalised = re.sub(r"[@$#]\w+", " ", normalised)
        normalised = re.sub(r"[^\w\s]", " ", normalised)
        return re.sub(r"\s+", " ", normalised).strip()

    def _score_text_sentiment(self, text: str) -> float:
        """
        Financial-lexicon sentiment in [-1.0, +1.0].

        Each *distinct* lexicon term counts once, so repetition inside one post
        cannot manufacture intensity. A term preceded within NEGATION_WINDOW
        tokens by a syntactic negator has its polarity flipped, so "not bullish"
        scores -1.0 rather than +1.0. The score is
        (n_bullish - n_bearish) / (n_bullish + n_bearish), or 0.0 with no hits.
        """
        if not isinstance(text, str):
            raise ValueError(f"text must be a string, got {type(text).__name__}")
        tokens = re.findall(r"[a-z']+", text.lower())
        bullish_terms: Set[str] = set()
        bearish_terms: Set[str] = set()
        for index, token in enumerate(tokens):
            if token in self.BULLISH_KEYWORDS:
                polarity = 1
            elif token in self.BEARISH_KEYWORDS:
                polarity = -1
            else:
                continue
            window = tokens[max(0, index - self.NEGATION_WINDOW):index]
            if any(prior in self.NEGATION_TERMS for prior in window):
                polarity = -polarity
            (bullish_terms if polarity > 0 else bearish_terms).add(token)

        total = len(bullish_terms) + len(bearish_terms)
        if total == 0:
            return 0.0
        return (len(bullish_terms) - len(bearish_terms)) / float(total)

    # ------------------------------------------------------------ validation

    def _validate_post(self, post: object, context: str) -> Tuple[SocialPost, datetime]:
        """Validates one post and returns it with its parsed, timezone-aware timestamp."""
        if not isinstance(post, SocialPost):
            raise ValueError(f"{context}: expected a SocialPost, got {type(post).__name__}")
        for name in ("post_id", "asset_id", "user_id"):
            value = getattr(post, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{context}: {name} must be a non-empty string, got {value!r}")
        if not isinstance(post.text, str) or not post.text.strip():
            raise ValueError(
                f"{context}: text must be a non-empty string. A post with no text carries no "
                "sentiment; drop it upstream rather than scoring it as 0.0 and diluting the mean.")
        _require_non_negative_int(post.user_account_age_days, "user_account_age_days", context)
        _require_non_negative_int(post.user_follower_count, "user_follower_count", context)
        _require_non_negative_int(post.user_posts_last_hour, "user_posts_last_hour", context)
        if not isinstance(post.is_verified_user, bool):
            raise ValueError(
                f"{context}: is_verified_user must be a bool, got {post.is_verified_user!r}")
        timestamp = _parse_iso_timestamp(post.created_at_iso, context)
        return post, timestamp

    # -------------------------------------------------------- per-post screen

    def filter_post(self, post: SocialPost) -> BotFilteringResult:
        """
        Applies the screens decidable from a single post:

        1. Account age -- unverified accounts younger than `min_account_age_days`.
           The SEC's "Social Media and Investment Fraud" investor alert warns that
           "fraudsters can set up new accounts specifically designed to carry out
           their scam" and to "be skeptical of information from social media
           accounts that lack a history of prior postings". That supports the
           direction of this rule; the 30-day number is this engine's default, not
           anything the SEC specifies.
        2. Posting-rate burst -- strictly above `max_posts_per_hour`.
        3. Spam pattern -- first matching pattern only, so one post yields at most
           one SPAM_PATTERN_MATCH reason.

        Coordination between posts is invisible here; see `process_social_posts`.

        Raises:
            ValueError: if the post is malformed (see `_validate_post`).
        """
        self._validate_post(post, f"post {getattr(post, 'post_id', '?')!r}")
        rejections: List[str] = []

        verified_exemption = post.is_verified_user and self.trust_verified_accounts
        if post.user_account_age_days < self.min_account_age_days and not verified_exemption:
            rejections.append(
                f"{REASON_YOUNG_ACCOUNT}: Account age ({post.user_account_age_days}d) < "
                f"{self.min_account_age_days}d.")

        if post.user_posts_last_hour > self.max_posts_per_hour:
            rejections.append(
                f"{REASON_HIGH_FREQUENCY_BURST}: {post.user_posts_last_hour} posts/hr > Max "
                f"{self.max_posts_per_hour}.")

        normalised_text = self._normalise_for_spam(post.text)
        for pattern, regex in self._spam_regexes:
            if regex.search(normalised_text):
                rejections.append(f"{REASON_SPAM_PATTERN_MATCH}: Matched pattern '{pattern}'.")
                break

        is_bot = len(rejections) > 0
        sentiment = 0.0 if is_bot else self._score_text_sentiment(post.text)

        return BotFilteringResult(
            post_id=post.post_id,
            is_bot_or_spam=is_bot,
            rejection_reasons=rejections,
            clean_sentiment_score=sentiment,
        )

    # ------------------------------------------------------------ aggregation

    def _classify(self, z_score: float) -> str:
        """
        Bands the *unrounded* Z-score.

        Rounding before comparison promotes 1.995 to STRONG_BULLISH; the reported
        Z is rounded, the decision is not.
        """
        if z_score >= self.strong_signal_z:
            return SIGNAL_STRONG_BULLISH
        if z_score >= self.signal_z:
            return SIGNAL_BULLISH
        if z_score <= -self.strong_signal_z:
            return SIGNAL_STRONG_BEARISH
        if z_score <= -self.signal_z:
            return SIGNAL_BEARISH
        return SIGNAL_NEUTRAL

    def process_social_posts(
        self,
        asset_id: str,
        posts: Sequence[SocialPost],
        as_of: Optional[datetime] = None,
    ) -> SocialSentimentSignal:
        """
        Screens a batch for one asset and standardizes the surviving sentiment.

        Order of operations, each step feeding the next:
          1. Validate every post and reject the batch if any post is malformed or
             carries a different `asset_id` than requested.
          2. If `as_of` is given, drop posts stamped after it (look-ahead) and,
             when `lookback_window_minutes` is set, posts older than the window.
             The retained window is inclusive at both ends.
          3. Score the raw (unfiltered) mean over the in-window posts, for
             comparison only -- it is not the signal.
          4. Apply the per-post screens.
          5. Collapse near-duplicate text among the survivors.
          6. Aggregate each author to their mean, one vote per author.
          7. If contributors < `min_effective_sample`, emit INSUFFICIENT_DATA with
             `sentiment_z_score=None`. No Z-score is produced.
          8. Otherwise standardize against the baseline and band the unrounded Z.

        Raises:
            ValueError: on a malformed post, an asset_id mismatch, a naive or
                unparseable timestamp, a naive `as_of`, or a configured lookback
                window with no `as_of` to anchor it.
        """
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError(f"asset_id must be a non-empty string, got {asset_id!r}")
        asset_id = asset_id.strip()

        if posts is None:
            raise ValueError("posts must be a sequence of SocialPost, got None")
        post_list = list(posts)

        if as_of is not None:
            if not isinstance(as_of, datetime):
                raise ValueError(f"as_of must be a datetime or None, got {type(as_of).__name__}")
            if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
                raise ValueError(
                    "as_of must be timezone-aware so the point-in-time cutoff is unambiguous")
        elif self.lookback_window_minutes is not None:
            raise ValueError(
                "lookback_window_minutes is configured but as_of was not supplied; the window has "
                "nothing to anchor to and would be silently ignored.")

        validated: List[Tuple[SocialPost, datetime]] = []
        for index, post in enumerate(post_list):
            context = f"posts[{index}]"
            checked, timestamp = self._validate_post(post, context)
            if checked.asset_id.strip() != asset_id:
                raise ValueError(
                    f"{context}: asset_id {checked.asset_id!r} does not match the requested "
                    f"{asset_id!r}. Aggregating another asset's posts under this label would "
                    "publish a signal for an instrument it was never measured on.")
            validated.append((checked, timestamp))

        if not validated:
            return self._empty_signal(asset_id, "No posts to analyze.")

        # 2. Point-in-time window.
        future_excluded = 0
        stale_excluded = 0
        if as_of is None:
            in_window = validated
        else:
            window_start = (
                as_of - timedelta(minutes=self.lookback_window_minutes)
                if self.lookback_window_minutes is not None else None
            )
            in_window = []
            for post, timestamp in validated:
                if timestamp > as_of:
                    future_excluded += 1
                    continue
                if window_start is not None and timestamp < window_start:
                    stale_excluded += 1
                    continue
                in_window.append((post, timestamp))

        if not in_window:
            return self._empty_signal(
                asset_id,
                f"All {len(validated)} post(s) fell outside the point-in-time window "
                f"({future_excluded} after as_of, {stale_excluded} stale).",
                total_posts=len(validated),
                future_excluded=future_excluded,
                stale_excluded=stale_excluded,
            )

        # 3. Unfiltered comparison mean.
        raw_scores = [self._score_text_sentiment(post.text) for post, _ in in_window]
        raw_mean = sum(raw_scores) / len(raw_scores)

        # 4. Per-post screens.
        bot_count = 0
        survivors: List[Tuple[SocialPost, float]] = []
        for post, _ in in_window:
            result = self.filter_post(post)
            if result.is_bot_or_spam:
                bot_count += 1
            else:
                survivors.append((post, result.clean_sentiment_score))

        # 5. Near-duplicate collapse (batch-level; invisible to filter_post).
        duplicate_count = 0
        if self.collapse_duplicate_text:
            seen_fingerprints: Set[str] = set()
            deduplicated: List[Tuple[SocialPost, float]] = []
            for post, score in survivors:
                fingerprint = self._normalise_for_fingerprint(post.text)
                if fingerprint and fingerprint in seen_fingerprints:
                    duplicate_count += 1
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                deduplicated.append((post, score))
            survivors = deduplicated

        # 6. One vote per author.
        by_author: Dict[str, List[float]] = {}
        for post, score in survivors:
            by_author.setdefault(post.user_id.strip(), []).append(score)
        distinct_authors = len(by_author)
        if self.one_vote_per_author:
            contributions = [sum(scores) / len(scores) for scores in by_author.values()]
        else:
            contributions = [score for _, score in survivors]

        clean_count = len(survivors)
        effective_n = len(contributions)
        filtered_mean = sum(contributions) / effective_n if effective_n else 0.0

        # 7/8. Sample gate, then standardize.
        if effective_n < self.min_effective_sample:
            z_score: Optional[float] = None
            signal = SIGNAL_INSUFFICIENT
            measurable = False
        else:
            unrounded_z = (filtered_mean - self.baseline_mean) / self.baseline_std
            signal = self._classify(unrounded_z)
            z_score = round(unrounded_z, 4)
            measurable = True

        z_text = "n/a" if z_score is None else f"{z_score:+.2f}"
        notes = (
            f"SOCIAL SENTIMENT [{asset_id}]: In-window Posts = {len(in_window)}, "
            f"Excluded (future/stale) = {future_excluded}/{stale_excluded}, "
            f"Bots Filtered = {bot_count}, Duplicates Collapsed = {duplicate_count}, "
            f"Contributors = {effective_n} (min {self.min_effective_sample}), "
            f"Filtered Sentiment = {filtered_mean:+.3f} (Raw {raw_mean:+.3f}), "
            f"Z-Score = {z_text}, Signal = {signal}."
        )
        if measurable:
            logger.info(notes)
        else:
            logger.warning("%s Signal suppressed: sample too small to standardize.", notes)

        return SocialSentimentSignal(
            asset_id=asset_id,
            total_posts_analyzed=len(validated),
            future_posts_excluded_count=future_excluded,
            stale_posts_excluded_count=stale_excluded,
            bot_posts_filtered_count=bot_count,
            duplicate_posts_filtered_count=duplicate_count,
            clean_posts_count=clean_count,
            distinct_authors_count=distinct_authors,
            effective_sample_size=effective_n,
            raw_sentiment_mean=round(raw_mean, 4),
            filtered_sentiment_mean=round(filtered_mean, 4),
            sentiment_z_score=z_score,
            directional_signal=signal,
            is_signal_measurable=measurable,
            audit_notes=notes,
        )

    def _empty_signal(
        self,
        asset_id: str,
        reason: str,
        total_posts: int = 0,
        future_excluded: int = 0,
        stale_excluded: int = 0,
    ) -> SocialSentimentSignal:
        """
        Result for a batch with nothing left to measure.

        The signal is INSUFFICIENT_DATA, not NEUTRAL: no posts is an absence of
        evidence, and reporting it as a neutral market read invites a consumer to
        act on it.
        """
        notes = f"SOCIAL SENTIMENT [{asset_id}]: {reason} Signal = {SIGNAL_INSUFFICIENT}."
        logger.warning(notes)
        return SocialSentimentSignal(
            asset_id=asset_id,
            total_posts_analyzed=total_posts,
            future_posts_excluded_count=future_excluded,
            stale_posts_excluded_count=stale_excluded,
            bot_posts_filtered_count=0,
            duplicate_posts_filtered_count=0,
            clean_posts_count=0,
            distinct_authors_count=0,
            effective_sample_size=0,
            raw_sentiment_mean=0.0,
            filtered_sentiment_mean=0.0,
            sentiment_z_score=None,
            directional_signal=SIGNAL_INSUFFICIENT,
            is_signal_measurable=False,
            audit_notes=notes,
        )
