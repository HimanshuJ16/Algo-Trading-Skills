---
name: feature-engineering-without-leakage
description: Use when constructing features for an ML-based trading signal classifier,
  to guarantee every feature is computable using only information available strictly
  before the prediction target's outcome
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
brokers_frameworks: []
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this while designing or auditing the feature set for any ML model that predicts a future price move, direction, or trading signal. Target leakage in financial ML is especially easy to introduce because many natural-seeming features (same-bar returns, same-bar volatility, labels derived from a window that overlaps the feature window) contain information that is only knowable after the outcome you're trying to predict — a model trained on leaked features shows excellent backtest/validation accuracy and then performs at or below random in live inference, because the leaked information simply doesn't exist yet at real prediction time.

## Prerequisites

- A precise, written definition of the prediction target (exact timestamp the target is realized, e.g., "direction of close-to-close return from bar T to bar T+1") separate from the feature computation timestamp
- Full audit of every feature's computation window relative to that target definition

## Workflow

1. Write down the exact target definition first, in terms of timestamps, before writing any feature code: "predict sign of return from close(T) to close(T+1)" is different from "predict sign of return from close(T) to close(T+5)" and the feature set's cutoff must be defined relative to whichever one is chosen.
2. For every candidate feature, explicitly state the timestamp at which it becomes knowable, and verify that timestamp is strictly before the feature-computation cutoff (which itself must be strictly before the target's realization timestamp).
3. Watch specifically for these common leakage patterns in trading ML:
   - Using same-bar high/low/close to predict same-bar direction (the label and a "feature" share information from the same, not-yet-closed bar).
   - Computing a rolling statistic (volatility, average volume) over a window that includes bars used to define the label.
   - Using a target-derived quantity as a feature by accident — e.g., accidentally including future returns in a lagged-feature construction due to an off-by-one indexing error in a pandas `shift()` call (shifting the wrong direction).
   - Using end-of-day-only data (adjusted close, some fundamentals feeds) as if it were available intraday, when the actual publish/adjustment timestamp is later than assumed.
4. Explicitly test the indexing/shift direction on a small, manually-verifiable example (a handful of rows where you compute the expected feature value by hand) — off-by-one shift errors are common in pandas/numpy code and produce a model that performs suspiciously well because it's effectively seeing the answer.
5. When engineering features that mix multiple data sources at different frequencies (e.g., daily fundamentals joined to intraday price bars), always join by as-of/publish timestamp, never by calendar date alone.
6. After the feature set is finalized, run a leakage sanity check: train a trivial model (or check feature-target correlation) using an intentionally-leaked version of one feature (e.g., literally include the future target shifted by one bar) and confirm this produces a dramatic, obviously-too-good accuracy jump — this calibrates what "suspiciously good" looks like for the rest of the audit.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Off-by-one errors in `shift()`/`lag()` calls (shifting a feature backward when it should be forward, or vice versa) silently turning a lagged feature into a leaked one.
- Using adjusted close prices (which incorporate future corporate action adjustments applied retroactively to historical rows) as a same-day feature, without accounting for the fact that the adjustment itself encodes information not known at that historical date.
- Computing rolling/window features with pandas defaults that aren't strictly backward-looking (e.g., centered windows) without explicit verification.
- Treating unusually high validation accuracy as good news rather than as the first thing to be suspicious of — in financial ML, "too good" almost always means leakage, not genuine edge.

## Verification

- Manually trace 3-5 sample rows end-to-end: for each, list every raw data point feeding each feature along with its timestamp, and confirm all are strictly before the feature-computation cutoff.
- Confirm the "intentional leak" sanity test (deliberately leaking one feature) produces a clearly anomalous accuracy jump, proving the audit process is sensitive enough to catch real leakage.
- Confirm walk-forward out-of-sample performance (see `walk-forward-validation-setup`) is reasonably close to in-sample performance — a large, unexplained gap after this feature audit suggests residual leakage not yet caught.

## Related Skills

- `walk-forward-validation-setup`
- `lookahead-bias-elimination`
- `offline-train-online-infer-deployment`
