---
name: central-bank-communication-nlp-analysis
description: >-
  Use when quantifying the hawkish or dovish stance of central bank statements, minutes
  and press conferences for a macro, rates or FX strategy, with sentence-level negation
  handling and policy uncertainty scoring.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: quant-research-alt-data
  tags: nlp, sentiment-analysis, central-bank, fomc, hawkish, dovish, macro, statement-diff
  brokers_frameworks: "Generic NLP; Apel-Grimaldi Lexicon; Hansen-McMahon Framework"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when developing quantitative macro strategies, interest rate models, currency algorithms, or equity index overlays that react to central bank communications. Specifically, use this to:
- Quantify the **Hawkish-Dovish stance** of monetary policy statements, minutes, and press conference transcripts (Federal Reserve FOMC, ECB Governing Council, Bank of England MPC, Bank of Japan, Reserve Bank of India).
- Calculate **Policy Surprise Deltas** ($\Delta \text{Stance} = \text{Stance}_t - \text{Stance}_{t-1}$) and statement diffs (redlines) between consecutive meetings.
- Extract **Monetary Policy Uncertainty** and data-dependence hedging metrics to gauge policy trajectory ambiguity.
- Prevent negation leakage across sentence boundaries and accurately capture multi-word monetary policy collocations (e.g., "quantitative tightening", "downward pressure", "labor market slack").

## When NOT to Use

- **Corporate Earnings or 10-K Analysis**: Corporate filings require broader corporate financial lexicons (Loughran-McDonald), where terms like "liability" or "restructuring" carry operational rather than monetary policy meaning (use `earnings-call-transcript-nlp-signal-research`).
- **Informal Speeches Without Timestamps**: Non-policy public commentary lacking synchronized release timestamps, which risks look-ahead bias and noise.
- **Microsecond Macroeconomic Arbitrage**: For trading immediate rate releases within sub-millisecond windows, hardware-accelerated direct data feeds (e.g. Bloomberg/Refinitiv machine-readable calendar feeds) should be used rather than raw unstructured text parsing.
- **Languages Other Than English**: The base engine is calibrated to English central bank releases; non-English central bank releases (e.g., BOJ Japanese text or PBOC Chinese text) require localized translation pipelines.

## Prerequisites

- Parsed text of central bank communications stripped of website navigation boilerplate.
- Exact embargo release timestamps (e.g., 14:00:00 ET for FOMC Statement, 14:30:00 ET for Chair Press Conference).
- Historical archive of previous policy statements for calculating sequential deltas and statement redlines.

## Workflow

1. **Text Ingestion & Embargo Verification**: Ingest raw communication text and verify release timestamp matches the official exchange embargo time.
2. **Text Cleaning & Sentence Segmentation**: Split text on sentence boundaries (`.`, `!`, `?`, `;`, `\n`) to ensure negation scope is strictly isolated to individual sentences. Mask decimals (*"0.25 percentage point"*, *"2.5 percent"*) and dotted abbreviations (*"U.S."*, *"e.g."*) **before** splitting — their periods are not sentence boundaries.
3. **Multi-Word Phrase Extraction (Collocations)**: Match atomic multi-word policy phrases (e.g., *"quantitative tightening"*, *"labor market slack"*, *"rate hike"*, *"price pressures"*) before evaluating single words to prevent term fragmentation.
4. **Sentence-Bounded Negation Resolution**:
   - For each phrase or word match, inspect preceding tokens within a local lookback window (default 3 tokens) *strictly within the same sentence*.
   - Invert matched polarity when negated (e.g., *"not ease"* $\to$ Hawkish, *"no further firming"* $\to$ Dovish).
5. **Multi-Tier Stance Scoring**:
   - Compute Normalized Stance Score:
     $$\text{Net Score} = \frac{\text{Hawkish} - \text{Dovish}}{\text{Hawkish} + \text{Dovish}} \in [-1.0, 1.0]$$
   - Compute Rhetoric Density per 1,000 words for Hawkish, Dovish, and Uncertainty terms.
6. **Sequential Statement Diffing (FOMC Redline)**:
   - Compare current statement against the previous meeting's statement.
   - Compute **Policy Surprise Delta**:
     $$\Delta \text{Score} = \text{NetScore}_t - \text{NetScore}_{t-1}$$
   - Calculate lexical similarity (Jaccard and Cosine similarity) and identify newly added or dropped hawkish/dovish policy phrases.
7. **Signal Generation**: Transmit the net score, surprise delta, and uncertainty index to macro execution and portfolio rebalancing engines.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sentence Boundary Negation Leakage**: Stripping punctuation and scanning tokens globally. A negation at the end of sentence 1 (*"We will not ease."*) must never negate a word at the beginning of sentence 2 (*"Growth remains strong."*). Sentence segmentation must precede tokenization.
- **Trading Absolute Score Instead of Policy Surprise Delta**: Central bank statements often exhibit persistent baseline tone (e.g., structurally hawkish during inflation shocks). Markets price in known stances; the true alpha signal is the **delta ($\Delta \text{Score}$)** from the previous statement.
- **Timestamp Mismatch & Look-Ahead Bias**: Aligning press conference remarks (which occur 30 minutes after statement release) or post-meeting minutes (released 3 weeks later) to the initial 14:00 statement bar.
- **Generic Sentiment Dictionary Misclassification**: Standard NLP dictionaries (e.g., VADER, general Loughran-McDonald) misclassify monetary terms—scoring *"cut"* or *"slowdown"* as generic negative sentiment rather than monetary accommodation (dovishness).
- **Splitting Sentences on Decimal Points and Abbreviations**: Central bank text is dense with figures (*"0.25 percentage point"*, *"2 percent objective"*) and abbreviations (*"U.S."*). A naive split on `.` fragments the sentence mid-clause, truncates the negation lookback window, and can invert the stance: *"the Committee sees no 0.25 percentage point hike"* scores hawkish once *"no"* is stranded in a preceding fragment.
- **Ignoring Uncertainty / Hedging Language**: Overlooking qualifying terms (*"data-dependent"*, *"highly uncertain"*, *"conditional"*) which temper the conviction of forward guidance.

## Verification

- Run test suite: `python -m unittest discover -s skills/central-bank-communication-nlp-analysis/scripts`.
- Validate repository compliance: `python tools/validate_skills.py` (validates all skills; the script takes no per-skill flag).
- Test sentence-boundary isolation with mock consecutive sentences, including sentences containing decimals and dotted abbreviations.
- Confirm non-text input raises rather than returning a neutral `0.0` stance.
- Verify statement diffing and policy surprise delta calculation against mock FOMC redlines.

## Related Skills

- `global-macro-economic-calendar-integration`
- `earnings-call-transcript-nlp-signal-research`
- `vix-and-volatility-index-derivative-strategies`

