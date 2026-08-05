---
name: social-media-sentiment-signal-with-bot-filtering
description: >-
  Production-grade quantitative social media sentiment engine featuring multi-layered bot & spam filtering (account age checks, posting burst detection, Telegram/Twitter spam regex pattern matching), financial NLP sentiment scoring, and baseline Z-score signal generation.
domain: Quant Research & Alternative Data
subdomain: Social Media NLP & Bot Filtering
tags: ["social-media-sentiment", "bot-filtering", "spam-detection", "nlp-sentiment", "finbert-vader", "z-score-signal"]
brokers_frameworks: ["StockTwits / X Twitter API", "VADER / FinBERT NLP", "Python Dataclasses", "pandas"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when harvesting social media text streams (StockTwits, Twitter/X, Reddit) to construct alpha sentiment signals for quantitative trading. Raw social media feeds are flooded with automated spam bots, Telegram pump-and-dump channel links, and high-frequency posting scripts designed to artificially manipulate retail sentiment. This engine filters out bot posts using account age rules, posting frequency thresholds, and regex spam matching before scoring financial sentiment and computing standardized Z-score signals.

## Prerequisites

- Social post payload (`SocialPost`: `post_id`, `asset_id`, `user_id`, `created_at_iso`, `text`, `user_account_age_days`, `user_follower_count`, `user_posts_last_hour`, `is_verified_user`).
- Historical baseline parameters (`historical_baseline_mean`, `historical_baseline_std`).

## Workflow

1. **Multi-Layered Bot & Spam Screening**:
   - Filter young unverified accounts ($< 30$ days old).
   - Filter high-frequency spammers ($> 40$ posts/hour).
   - Filter spam regex patterns (`t.me/`, `bit.ly/`, `guaranteed profit`, `cashapp`).
2. **Financial NLP Sentiment Scoring**:
   - Evaluate clean text using financial lexicon (`bullish`, `breakout`, `call`, `moon` vs `bearish`, `dump`, `crash`, `put`).
3. **Sentiment Z-Score Signal Generation**:
   - Calculate filtered sentiment mean ($\mu_{\text{clean}}$).
   - Compute Z-score signal: $Z = \frac{\mu_{\text{clean}} - \mu_{\text{baseline}}}{\sigma_{\text{baseline}}}$.
4. **Execution Output**: Output structured `SocialSentimentSignal`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trading Unfiltered Raw Sentiment**: Constructing trading signals on raw social media text, getting whipsawed by coordinated bot pump-and-dump campaigns.
- **Ignoring Account Age & Frequency Indicators**: Treating a 1-day-old account posting 100 times an hour with equal weight to an established 5-year-old account.
- **Static Sentiment Baselines**: Failing to normalize sentiment scores against historical rolling 30-day baseline means and standard deviations.

## Verification

- Instantiate `SocialMediaSentimentSignalWithBotFilteringEngine`. Filter valid post ("NVDA breakout bullish rocket!") $\implies$ verify `is_bot_or_spam=False` and positive sentiment. Filter spam post ("Join my channel t.me/free_crypto for guaranteed profit" from 5-day-old account) $\implies$ verify `is_bot_or_spam=True` with 3 rejection reasons. Process post batch for TSLA $\implies$ verify bot post excluded and positive Z-score signal generated.
- Run `python scripts/test_social_media_sentiment_signal_with_bot_filtering.py`.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `alternative-data-vendor-due-diligence-checklist`
---
