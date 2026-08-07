---
name: web-scraped-sentiment-data-pipeline
description: "Institutional alternative data skill for scraping, cleaning, tokenizing, and scoring financial text using the Loughran-McDonald Lexicon, aggregating volume-weighted sentiment metrics, calculating 30-day baseline Z-score anomalies, and generating directional trading signals."
domain: Quantitative Research & Alternative Data
subdomain: Financial NLP & Web-Scraped Sentiment Analysis
tags:
- alt-data
- sentiment-analysis
- nlp
- loughran-mcdonald
- web-scraping
- z-score
- feature-engineering
brokers_frameworks:
- news-feed
- reddit-wsb
- twitter-x
- stocktwits
- sec-edgar
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when processing web-scraped text feeds (financial news, SEC 8-K/10-K filings, Reddit r/wallstreetbets, Twitter/X, StockTwits) to extract quantitative sentiment features, compute 30-day baseline sentiment Z-scores, and generate systematic trading signals.

This skill provides institutional mechanisms to:
- Strip HTML tags, remove URLs, normalize cashtags ($\$AAPL$), and clean noisy web text.
- Score financial text using **Loughran-McDonald Financial Lexicon** tokenization ($S = \frac{N_{\text{pos}} - N_{\text{neg}}}{N_{\text{pos}} + N_{\text{neg}}}$).
- Aggregate sentiment scores across multiple sources (`NEWS`, `REDDIT`, `TWITTER`, `STOCKTWITS`).
- Compute **30-Day Historical Sentiment Baseline Z-Scores** ($Z = \frac{\bar{S}_t - \mu_{30\text{d}}}{\sigma_{30\text{d}}}$).
- Output directional trading signals (`LONG`, `SHORT`, `NEUTRAL`) and confidence scores.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `re`, `typing`).
- Raw scraped text items (item ID, source, timestamp, ticker, text content).

## Workflow

1. **Ingest Raw Scraped Feed**: Construct `RawScrapedItem` instances containing item ID, source, timestamp, ticker symbol, and uncleaned text.
2. **Clean & Normalize Text**: Call `clean_text(text)` to strip HTML tags, remove URLs, remove special characters, and lowercase tokens.
3. **Score Financial Sentiment**: Call `score_text(clean_text)` to perform Loughran-McDonald lexicon word matching and derive raw sentiment scores $[-1.0, +1.0]$.
4. **Process Scraped Feed**: Invoke `process_scraped_feed(items)` to convert raw items into `ScoredSentimentItem` records.
5. **Generate Directional Trade Signal**: Call `generate_ticker_signals(scored_items, ticker, date, baseline_scores)` to evaluate 30-day Z-score anomalies and generate `LONG`/`SHORT` signals.

## Common Pitfalls

- **Generic NLP Dictionary Failure**: Using general-purpose NLP lexicons (like VADER or TextBlob) on financial text misclassifies words like *"liability"*, *"cost"*, or *"shares"*. Always use **Loughran-McDonald Financial Dictionaries**.
- **Syndicated News Double-Counting**: Reposting the same press release across multiple news wires (PR Newswire, BusinessWire) inflates sentiment volume. Articles must be deduplicated using MinHash or headline matching.
- **Lookahead Bias in Baseline Norms**: Calculating historical sentiment baseline means ($\mu$) and standard deviations ($\sigma$) using future scraped posts creates severe backtest overfitting.
- **Spam & Bot Sentiment Manipulation**: Unfiltered social media feeds contain bot spam promoting penny stocks. Filter out accounts with high post frequency or zero account age.

## Verification

Run the unit test suite to validate text cleaning, Loughran-McDonald sentiment tokenization, feed processing, 30-day baseline Z-score signal generation, and neutral fallbacks:

```bash
python -m unittest discover -s skills/web-scraped-sentiment-data-pipeline/scripts
```

## Related Skills

- `weather-data-signal-research-for-commodity-strategies`
- `transfer-learning-across-correlated-instruments`
- `tick-size-pilot-program-impact-assessment`
- `wash-trade-and-spoofing-self-detection`
