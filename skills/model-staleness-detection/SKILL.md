---
name: model-staleness-detection
description: Use when a live signal classifier has been running for a while and needs
  ongoing monitoring for performance drift relative to its training-time distribution,
  rather than being trusted indefinitely after initial validation
domain: algorithmic-trading
subdomain: financial-ml
tags:
- financial-ml
brokers_frameworks: []
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any ML-based signal classifier that has been promoted to live or paper trading — model validation at deployment time (see `walk-forward-validation-setup`, `offline-train-online-infer-deployment`) is necessary but not sufficient, because financial markets are non-stationary: the statistical relationships a model learned from historical data can weaken or invert over time as market regime, participant behavior, or microstructure changes. A model that was genuinely validated at deployment can still become quietly wrong months later with no code change and no error — the only way to catch this is active monitoring, not one-time validation.

## Prerequisites

- Logged predictions and their eventual realized outcomes for every live inference the model makes (not just trades that were acted on — all predictions, so degradation can be measured even for signals the strategy chose not to act on due to other filters)
- The training-time performance metrics and, ideally, the training-time feature distributions, to compare against

## Workflow

1. Log every live prediction alongside the features that produced it and, once realized, the actual outcome — this creates a running live performance dataset directly comparable to the offline validation metrics.
2. Track a rolling live accuracy/performance metric (matching whatever metric was used in offline validation — accuracy, precision on the traded direction, Sharpe of a paper P&L attributable to the signal) over a moving window (e.g., trailing 20/60 trading days) rather than a single cumulative number, since a cumulative average masks recent degradation by diluting it with historical good performance.
3. Separately monitor feature distribution drift: compare the live feature distributions (mean, variance, or a distributional distance measure) against the training-time distributions for each feature — a feature whose live distribution has shifted meaningfully from training (e.g., a volatility feature now regularly outside the range seen in training data) is a leading indicator of staleness, often detectable before the rolling accuracy metric itself degrades.
4. Define an explicit staleness threshold in advance (e.g., rolling accuracy drops below X% for Y consecutive trading days, or feature drift exceeds a defined distance threshold) — deciding the threshold after observing a suspicious pattern is a form of hindsight bias that leads to rationalizing continued use of a degrading model.
5. On crossing the staleness threshold, do not silently continue trading on the model — either automatically reduce position sizing/confidence weighting for that signal, route to human review, or halt the signal's contribution to live decisions pending retraining, depending on the strategy's risk tolerance (this ties into `kill-switch-and-drawdown-circuit-breakers` for the mechanism, but the trigger condition originates here).
6. When retraining to address detected staleness, retrain using the walk-forward discipline from `walk-forward-validation-setup` on the newly available data, and run the newly retrained model through the same shadow-period validation as any new model deployment before it replaces the live one.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Validating a model once at deployment and treating it as permanently trustworthy with no ongoing monitoring — the single most common gap in production trading ML systems.
- Monitoring only cumulative/all-time accuracy, which dilutes recent degradation with historical good performance and delays detection.
- Waiting for accuracy metrics to degrade before investigating, rather than also monitoring feature distribution drift, which is often an earlier warning signal.
- Defining "how bad is bad enough to act" only after noticing a concerning pattern, rather than committing to a threshold in advance.
- Continuing to trade a signal at full size/confidence after crossing a defined staleness threshold "just to see if it recovers" — this is exactly the situation risk controls should override strategy discretion, not the reverse.

## Verification

- Confirm every live prediction is logged with its features and eventual outcome, verifiable by spot-checking that the logged dataset can reproduce the rolling accuracy metric independently.
- Backtest the staleness-detection logic itself against a known historical regime change (a period where a similar strategy is documented to have degraded) and confirm the monitoring would have flagged staleness before or reasonably close to when performance actually degraded.
- Confirm the defined staleness threshold and the corresponding automatic response (size reduction, halt, human review trigger) are both documented and testable independently of live trading (e.g., via a replay of logged predictions against the threshold logic).

## Related Skills

- `walk-forward-validation-setup`
- `offline-train-online-infer-deployment`
- `kill-switch-and-drawdown-circuit-breakers`
