# Workflows for Social Media Sentiment Signal With Bot Filtering

The engine is deliberately split by *what a screen can see*. Rules decidable from a
single post live in `filter_post`; rules that require the whole batch live in
`process_social_posts`. Coordination is invisible to the first and is the point of
the second.

## 1. Ingestion

- Stream posts for one asset from the platform API (StockTwits, X/Twitter, Reddit).
- Capture `user_account_age_days`, `user_follower_count` and `user_posts_last_hour`
  **at ingestion time** and persist them with the post. These are as-of-query values;
  backfilling them from a later query makes every historical account look established
  and produces a backtest of a filter that never ran.
- Persist `created_at` as timezone-aware ISO-8601. X API v2 already returns UTC with a
  trailing `Z`; anything naive must be resolved upstream, not assumed to be UTC.
- One batch, one asset. A post carrying a different `asset_id` than the batch raises.

## 2. Point-in-time cutoff

- Pass an `as_of` instant per call. Posts stamped after it are excluded and counted in
  `future_posts_excluded_count`; without `as_of`, **no cutoff is applied at all** and
  the counters reading zero is not evidence of correctness.
- Set `lookback_window_minutes` to bound staleness. The retained window is
  $[\,as\_of - \text{window},\ as\_of\,]$, inclusive at both ends. Configuring the
  window without passing `as_of` raises rather than being silently ignored.

## 3. Per-post screens (`filter_post`)

Three rules, evaluated independently so the audit record shows every reason:

| Screen | Rule | Reason code |
|---|---|---|
| Account age | unverified and `user_account_age_days < min_account_age_days` | `YOUNG_ACCOUNT` |
| Posting burst | `user_posts_last_hour > max_posts_per_hour` (strictly) | `HIGH_FREQUENCY_BURST` |
| Spam pattern | first matching pattern only, after de-obfuscation | `SPAM_PATTERN_MATCH` |

- Text is normalised before matching: `(dot)`/`[dot]`/` dot ` become `.`, Unicode
  period look-alikes (U+2024, U+FF0E, U+3002) are folded, zero-width characters are
  stripped. This defeats the cheapest evasions and nothing more.
- A verification badge grants no exemption unless `trust_verified_accounts=True`.
- Rejected posts have their sentiment forced to `0.0` and are excluded from the
  filtered mean — but they remain in `raw_sentiment_mean`, which exists precisely so
  the filtered and unfiltered readings can be compared in the audit record.

## 4. Batch-level coordination screens (`process_social_posts`)

- **Near-duplicate collapse.** Post text is reduced to a fingerprint — links,
  cashtags, mentions, hashtag markers and punctuation removed, whitespace collapsed —
  and repeats are dropped, keeping the first occurrence. A post whose fingerprint is
  empty (nothing but a link and a ticker) is never treated as a duplicate, because
  there is no content left to compare.
- **One vote per author.** Posts are grouped by `user_id`, each author's scores are
  averaged to one contribution, and the batch mean is taken across authors. A single
  prolific account cannot carry the batch.
- The collapse is deliberately aggressive and can merge genuinely independent short
  posts. It only ever removes contributions, so the bias is toward
  `INSUFFICIENT_DATA` — it can mute a real signal but cannot manufacture one.

## 5. Sentiment scoring

$$S = \frac{n_{\text{bullish}} - n_{\text{bearish}}}{n_{\text{bullish}} + n_{\text{bearish}}} \in [-1, +1]$$

- Each **distinct** lexicon term counts once per post, so "moon moon moon moon" scores
  the same as "moon".
- A term within three tokens after a syntactic negator has its polarity flipped:
  "not bullish" $= -1.0$. The window is bounded and does not cross sentences, so
  "not a single one of them is bullish" still scores $+1.0$ — a known limitation.
- `pump` is deliberately absent from the bullish lexicon: it is the vocabulary of the
  manipulation this module screens for, and it remains matched as a `$pump` spam
  pattern.
- A post with no lexicon hit contributes `0.0`, so unrelated chatter dilutes the mean
  rather than being ignored. That is intended — the statistic is the balance of
  opinion, not its peak.

## 6. Sample gate

- `effective_sample_size` is the contributor count (authors, or posts when
  `one_vote_per_author=False`).
- Below `min_effective_sample`: `sentiment_z_score = None`,
  `directional_signal = INSUFFICIENT_DATA`, `is_signal_measurable = False`, and the
  suppression is logged at WARNING so it appears in the audit trail.
- `None` means "not measurable". Rendering it as `0.0` fabricates a measurement.

## 7. Standardization and banding

$$Z = \frac{\mu_{\text{filtered}} - \mu_{\text{baseline}}}{\sigma_{\text{baseline}}}$$

- The baseline must describe the same aggregate statistic. See `standards.md` —
  a per-post $\sigma$ understates $Z$ by roughly $\sqrt{n}$.
- $\sigma_{\text{baseline}}$ is validated finite and strictly positive at construction,
  so there is no division guard here and no path that returns a confident $Z = 0$ from
  a broken configuration.
- Bands, decided on the **unrounded** $Z$ and inclusive at each edge:

| Condition | `directional_signal` |
|---|---|
| $Z \ge$ `strong_signal_z` | `STRONG_BULLISH` |
| `signal_z` $\le Z <$ `strong_signal_z` | `BULLISH` |
| $\|Z\| <$ `signal_z` | `NEUTRAL` |
| $-$`strong_signal_z` $< Z \le -$`signal_z` | `BEARISH` |
| $Z \le -$`strong_signal_z` | `STRONG_BEARISH` |

## 8. Audit output

`SocialSentimentSignal` carries every count needed to reconstruct the decision:
`total_posts_analyzed`, `future_posts_excluded_count`, `stale_posts_excluded_count`,
`bot_posts_filtered_count`, `duplicate_posts_filtered_count`, `clean_posts_count`,
`distinct_authors_count`, `effective_sample_size`, both means, the Z-score (or `None`),
the band, `is_signal_measurable`, and `audit_notes`. Persist the record together with
the configuration used; the thresholds are parameters, so the numbers are not
reproducible without them.
