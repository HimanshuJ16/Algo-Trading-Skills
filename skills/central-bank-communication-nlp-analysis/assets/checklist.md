# Pre-Flight Checklist: Central Bank Communication NLP Engine

Use this checklist before deploying central bank NLP sentiment and policy surprise signals to production quantitative strategies.

## 1. Text Ingestion & Sentence Segmentation
- [ ] Has website boilerplate, HTML tags, and legal disclaimers been completely stripped from the raw statement?
- [ ] Is text segmented into individual sentences before tokenization?
- [ ] Are sentence boundary delimiters (`.`, `!`, `?`, `;`, `\n`) properly preserved to prevent cross-sentence negation leakage?
- [ ] Are decimals (`0.25`, `2.5`) and dotted abbreviations (`U.S.`, `e.g.`) masked before splitting so their periods do not create spurious sentence boundaries?

## 2. Lexicon & Multi-Word Collocation Matching
- [ ] Are multi-word collocations (*"quantitative tightening"*, *"labor market slack"*, *"rate hike"*, *"downward pressure"*) matched before single-word tokens?
- [ ] Does the matcher track consumed token indices to avoid double counting terms?
- [ ] Is the lexicon calibrated specifically to monetary policy (Apel-Grimaldi / Hansen-McMahon framework) rather than generic sentiment (VADER/Harvard-IV)?

## 3. Negation Handling & Scope Limitation
- [ ] Is the negation lookback window configured (recommended 3 tokens)?
- [ ] Is negation evaluation strictly confined to the current sentence?
- [ ] Does negation invert sentiment correctly (negated hawkish $\to$ dovish, negated dovish $\to$ hawkish)?

## 4. Stance Index & Rhetoric Density Scoring
- [ ] Is the normalized net score bounded in $[-1.0, 1.0]$ with zero division protected?
- [ ] Are document densities (mentions per 1,000 words) calculated for hawkish, dovish, and uncertainty terms?
- [ ] Is an empty or neutral document safely returned with score `0.0`?
- [ ] Does non-text input raise an error rather than being scored as a neutral `0.0` stance?

## 5. Sequential Statement Diffing & Policy Surprise ($\Delta \text{Score}$)
- [ ] Is the current statement compared against the exact preceding meeting's statement?
- [ ] Is the policy surprise delta calculated as $\Delta \text{Score} = \text{NetScore}_t - \text{NetScore}_{t-1}$?
- [ ] Are Jaccard and Cosine similarity measures computed to assess structural wording changes?
- [ ] Are newly added and dropped hawkish/dovish phrases extracted for auditability?

## 6. Timestamp Synchronization & Look-Ahead Prevention
- [ ] Does the timestamp reflect the exact embargo release time (e.g. 14:00:00 US Eastern for FOMC Statement)?
- [ ] Are post-meeting minutes (released 3 weeks later) timestamped on their actual release date, not backfilled to the statement date?
- [ ] Are press conference transcripts processed separately from initial statements?