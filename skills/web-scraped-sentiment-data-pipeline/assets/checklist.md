# Institutional Web-Scraped Sentiment Operations Checklist

## Web Scraping & Data Cleaning
- [ ] **HTML & URL Stripping**: Verify regex patterns remove HTML tags (`<p>`, `<div>`) and HTTP/HTTPS URLs.
- [ ] **Ticker Normalization**: Strip cashtags (`$AAPL` -> `AAPL`) and upper-case all ticker symbols.
- [ ] **Syndicated Article Deduplication**: Deduplicate identical press releases across news wire sources.

## Loughran-McDonald Lexicon Scoring
- [ ] **Financial Dictionary Matching**: Score tokens against Loughran-McDonald positive and negative financial wordlists.
- [ ] **Raw Score Normalization**: Confirm raw sentiment scores are normalized to the $[-1.0, +1.0]$ interval.

## Baseline Anomaly & Signal Generation
- [ ] **30-Day Rolling Baseline Calculation**: Compute 30-day expanding/rolling mean ($\mu_{30\text{d}}$) and standard deviation ($\sigma_{30\text{d}}$).
- [ ] **Z-Score Anomaly Triggering**: Verify $Z_{\text{sentiment}} \ge +1.5$ triggers `LONG` and $Z_{\text{sentiment}} \le -1.5$ triggers `SHORT`.
- [ ] **Bot & Spam Filtering**: Filter out social media accounts with post frequencies $> 50\ \text{posts/hour}$.