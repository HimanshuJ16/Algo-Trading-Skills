---
name: central-bank-communication-nlp-analysis
description: >-
  Quantitative NLP engine for extracting hawkish vs. dovish sentiment from central bank statements, featuring n-gram negation handling and standardized scoring.
domain: Alpha Generation
subdomain: Alternative Data
tags: ["nlp", "sentiment-analysis", "central-bank", "fomc", "hawkish", "dovish"]
brokers_frameworks: ["Generic NLP"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when processing central bank communications (FOMC statements, ECB press conferences) to generate quantitative trading signals. The engine translates unstructured text into a normalized Hawkish-Dovish index score. It acts as the feature-extraction layer for macro-economic trading models (e.g., trading FX, interest rate swaps, or equity indices based on policy surprises).

## Prerequisites

- Clean, parsed text of central bank communications.
- Basic NLP pre-processing capabilities (tokenization).

## Workflow

1. **Ingestion**: The system receives a raw text document (e.g., the latest FOMC statement).
2. **Pre-processing**: The engine tokenizes the text into sentences and words, converting to lowercase and stripping punctuation.
3. **Lexicon Matching**:
   - The text is scored against specialized financial dictionaries (Hawkish vs. Dovish terms).
   - **Crucial Step**: The engine checks for negation (e.g., "not tighten", "less accommodative") within a defined n-gram window to invert the score.
4. **Scoring**: A net sentiment score is calculated: $(Hawkish - Dovish) / Total\_Words$.
5. **Signal Generation**: The net score is compared to the historical baseline to detect "surprises" (hawkish or dovish shifts).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring Negation**: Scoring "will not raise rates" as Hawkish because it contains the word "raise". Negation windows (looking 2-3 words back) are mandatory.
- **Using Generic Lexicons**: Using standard sentiment dictionaries (like VADER) which score "liability" or "cut" incorrectly in a central bank context. You must use specialized macro-finance lexicons.
- **Absolute vs Relative Scoring**: Trading directly on an absolute hawkish score. Markets price in expectations. The true signal is the *delta* between the current score and the previous statement's score.

## Verification

- Feed a mock FOMC statement into the engine. Include a negated phrase like "we do not plan to increase rates." Verify the engine correctly scores this as Dovish (or neutral) rather than Hawkish.
- Run `python scripts/test_central_bank_nlp_engine.py`.

## Related Skills

- `global-macro-economic-calendar-integration`
- `earnings-call-transcript-nlp-signal-research`
