---
name: earnings-call-transcript-nlp-signal-research
description: Quantitative NLP research engine for analyzing earnings call transcripts,
  computing Loughran-McDonald financial sentiment, Q&A tone divergence, and executive
  uncertainty ratios for equity trading signals.
domain: Quantitative Research & Alternative Data
subdomain: Financial NLP & Sentiment Signals
tags:
- nlp-signals
- earnings-transcripts
- loughran-mcdonald
- sentiment-analysis
- qa-tone-divergence
- alternative-data
- finbert
brokers_frameworks:
- Loughran-McDonald Dictionary
- spaCy/NLTK
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative equity research, event-driven hedge funds, and post-earnings announcement drift (PEAD) strategies. Corporate earnings call transcripts provide high-alpha sentiment signals. Prepared management remarks are heavily polished by PR teams, whereas spontaneous Q&A responses reveal executive hesitation. This module parses transcripts using the Loughran-McDonald financial lexicon, measuring **Q&A Tone Divergence** ($\Delta_{\text{tone}}$) and **Uncertainty Ratios** to generate trading signals.

## Prerequisites

- Raw earnings call transcript text or structured section dictionary (`prepared_remarks_text`, `qa_session_text`).
- Loughran-McDonald financial sentiment dictionary (Positive, Negative, Uncertainty, Weak Modal lists).

## Workflow

1. **Transcript Section Parsing**:
   - Separate transcript into `PREPARED_REMARKS` and `QA_SESSION`.
2. **Loughran-McDonald Keyword Extraction**:
   - Tokenize text and match against financial lexicon ($N_{\text{pos}}, N_{\text{neg}}, N_{\text{uncert}}$).
3. **Sentiment & Tone Divergence Computation**:
   - $\text{Net Sentiment} = \frac{N_{\text{pos}} - N_{\text{neg}}}{N_{\text{pos}} + N_{\text{neg}} + \epsilon}$.
   - $\Delta_{\text{tone}} = \text{Net Sentiment}_{\text{QA}} - \text{Net Sentiment}_{\text{Prepared}}$.
   - $\text{Uncertainty Ratio} = \frac{N_{\text{uncert}}}{N_{\text{total\_words}}}$.
4. **Signal Classification**:
   - If $\Delta_{\text{tone}} < -0.15 \implies$ Flag `BEARISH_QA_DIVERGENCE`.
   - If Net Sentiment $> 0.40$ and Uncertainty $< 1.5\% \implies$ Flag `BULLISH_EARNINGS_TONE`.
5. **Audit Report Generation**: Output structured `EarningsTranscriptAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using General-Purpose Dictionaries**: Applying general English sentiment lexicons (e.g. VADER), misinterpreting neutral terms like "cost", "liability", or "risk" as negative.
- **Ignoring Q&A vs Prepared Remarks Separation**: Combining the entire transcript into a single blob, masking sharp tone drops when executives are pressed by analysts.
- **Look-Ahead Bias in Transcript Availability**: Backtesting transcript sentiment using UTC dates instead of the exact timestamp when the transcript was published.

## Verification

- Instantiate `EarningsTranscriptNlpEngine`. Submit transcript with optimistic prepared remarks ($S_{\text{prep}} = +0.60$) and defensive Q&A answers ($S_{\text{qa}} = -0.10$, $\Delta_{\text{tone}} = -0.70$). Verify engine detects `BEARISH_QA_DIVERGENCE`, computes Loughran-McDonald keyword frequencies, and outputs a bearish signal.
- Run `python scripts/test_earnings_call_transcript_nlp_signal_research.py`.

## Related Skills

- `central-bank-communication-nlp-analysis`
- `social-media-sentiment-signal-with-bot-filtering`
---
