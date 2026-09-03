---
name: web-scraped-sentiment-data-pipeline
description: >-
  Use when turning scraped financial text (news wires, SEC filings, Reddit, X/Twitter, StockTwits) into a point-in-time sentiment feature for a ticker. Cleans HTML/URL/entity noise, scores against verified Loughran-McDonald financial word lists with negation handling, collapses syndicated wire duplicates, aggregates a daily mean strictly at or before the signal date, and standardises it against a caller-supplied baseline — refusing to emit a Z-score when the document count, the per-document lexicon evidence, or the baseline is too thin to interpret.
domain: Quantitative Research & Alternative Data
subdomain: Financial NLP & Web-Scraped Sentiment Analysis
tags:
- alt-data
- sentiment-analysis
- nlp
- loughran-mcdonald
- web-scraping
- z-score
- point-in-time
- feature-engineering
brokers_frameworks:
- news-wire-feeds
- sec-edgar
- reddit-api
- x-twitter-api
- stocktwits-api
- python-standard-library
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a strategy consumes scraped financial text for a ticker and needs a
numeric, point-in-time sentiment feature out the other end. It covers the four places this
pipeline usually breaks: the lexicon is not what it claims to be, syndicated copy is counted
once per wire, the aggregation window silently includes documents published after the decision
point, and a Z-score is reported against a standard deviation that was never measurable.

The engine:

- Cleans scraped text — `<script>`/`<style>` bodies, tags, HTML entities, URLs (including bare
  `www.`), cashtags and punctuation — before tokenizing.
- Scores against **verified Loughran-McDonald word lists** with a negation window, so
  *"not profitable"* scores negative.
- Reports **both** normalisations per document: the polarity $(N_p - N_n)/(N_p + N_n)$ and the
  LM tone $(N_p - N_n)/N_{\text{tokens}}$, alongside `matched_word_count` so a consumer can see
  how much evidence a $\pm1.0$ rests on.
- Marks near-duplicate documents (same ticker, same cleaned text) rather than dropping them, so
  the batch stays auditable one record per input.
- Aggregates only documents inside `[signal_date - window + 1, signal_date]` resolved in
  `session_timezone`, and **counts** everything it excluded.
- Standardises against a caller-supplied baseline, or returns `INSUFFICIENT_DATA` with
  `sentiment_zscore = None`.

## When NOT to Use

- **As a substitute for the full dictionary.** The bundled lists are a verified subset (80
  positive, 140 negative) of a dictionary with **354 positive and 2,355 negative** entries. They
  exist so the module runs standalone. Load the real one with
  `load_lm_lexicon_from_master_dictionary()` before trading it.
- **In a commercial deployment without checking the licence.** Notre Dame's Software Repository
  for Accounting and Finance publishes the LM materials "free for use in academic research" and
  directs commercial users to contact the authors. Confirm entitlement first — see
  `alternative-data-vendor-due-diligence-checklist`.
- **On filing text, with the default exclusions.** `FILING_SPECIFIC_TERMS` removes LM entries
  whose polarity is an artefact of 10-K prose. On 10-K/8-K text that is the wrong call: pass
  `exclude_filing_specific_terms=False`.
- **As a bot or coordination filter.** `RawScrapedItem` carries no author or account metadata,
  so the engine cannot screen accounts, rate-limit bursts, or apply one-vote-per-author. Duplicate
  *text* collapse is all it does. For social feeds use
  `social-media-sentiment-signal-with-bot-filtering`.
- **As a standalone entry signal.** The output is one alternative-data input. It carries no
  position sizing, stops or exposure limits — see `liquidity-adjusted-position-sizing` and
  `kill-switch-and-drawdown-circuit-breakers`.
- **Where the scraping itself is not permitted.** Site terms of service, `robots.txt`, and
  per-venue market-data entitlements govern what may be collected, stored and traded on,
  independently of what a page returns — `data-vendor-contractual-usage-restriction-tracking`.

## Prerequisites

- Python 3.9+, standard library only (`csv`, `datetime`, `html`, `logging`, `math`, `re`).
- `RawScrapedItem` records with `item_id`, `source`, `timestamp`, `ticker`, `text_content`.
- **Timezone-aware timestamps.** Naive datetimes are rejected at construction. A naive stamp
  cannot be placed on a trading day without guessing an offset, and guessing is how future
  documents leak into a backtest.
- **A baseline in the right units.** `historical_baseline_scores` must be past values of *this
  same daily aggregate* for this ticker under the same filters and window — **not** individual
  document scores. The standard deviation of a mean of $n$ observations is smaller than the
  per-document standard deviation by roughly $\sqrt{n}$, so a per-document $\sigma$ in the
  denominator understates every $Z$ by that factor. The engine cannot detect which one it was
  handed.
- **A baseline matching `score_metric`.** `polarity` (default) and `lm_tone` are different units;
  a baseline built for one is invalid for the other.
- Thresholds you are willing to defend: `zscore_threshold`, `min_matched_words`, `min_items`,
  `min_baseline_observations`, `aggregation_window_days`, `score_metric`. The defaults are house
  heuristics, not standards — see `references/standards.md`.

## Workflow

1. **Ingest and validate**: construct `RawScrapedItem` instances. A malformed item — naive
   timestamp, implausible ticker, non-string text — raises rather than being dropped, so a
   broken ingestion join fails loudly instead of shrinking the sample in silence.

2. **Clean**: `clean_text(text)`.
   - **Decision point — remove `<script>`/`<style>` *bodies* before stripping tags.** Stripping
     only the tags leaves the JavaScript in the corpus, where identifiers and string literals
     tokenize into the lexicon.
   - **Decision point — unescape entities to a fixed point, before removing punctuation.**
     Otherwise `&amp;` loses its `&` and `;` and leaves the token `amp` in every scraped
     article — and a double-escaped page (`&amp;amp;`, common when a CMS escapes content that
     was already escaped) defeats a single unescape pass.
   - **Decision point — an unclosed `<script>` still hides its body.** A truncated scrape ends
     mid-element; stripping only the opening tag spills the JavaScript into the tokens.

3. **Score**: `score_text(clean)` returns `(positive, negative, polarity)`.
   - **Decision point — a lexicon term within `NEGATION_WINDOW` (3) tokens after a negator has
     its polarity flipped.** Without it, *"not profitable"* and *"highly profitable"* score
     identically at $+1.0$.
   - **Decision point — polarity is not the LM tone measure.** Loughran and McDonald normalise
     word-list counts by the total words in the document. Polarity divides by matched words
     only, which makes a one-word match indistinguishable from a fifty-word match. On short news
     text most documents saturate at exactly $\pm 1.0$, so the daily mean over polarity is close
     to binary. Set `score_metric="lm_tone"` to aggregate the LM normalisation instead — and
     rebuild the baseline, because the two are different units.

4. **Process the feed**: `process_scraped_feed(items)`.
   - **Decision point — duplicates are marked, not deleted.** A press release reposted across
     PR Newswire, Business Wire and a dozen aggregators is one opinion. `is_duplicate` and
     `duplicate_of` preserve the lineage; the signal step excludes them from the aggregate.
   - Duplicate identity is `(ticker, cleaned text)`, so the same headline about two different
     tickers is two documents, and empty documents are never collapsed into one group.
   - **Decision point — the surviving copy is the earliest by timestamp, not the first in the
     list.** The survivor's timestamp decides which day the document lands on, so keeping a
     later repost can push a document across the point-in-time cutoff. Deduplication is
     therefore independent of the order the caller supplied.

5. **Generate the signal**: `generate_ticker_signals(scored, ticker, signal_date, baseline)`.
   - **Decision point — `signal_date` is a cutoff, not a label.** Documents stamped at or after
     midnight following `signal_date` in `session_timezone` are excluded and counted in
     `future_items_excluded`. A backtest that passes the full scored history is otherwise
     averaging in documents published after the decision it is simulating.
   - **Decision point — below `min_items` eligible documents, or below
     `min_baseline_observations`, there is no Z-score at all.** `sentiment_zscore` is `None`
     and the direction is `INSUFFICIENT_DATA`.
   - **Decision point — `INSUFFICIENT_DATA` is not `NEUTRAL`.** `NEUTRAL` means the balance of
     opinion was measured and was flat. A consumer may reasonably act on the second and must
     never act on the first.
   - **Decision point — a degenerate baseline raises the flag, not a substitute $\sigma$.** A
     constant baseline has $\sigma = 0$ and no Z-score interpretation. Substituting $\sigma = 1.0$
     fabricates the denominator and reports a confident number built on nothing. The test is
     $\sigma \ge$ `min_baseline_std`, not $\sigma > 0$: a $\sigma$ of $10^{-160}$ passes a bare
     positivity check and then yields $|Z| \approx 10^{160}$ — reported as maximum conviction
     next to a `baseline_std` that displays as `0.0`.
   - **Decision point — the band is decided on the unrounded $Z$.** Rounding first promotes
     $1.4951$ to $1.50$ and hands back a `LONG` the data does not support.

> Full procedure: see `references/workflows.md`.
> Standards, formulas and threshold provenance: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling a hand-written word list "Loughran-McDonald"**: LM is a specific, published,
  verifiable artefact. `growth`, `revenue`, `dividend`, `buy`, `record`, `momentum`, `bullish`,
  `rally`, `surge`, `slump`, `bearish` and `sell` are **not** in either LM sentiment category.
  Attributing them to LM misrepresents the dictionary and the paper. Check membership against
  the Master Dictionary rather than trusting the label.
- **Reading `risk` as negative sentiment**: LM classifies it under **Uncertainty**, not Negative.
  `lawsuit` is **Litigious** and `drop` is **Interesting**. Folding the other categories into the
  negative count changes what the score measures and breaks comparability with any published
  result.
- **Transferring 10-K vocabulary to headlines unexamined**: LM was induced from filings, so
  `despite` is LM-Positive and `closed`, `disclose`, `claims`, `critical`, `force`, `against`,
  `volatility` and `restructuring` are LM-Negative. "Shares closed higher" is not a negative
  headline. See `FILING_SPECIFIC_TERMS`.
- **A one-word match reported as maximum conviction**: the polarity $(N_p - N_n)/(N_p + N_n)$
  saturates at exactly $\pm 1.0$ on a single matched word. `min_matched_words` exists for this;
  do not set it to 1 to make a thin ticker "work".
- **Switching `score_metric` without rebuilding the baseline**: `polarity` and `lm_tone` have
  different scales. Standardising a tone mean against a polarity baseline produces a Z-score that
  is arithmetically valid and semantically meaningless.
- **Trading a mean over one document**: one document at $+1.0$ against a $\sigma$ of $0.125$ is
  $Z = +8$ — a maximum-conviction signal off a sample of one.
- **Syndicated double-counting**: the same press release across twelve wires inflates both the
  mean and the apparent sample size. Text-identity dedup catches verbatim reposts; a rewritten
  lede needs MinHash or shingled Jaccard, which this engine does not implement.
- **`signal_date` used as a label rather than a cutoff**: the single most expensive bug in this
  pipeline. A signal whose exclusion counters all read zero is not evidence of correctness — it
  may just mean no cutoff was ever applied.
- **Timezone-naive timestamps**: a document stamped `2026-03-11T00:00:00Z` is *inside* the
  10 March New York session and *outside* the 10 March UTC session. Which day a document lands
  on decides whether it is data or look-ahead.
- **Absorbing a broken baseline into $Z = 0$**: setting `baseline_mean = current_mean` when no
  baseline was supplied produces $Z = 0$ and a confident `NEUTRAL` on zero information.
- **Reading `confidence_score` as a probability**: it is $\min(1, |Z| / (\text{threshold} \times
  \text{multiple}))$ — a bounded presentation scale, not a calibrated likelihood of the trade
  working.
- **Assuming the engine screens bots**: it does not. It has no account metadata to screen with.

## Verification

- Construct `WebScrapedSentimentPipelineEngine()`. Confirm the bundled lists carry 80 positive
  and 140 negative terms, are lowercase, and are disjoint; confirm `growth`, `revenue`,
  `dividend`, `buy`, `record`, `bullish`, `rally`, `surge`, `slump`, `bearish`, `sell`, `risk`,
  `lawsuit` and `drop` are in neither.
- Cleaning: `<script>var loss = 'failure failure';</script>` contributes no tokens;
  `profit &amp; loss` yields `profit loss` with no `amp`; `www.spam.example/x` is removed;
  `record_loss` splits into two tokens; a non-string raises.
- Scoring: `strong improvement profitable` $= (3, 0, +1.0)$; `strong improvement profitable loss`
  $= (3, 1, +0.5)$; `not profitable` $= (0, 1, -1.0)$; a term four tokens after the negator is
  **not** flipped.
- Intensity: `strong improvement` and the same two words buried in 98 filler tokens both score
  polarity $+1.0$; only `lm_tone` separates them ($1.0$ versus $0.02$). With
  `score_metric="lm_tone"` the same three long documents that give a polarity mean of $1.0$ give
  a tone mean below $0.2$ and a correspondingly smaller $Z$.
- Dedup: three wire copies of one release mark two as `is_duplicate` with `duplicate_of` set to
  the first; the same text under two tickers marks neither.
- Point-in-time: with `session_timezone=UTC`, three documents stamped `2026-03-11T00:00Z` against
  `signal_date=2026-03-10` land in `future_items_excluded`; the same documents under a UTC-4
  session are retained. `2026-03-10T23:59:59Z` is included.
- Look-ahead regression: three positive documents on the signal date plus twenty negative
  documents stamped the next day must yield `LONG` with `future_items_excluded == 20`, not the
  `SHORT` an uncut aggregate produces.
- Gates: one eligible document, four documents matching a single lexicon word, five wire copies
  of one release, a 5-observation baseline, and a constant baseline `[0.25] * 21` must each
  yield `INSUFFICIENT_DATA` with `sentiment_zscore is None` and `baseline_std is None`.
- Banding: with a baseline of mean $-0.1875$ and $\sigma = 0.125$ and a document mean of $0.0$,
  $Z = 1.5$ exactly must band `LONG` (inclusive). With mean $-0.1868875$, $Z = 1.4951$ must
  report `sentiment_zscore == 1.5` and still band `NEUTRAL`.
- Negative checks: a NaN, infinite, string or boolean baseline value; a `datetime` passed as
  `signal_date`; a blank `target_ticker`; a naive timestamp; `zscore_threshold` of 0, negative or
  NaN; `min_baseline_observations=1`; an unrecognised `score_metric`; an empty or overlapping
  word list — all raise `SentimentPipelineError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/web-scraped-sentiment-data-pipeline/scripts` and
  confirm a 100% pass rate.

## Related Skills

- `social-media-sentiment-signal-with-bot-filtering`
- `earnings-call-transcript-nlp-signal-research`
- `google-trends-and-search-volume-signal-research`
- `alternative-data-vendor-due-diligence-checklist`
- `data-vendor-contractual-usage-restriction-tracking`
- `lookahead-bias-elimination`
- `feature-engineering-without-leakage`
- `wash-trade-and-spoofing-self-detection`
