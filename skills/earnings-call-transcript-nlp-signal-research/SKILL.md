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
- negation-handling
brokers_frameworks:
- Loughran-McDonald Master Dictionary
- Python Standard Library
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative equity research, event-driven funds, and post-earnings announcement drift (PEAD) strategies. Prepared management remarks are scripted and reviewed by IR/legal; the analyst Q&A is spontaneous, and the tone gap between the two is the informative part. Price, Doran, Peterson & Bliss (2012, *Journal of Banking & Finance* 36, 992–1011) find that conference call tone predicts abnormal returns and that the Q&A portion carries **incremental** explanatory power for PEAD beyond the presentation section — that is the empirical basis for scoring the two sections separately rather than as one blob.

The module scores each section with the Loughran-McDonald (LM) financial lexicon, applies the LM negation rule, and reports **Q&A Tone Divergence** ($\Delta_{\text{tone}}$) and an **Uncertainty Ratio**.

## When NOT to Use

- **As a standalone trading signal.** This is a research feature generator. It has no return model, no position sizing, and no calibration; combine it with an earnings-surprise and liquidity model before it sizes anything.
- **On non-English transcripts, machine translations, or auto-generated ASR captions.** LM is an English list built on US SEC filings; translation and transcription noise destroy the word counts.
- **On any text that is not a segmented transcript** — press releases, 8-Ks, prepared-only excerpts. Divergence is undefined without both sections.
- **On very short or partially transcribed sections.** The engine returns `INSUFFICIENT_DATA` rather than a tradable score; do not override that by lowering the sample floor to make a signal appear.
- **For commercial use without an LM licence.** The LM dictionary is free for academic research only (see Prerequisites).

## Prerequisites

- Transcript already segmented into `prepared_remarks_text` and `qa_session_text`. This skill does not parse vendor transcript layouts.
- The **Loughran-McDonald Master Dictionary** (Positive, Negative, Uncertainty lists), loaded and passed to the constructor. The bundled `DEFAULT_LM_*` sets are a small verified subset (~40 words per category vs ~2,355 Negative / ~354 Positive / ~297 Uncertainty in the full lists) — they exist so the engine runs out of the box, and are not adequate for research.
  - Source: <https://sraf.nd.edu/loughranmcdonald-master-dictionary/>
  - Licensing: "The dictionary/sentiment lists are free for use in academic research." Commercial licences must be obtained from the authors.
- A timezone-aware transcript **publication** timestamp (not the call date) if the output feeds a backtest.
- Python 3.8+ standard library only. No NLP framework, model download, or third-party dependency is required.

## Workflow

1. **Transcript Section Parsing**: Supply `PREPARED_REMARKS` and `QA_SESSION` text separately. If your vendor only delivers a single blob, segment it before calling — a combined score masks exactly the tone drop this skill measures.
2. **Tokenization**: Lower-case; contractions and hyphenated terms stay single tokens ("don't", "year-over-year") so the ratio denominator is not inflated by fragments.
3. **Loughran-McDonald Matching with Negation**: Count $N_{\text{pos}}, N_{\text{neg}}, N_{\text{uncert}}$. A positive word preceded **within three tokens** by a negator (`no, not, none, neither, never, nobody`) is reclassified as negative, per Loughran & McDonald (2011). The rule is applied to positive words only — double negation of negative words is rare in disclosure. LM categories overlap by design ("volatility" is both Negative and Uncertainty), so uncertainty is counted independently of polarity.
4. **Sentiment & Divergence Computation**:
   - $\text{Net Sentiment} = \dfrac{N_{\text{pos}} - N_{\text{neg}}}{N_{\text{pos}} + N_{\text{neg}}}$, defined as $0.0$ when the denominator is zero (no polarity evidence — *not* a neutral tone).
   - $\Delta_{\text{tone}} = \text{Net Sentiment}_{\text{QA}} - \text{Net Sentiment}_{\text{Prepared}}$.
   - $\text{Uncertainty Ratio} = \dfrac{N_{\text{uncert}}}{N_{\text{total words}}} \times 100\%$.
   - `overall_net_sentiment` pools the raw counts of both sections; it is not the mean of the two section scores.
5. **Sample-Sufficiency Gate**: If either section falls below `min_section_words` (default 50) or `min_polarity_terms` (default 5), emit `INSUFFICIENT_DATA` and stop. A two-word "section" can otherwise produce a full-strength $\Delta_{\text{tone}} = -2.0$ off two lexicon hits.
6. **Signal Classification** (precedence order, first match wins):
   - `INSUFFICIENT_DATA` — sample gate failed.
   - `BEARISH_QA_DIVERGENCE` — $\Delta_{\text{tone}} <$ `bearish_divergence_threshold` (default $-0.15$).
   - `BULLISH_EARNINGS_TONE` — pooled net sentiment $>$ `bullish_sentiment_threshold` (default $0.40$) **and** pooled uncertainty ratio $<$ `max_uncertainty_ratio_pct` (default $1.5\%$).
   - `NEUTRAL` — otherwise.
   Bearish divergence deliberately outranks bullish tone: glowing prepared remarks followed by a collapsing Q&A is the case this signal exists to catch.
7. **Threshold Calibration**: The four defaults above are plausible starting points, **not** empirically validated constants. Re-estimate them per universe (sector, market cap, transcript vendor) on out-of-sample data before trading.
8. **Audit Report Generation**: Output `EarningsTranscriptAuditReport`, carrying the timezone-aware publication timestamp for point-in-time alignment.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using General-Purpose Dictionaries**: General English lexicons (e.g. VADER, Harvard IV) score "liability", "cost", "capital" and "tax" as negative. Loughran & McDonald (2011) built the financial lists precisely because, in 10-Ks filed 1994–2008, "almost three-fourths of the words identified as negative by the widely used Harvard Dictionary are words typically not considered negative in financial contexts."
- **Hand-Rolling a "Loughran-McDonald-like" Word List**: Plausible-sounding earnings vocabulary is often not in LM at all — `growth`, `record`, `momentum`, `robust`, `expansion`, `profit` and `exceed` are **not** LM Positive; `headwind` is **not** LM Negative. Worse, `risk`/`risks` are LM **Uncertainty**, not Negative: putting them in the negative list is the same general-purpose error, just self-inflicted. Verify every word against the Master Dictionary flags.
- **Skipping the Negation Rule**: Without it, "margins did not improve" and "we are not confident" score as positive statements. LM prescribe a three-word look-back window for negated positives.
- **Ignoring Q&A vs Prepared Remarks Separation**: Scoring the whole transcript as one document averages away the divergence and discards the section shown to carry the incremental PEAD information.
- **Trading Tiny Samples**: A truncated or partially-transcribed section yields extreme $\pm 1.0$ scores from one or two matches. Sentiment on a handful of polarity terms is noise, not tone.
- **Treating Zero Sentiment as Neutral**: A section with no lexicon hits scores $0.0$, which is identical to a perfectly balanced section. Check `has_sufficient_sample` before interpreting a zero.
- **Transplanting Thresholds Across Universes**: The $-0.15$ / $0.40$ / $1.5\%$ defaults are illustrative. Tone distributions differ by sector, market cap, and transcript vendor; an uncalibrated threshold produces a signal that fires on style, not information.
- **Look-Ahead Bias in Transcript Availability**: Align on the exact transcript **publication** timestamp, not the call date, the earnings date, or a naive local datetime. Vendors typically publish a full transcript minutes-to-hours after the call ends; scoring it as of the call start trades on text that did not exist yet. The engine rejects timezone-naive timestamps for this reason.

## Verification

- Instantiate `EarningsTranscriptNlpEngine` with the full LM lists. Submit a call with optimistic prepared remarks and a defensive Q&A: with prepared remarks at $+1.00$ (6 LM positives, 0 negatives) and Q&A at $-0.78$ (1 positive, 8 negatives), $\Delta_{\text{tone}} = -1.78$ and the engine emits `BEARISH_QA_DIVERGENCE`.
- Confirm `analyze_text_section("QA_SESSION", "The quarter was not strong and demand did not improve.")` returns `positive_count == 0`, `negative_count == 2`, `negated_positive_count == 2`.
- Confirm a one-word section pair returns `INSUFFICIENT_DATA`, not a bearish trade.
- Confirm a timezone-naive `transcript_published_at` raises `ValueError`.
- Run `python -m unittest discover -s skills/earnings-call-transcript-nlp-signal-research/scripts`.

## Related Skills

- `central-bank-communication-nlp-analysis`
- `social-media-sentiment-signal-with-bot-filtering`
- `lookahead-bias-elimination`
- `alternative-data-vendor-due-diligence-checklist`
