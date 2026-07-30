---
name: google-trends-and-search-volume-signal-research
description: >-
  Alternative data quantitative engine for processing Google Search Volume Index (SVI), calculating rolling Z-score attention spikes, and generating trading signals without lookahead bias.
domain: Quant Research & Alt Data
subdomain: Web Search Volume & Retail Sentiment Signal Research
tags: ["google-trends", "svi", "alt-data", "z-score", "retail-sentiment", "attention-spikes", "point-in-time"]
brokers_frameworks: ["Google Trends API (pytrends)", "SciPy Stats", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in alternative data research, retail sentiment signal construction, and market anomaly detection. Sudden spikes in Google Search Volume Index (SVI $0-100$) for keywords ("debt", "recession", company brand names like "Tesla", "Nvidia") act as leading indicators of retail attention, panic selling, or demand surges (Preis, Moat, & Stanley 2013). This module computes rolling SVI Z-scores ($Z_t = \frac{\text{SVI}_t - \mu}{\sigma}$), enforces Point-in-Time availability lags ($\Delta t_{\text{lag}} = 24\text{ hours}$), and classifies attention surges vs panic spikes.

## Prerequisites

- Search Volume Index (SVI) time series data (`timestamp`, `keyword`, `svi_score`).
- Ticker price momentum data to distinguish bullish demand from panic selling.
- Lookback window $N = 30$ periods and threshold $Z_{\text{threshold}} = 2.0$.

## Workflow

1. **Point-in-Time Availability Alignment**:
   - Shift SVI data by publication lag $\Delta t_{\text{lag}} = 24\text{ hours}$ to prevent lookahead bias.
2. **Rolling SVI Z-Score Calculation**:
   - Compute rolling mean $\mu_N$ and rolling standard deviation $\sigma_N$ over lookback window $N$.
   - $Z_t = \frac{\text{SVI}_t - \mu_N}{\max(1.0, \sigma_N)}$.
3. **Signal Classification**:
   - If $Z_t \ge 2.0$ AND Price Momentum $> 0 \implies$ `BULLISH_ATTENTION_SURGE`.
   - If $Z_t \ge 2.0$ AND Price Momentum $< 0 \implies$ `BEARISH_PANIC_SPIKE`.
   - Else $\implies$ `NEUTRAL_ATTENTION`.
4. **Audit Report Generation**: Output structured `GoogleTrendsSignalReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Lookahead Bias via Unshifted SVI Data**: Evaluating Google Trends data on date $T$ assuming it was available at midnight $T$, ignoring the 24-48 hour publishing lag.
- **Ignoring Keyword Ambiguity**: Tracking generic terms (e.g. "Apple" fruit vs AAPL company) without normalizing search categories.
- **Un-Normalized Historical SVI Rescaling**: Failing to account for Google Trends' 0-100 scaling reset across changing date ranges.

## Verification

- Instantiate `GoogleTrendsSignalEngine`. Input 30-day SVI series for "Nvidia". Baseline mean $\mu = 40.0$, $\sigma = 5.0$. Day 31 SVI spikes to $90.0$ ($Z = +10.0$). Test Positive Momentum $\implies$ verifies engine outputs `BULLISH_ATTENTION_SURGE` with 24h lag alignment. Test Negative Momentum $\implies$ verifies engine outputs `BEARISH_PANIC_SPIKE`.
- Run `python scripts/test_google_trends_and_search_volume_signal_research.py`.

## Related Skills

- `web-scraped-sentiment-data-pipeline`
- `social-media-sentiment-signal-with-bot-filtering`
---
