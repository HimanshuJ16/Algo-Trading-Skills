# Web-Scraped Sentiment Pipeline — Pre-Flight Checklist

Sign off before a sentiment feature from this pipeline informs a live order.

## Lexicon provenance

- [ ] **Membership verified, not assumed.** Every scored term is confirmed present in the
      Loughran-McDonald `Positive` or `Negative` column of the Master Dictionary. No term was
      added by hand because it "sounds bullish".
- [ ] **Categories not conflated.** `risk` (Uncertainty), `lawsuit` (Litigious) and `drop`
      (Interesting) are not counted as negative sentiment.
- [ ] **Full dictionary loaded for production.** The bundled 80/140 subset is a smoke-test
      convenience; the real lists carry 354 positive and 2,355 negative entries.
- [ ] **Dictionary release pinned and recorded** against every backtest. SRAF updates annually.
- [ ] **Domain transfer considered.** `FILING_SPECIFIC_TERMS` excluded for news and social text;
      `exclude_filing_specific_terms=False` when the corpus is filings.
- [ ] **Licence confirmed.** LM materials are published free for academic research; commercial
      use requires contacting the authors. Entitlement checked for this deployment.

## Collection and cleaning

- [ ] **Collection is permitted.** Site terms of service, `robots.txt` and platform API terms
      reviewed for every source. EDGAR crawlers respect 10 req/s and send an identifying
      `User-Agent`.
- [ ] **`<script>`/`<style>` bodies removed before tag stripping**, including **unclosed**
      elements from truncated scrapes — verified no JavaScript identifiers reach the token
      stream.
- [ ] **HTML entities unescaped to a fixed point before punctuation removal** — verified both
      `&amp;` and the double-escaped `&amp;amp;` leave no `amp` token.
- [ ] **URLs removed**, including bare `www.` forms.
- [ ] **Underscores split**, so `record_loss` is two tokens.

## Scoring

- [ ] **Negation handled.** `not profitable` scores negative, not positive.
- [ ] **Both normalisations recorded.** `raw_sentiment_score` (polarity) and `lm_tone` are stored
      per document, alongside `matched_word_count`.
- [ ] **Saturation understood.** Everyone reading the output knows a polarity of $\pm1.0$ can rest
      on a single matched word, and `min_matched_words` is set above 1.

## Deduplication

- [ ] **Syndicated reposts collapsed.** Verbatim wire copy across PR Newswire, Business Wire and
      aggregators contributes once.
- [ ] **Lineage preserved.** Duplicates are marked with `duplicate_of`, not deleted, so the audit
      trail shows what was suppressed and why.
- [ ] **Earliest copy survives.** Deduplication keeps the earliest timestamp, not the first item
      in the list, so a later repost cannot carry a document across the cutoff.
- [ ] **Rewritten copy acknowledged.** Text-identity dedup does not catch a re-worded lede; if
      that matters, MinHash or shingled Jaccard is added upstream.

## Point-in-time integrity

- [ ] **All timestamps timezone-aware**, and they are *publication* times, not scrape times.
- [ ] **`session_timezone` matches the traded session** and is fixed for the study.
- [ ] **`signal_date` used as a cutoff.** A backtest run confirms `future_items_excluded` is
      non-zero on a corpus that extends past the signal date — a counter reading zero everywhere
      is not evidence the cutoff works.
- [ ] **Aggregation window is a deliberate choice**, not the default left unread.

## Baseline and standardisation

- [ ] **Baseline units match the aggregate.** Past values of `current_sentiment_mean` for this
      ticker under this exact configuration, including the same `score_metric` — not
      per-document scores.
- [ ] **Baseline rebuilt** after any lexicon or gate change.
- [ ] **Baseline length at or above `min_baseline_observations`.**
- [ ] **No substitute $\sigma$ anywhere in the stack.** A degenerate baseline yields
      `INSUFFICIENT_DATA`, never a Z-score against $\sigma = 1.0$.
- [ ] **`min_baseline_std` respected, not tuned down to force a signal.** A near-constant
      baseline is a dormant ticker, not an opportunity.
- [ ] **Banding on the unrounded $Z$** — confirmed $Z = 1.4951$ reports 1.5 and bands `NEUTRAL`.

## Consumption

- [ ] **`INSUFFICIENT_DATA` is not treated as `NEUTRAL`** anywhere downstream, and
      `sentiment_zscore is None` is never coerced to 0.
- [ ] **`confidence_score` is not read as a probability** and is not multiplied into position
      size.
- [ ] **Signal is one input, not an entry trigger.** Sizing, stops and exposure limits come from
      `liquidity-adjusted-position-sizing` and `kill-switch-and-drawdown-circuit-breakers`.
- [ ] **No bot or coordination screening is claimed.** This engine has no author metadata; social
      feeds go through `social-media-sentiment-signal-with-bot-filtering` first.
- [ ] **MNPI exposure reviewed** for every source —
      `insider-trading-controls-for-alternative-data-usage`.

## Verification

- [ ] `python -m unittest discover -s skills/web-scraped-sentiment-data-pipeline/scripts` passes
      100%.
- [ ] `python tools/validate_skills.py` reports this skill passing.
