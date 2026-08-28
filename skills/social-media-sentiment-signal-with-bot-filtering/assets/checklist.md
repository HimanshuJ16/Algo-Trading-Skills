# Pre-Flight Checklist

Sign off before a social-sentiment signal is allowed to influence capital.

## Data contract

- [ ] Are `user_account_age_days`, `user_follower_count` and `user_posts_last_hour`
      snapshotted **at ingestion**, not backfilled from a later query?
- [ ] Are all post timestamps timezone-aware ISO-8601 (naive values rejected, not
      assumed UTC)?
- [ ] Does every post in a batch carry the same `asset_id` as the batch, with a
      mismatch treated as a broken ingestion join rather than filtered away?
- [ ] Is the platform's licence position established for storing and trading on this
      feed?

## Point-in-time

- [ ] Is an explicit `as_of` passed on every call, in research and in production?
- [ ] Is `lookback_window_minutes` set to a defensible staleness bound?
- [ ] Have `future_posts_excluded_count` and `stale_posts_excluded_count` been
      inspected on a real batch — and understood to read zero when no cutoff was
      applied at all?

## Screens

- [ ] Are `min_account_age_days` and `max_posts_per_hour` calibrated against this
      venue's observed distributions, rather than left at the house defaults of 30
      and 40?
- [ ] Is `trust_verified_accounts` left `False` unless the platform's badge has been
      independently established to mean identity verification?
- [ ] Is duplicate-text collapse enabled, and has its effect on a real batch been
      measured (over-collapse mutes signal; disabling it re-opens campaigns)?
- [ ] Is `one_vote_per_author` enabled, so a prolific account cannot carry a batch?

## Statistics

- [ ] Do `historical_baseline_mean` and `historical_baseline_std` describe the
      **filtered aggregate statistic** (past daily filtered means), not individual
      post scores?
- [ ] Is `historical_baseline_std` finite and strictly positive, and is a degenerate
      baseline treated as a configuration error rather than a NEUTRAL reading?
- [ ] Is `min_effective_sample` calibrated to this asset's normal contributor volume,
      and demonstrably not lowered to make a thin ticker produce signals?
- [ ] Are `signal_z` and `strong_signal_z` re-estimated out-of-sample rather than left
      at the illustrative 0.75 / 2.0 defaults?

## Consumption

- [ ] Does the downstream consumer distinguish `INSUFFICIENT_DATA` from `NEUTRAL`, and
      refuse to act on the former?
- [ ] Is `sentiment_z_score is None` rendered as "not measurable" and never coerced
      to 0?
- [ ] Is the full `SocialSentimentSignal` record persisted **with the configuration
      used**, so the numbers are reproducible?
- [ ] Is this signal one input among several, with sizing, stops and exposure limits
      owned elsewhere?

## Scope

- [ ] Is it documented — internally and externally — that these screens are metadata
      hygiene, not bot detection, and that a competent operator passes all of them?
- [ ] Is the read path physically separate from any system able to post, with separate
      credentials?
