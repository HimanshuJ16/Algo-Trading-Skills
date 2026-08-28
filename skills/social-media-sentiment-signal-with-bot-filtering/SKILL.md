---
name: social-media-sentiment-signal-with-bot-filtering
description: >-
  Use when building a trading signal from social-media post streams (StockTwits, X/Twitter, Reddit) and the feed must be screened for bots, spam and coordinated campaigns first. Applies per-post screens (account age, posting-rate burst, spam patterns), batch-level coordination screens (near-duplicate text collapse, one-vote-per-author), financial-lexicon sentiment scoring with negation handling, and a baseline Z-score that is suppressed entirely when the contributor count is too small to interpret.
domain: Quant Research & Alternative Data
subdomain: Social Media NLP & Bot Filtering
tags: ["social-media-sentiment", "bot-filtering", "spam-detection", "coordination-detection", "nlp-sentiment", "z-score-signal"]
brokers_frameworks: ["StockTwits API", "X (Twitter) API v2", "Reddit API", "Python Standard Library"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a strategy consumes social-media text for a ticker and the feed has to be cleaned before it is aggregated. Stock microblog streams are contaminated at scale: Cresci et al. (2019) analysed 9M stock-related tweets across the five main US markets and found coordinated bot groups piggybacking on the cashtags of liquid names to promote low-value stocks, with as much as 71% of the authors of suspicious financial tweets classified as bots. A sentiment mean taken over that stream measures a campaign, not an opinion.

The engine screens in two places, because the two kinds of abuse are visible in different places:

- **Per post** — account age, posting-rate burst, spam-pattern match. These are decidable from one post and live in `filter_post`.
- **Per batch** — near-duplicate text collapse and one-vote-per-author aggregation. Coordination is a property of the *group*; no per-post rule can see it. These live in `process_social_posts`.

It then scores the survivors with a financial lexicon and standardizes against a baseline — or refuses to, when too few independent contributors remain.

## When NOT to Use

- **As a bot detector.** These are metadata hygiene rules, not detection. Cresci et al. (2017) benchmarked Twitter itself, human annotators and state-of-the-art tools against modern social spambots and found none of them accurate (human annotators scored under 24%). Assume a competent operator passes every rule here.
- **On a feed whose user counters were backfilled.** `user_account_age_days`, `user_follower_count` and `user_posts_last_hour` are as-of-query values. Fetched today for a two-year-old post they describe the account *now*, so every historical account passes the age screen in hindsight and the backtest is measuring a filter that never ran. These fields must be snapshotted at ingestion.
- **As a standalone entry signal.** The output is one alternative-data input. Sentiment Z-scores do not carry position sizing, stops or exposure limits — see `liquidity-adjusted-position-sizing` and `kill-switch-and-drawdown-circuit-breakers`.
- **With a baseline computed on individual post scores.** See Prerequisites — the units must match, or every signal is muted by roughly $\sqrt{n}$.
- **Where the data licence does not permit it.** Platform terms govern what may be stored, redistributed and traded on, independently of what the API returns. Confirm entitlement before building on a feed — `alternative-data-vendor-due-diligence-checklist`, `data-vendor-contractual-usage-restriction-tracking`.
- **To generate the posts as well as read them.** Posting to move a price you trade is market manipulation. This skill reads a public stream; it must never be wired to one that writes.

## Prerequisites

- `SocialPost` records with `post_id`, `asset_id`, `user_id`, `created_at_iso`, `text`, `user_account_age_days`, `user_follower_count`, `user_posts_last_hour`, `is_verified_user`.
- **Timezone-aware ISO-8601 timestamps.** Naive strings are rejected. X's API v2 stamps `created_at` in RFC 3339 UTC with a trailing `Z`, so live feed data already complies; a bare `2026-08-05` does not and is refused rather than assumed to be UTC.
- **A baseline in the right units.** `historical_baseline_mean` and `historical_baseline_std` must describe the distribution of *this same filtered aggregate statistic* — e.g. past daily filtered means for this asset under the same filters and window. They must **not** be the mean and standard deviation of individual post scores: the standard deviation of a mean of $n$ observations is smaller than the per-post standard deviation by roughly $\sqrt{n}$, so a per-post $\sigma$ in the denominator understates every $Z$ by that factor. The engine cannot detect which one it was handed.
- Thresholds you are willing to defend: `min_account_age_days`, `max_posts_per_hour`, `min_effective_sample`, `signal_z`, `strong_signal_z`. The defaults are house heuristics, not standards — see `references/standards.md`.

## Workflow

1. **Validate and align the batch**:
   - Every post is validated before anything is aggregated; a malformed post fails the batch rather than being dropped silently.
   - **Decision point — a post whose `asset_id` differs from the requested asset raises.** Aggregating another ticker's posts under this label would publish a signal for an instrument it was never measured on. Filtering them out quietly would hide a broken ingestion join.

2. **Apply the point-in-time cutoff**:
   - With `as_of`, posts stamped after it are excluded and counted in `future_posts_excluded_count`; with `lookback_window_minutes` set, posts older than the window are counted in `stale_posts_excluded_count`. The retained window is inclusive at both ends.
   - **Decision point — without `as_of` no cutoff is applied at all.** The engine does not guess one. A backtest that omits it is reading the future and the counters will read zero, which is not evidence of correctness.
   - Configuring `lookback_window_minutes` without passing `as_of` raises rather than silently ignoring the window.

3. **Run the per-post screens** (`filter_post`):
   - Unverified accounts below `min_account_age_days`; accounts strictly above `max_posts_per_hour`; the first matching spam pattern.
   - **Decision point — a verification badge buys no exemption by default.** X's published criteria for the blue checkmark are a paid Premium subscription plus a display name, photo, confirmed phone and recent activity; X states it does not mean the account was ID verified. On 5 December 2025 the European Commission fined X €120M under the DSA, finding the checkmark deceptive because "anyone can pay to obtain the 'verified' status without the company meaningfully verifying who is behind the account". Set `trust_verified_accounts=True` only for a platform whose badge you have independently established to mean identity.

4. **Run the batch-level coordination screens** (`process_social_posts`):
   - Near-duplicate collapse: post text is normalised (links, cashtags, mentions, punctuation stripped) and repeats are dropped, keeping the first.
   - One vote per author: each author's posts are averaged to a single contribution before the cross-author mean, so a prolific account cannot carry the batch.
   - **Decision point — the effective sample size is the contributor count, not the post count.** Thirty identical posts from thirty aged, low-rate accounts is one contribution, not thirty.

5. **Score sentiment and gate the signal**:
   - Each distinct lexicon term counts once per post; a term within three tokens after a syntactic negator has its polarity flipped, so "not bullish" scores $-1.0$.
   - **Decision point — below `min_effective_sample` contributors there is no Z-score at all.** `sentiment_z_score` is `None` and `directional_signal` is `INSUFFICIENT_DATA`. `None` means "not measurable" and must be rendered as such, never coerced to 0.
   - **Decision point — `INSUFFICIENT_DATA` is not `NEUTRAL`.** `NEUTRAL` means the balance of opinion was measured and was flat. A consumer may reasonably act on the second and must never act on the first.

6. **Standardize and band**:
   - $Z = (\mu_{\text{filtered}} - \mu_{\text{baseline}}) / \sigma_{\text{baseline}}$, with $\sigma_{\text{baseline}}$ validated finite and strictly positive at construction.
   - **Decision point — the band is decided on the unrounded $Z$.** Rounding first promotes $1.9951$ to $2.0$ and hands back a STRONG signal the data does not support. The reported figure is rounded; the decision is not.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading a signal built from one post**: a single surviving post scoring $+1.0$ against a $\sigma$ of $0.15$ yields $Z \approx 6.3$ — a maximum-conviction signal off a sample of one. The sample gate exists for this; do not disable it by setting `min_effective_sample=1` to make a thin ticker "work".
- **Screening accounts but not campaigns**: thirty established, low-rate accounts posting the same line pass every per-post rule. Per-account heuristics do not see coordination; the duplicate-text and per-author screens do.
- **Treating a paid checkmark as identity**: exempting "verified" accounts from the age screen re-opens the cheapest bypass there is — a €3/month subscription.
- **Backfilled account metadata**: querying account age today for historical posts makes every account look established and produces a backtest of a filter that never fired.
- **A per-post baseline in the denominator**: mixing the units mutes every signal by roughly $\sqrt{n}$, and the output still looks like a valid Z-score.
- **Absorbing a degenerate baseline into $Z=0$**: a zero, negative or NaN $\sigma$ has no Z-score interpretation. The engine raises at construction rather than reporting a confident NEUTRAL built on a broken config.
- **Rounding before banding**: `round(1.9951, 2) == 2.0` silently upgrades BULLISH to STRONG_BULLISH.
- **Scoring `pump` as bullish**: it is the vocabulary of the manipulation this skill screens for. A pump post that clears the filters should not also push the signal the way the campaign intends. `moon`, `rocket` and `breakout` carry the same risk and are kept only because they are also ordinary retail vernacular.
- **Reading `call`/`put` as direction**: they are instrument names. "Sold calls" is a bearish position scored bullish here — strip or reweight them on an options-heavy venue.
- **Assuming the spam regexes hold**: `t(dot)me` and Unicode period look-alikes are normalised, but obfuscation is adaptive and a rewritten payload defeats the list the same day.
- **Aggregating another ticker's posts**: a broken ingestion join publishes a signal for an instrument it was never measured on. The engine raises on an `asset_id` mismatch.

## Verification

- Instantiate `SocialMediaSentimentSignalWithBotFilteringEngine(historical_baseline_mean=0.0, historical_baseline_std=0.1, min_effective_sample=20)`. Feed 12 authors posting distinct bullish text and 8 posting distinct bearish text: mean $= (12-8)/20 = +0.2$, $Z = 0.2/0.1 = 2.0$ exactly, banded `STRONG_BULLISH` at the inclusive boundary.
- Feed 30 established, low-rate accounts posting the *same* line: confirm `bot_posts_filtered_count == 0`, `duplicate_posts_filtered_count == 29`, `effective_sample_size == 1`, and `INSUFFICIENT_DATA` with `sentiment_z_score is None`.
- Feed a single clean bullish post: confirm `INSUFFICIENT_DATA`, not `STRONG_BULLISH`.
- Screens: a 5-day-old account posting 100/hr with a `t.me/` link yields exactly three rejection reasons; a 1-day-old *verified* account is still rejected unless `trust_verified_accounts=True`; age 29 rejects and 30 passes; 41 posts/hr rejects and 40 passes; `t (dot) me/`, `t dot me/` and `t․me/` all match.
- Scoring: "not bullish" $= -1.0$; "moon moon moon moon" $=$ "moon"; "pump it" $= 0.0$.
- Point-in-time: posts stamped after `as_of` land in `future_posts_excluded_count`; `2026-08-05T08:30:00-04:00` is after a `12:00Z` cutoff; a naive `as_of`, a naive post timestamp, and a configured `lookback_window_minutes` with no `as_of` all raise.
- Banding: with `historical_baseline_mean=-1.9951, historical_baseline_std=1.0` and a balanced batch, $Z = 1.9951$ must band `BULLISH`, not `STRONG_BULLISH`.
- Negative checks: `historical_baseline_std` of $0$, negative, NaN or infinity; `min_effective_sample=0`; `signal_z >= strong_signal_z`; an empty or non-string `text`; a negative counter; an `asset_id` mismatch — all raise `ValueError`.
- Run `python -m unittest discover -s skills/social-media-sentiment-signal-with-bot-filtering/scripts` and confirm a 100% pass rate.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `google-trends-and-search-volume-signal-research`
- `alternative-data-vendor-due-diligence-checklist`
- `insider-trading-controls-for-alternative-data-usage`
- `wash-trade-and-spoofing-self-detection`
- `lookahead-bias-elimination`
