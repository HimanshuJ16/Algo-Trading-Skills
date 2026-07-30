---
name: lookahead-bias-elimination
description: Use when writing or auditing backtest code to ensure no signal, feature,
  or decision uses information that would not have been available at the actual decision
  timestamp in live trading
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
brokers_frameworks: []
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this on every backtest implementation before trusting its results, and especially when an agent generates backtest code from a natural-language strategy description — lookahead bias is the single most common reason a backtest shows profitable results that evaporate or reverse in live trading, and it is easy to introduce without noticing because the code often "looks correct" (uses the right column names, right indicators) while silently peeking at future data.

## Prerequisites

- A precise definition of the decision timestamp for the strategy (the exact moment a trading decision is made, not the bar/candle it's associated with)
- Full historical OHLCV or tick data with accurate timestamps for every field being used

## Workflow

1. For every feature/indicator computed at time T, explicitly verify it uses only data with a timestamp ≤ T. The most common violation is using a bar's own close/high/low to generate a signal evaluated "at" that same bar — in live trading, the bar isn't closed yet at decision time, so a same-bar-close-dependent signal is unexecutable live and must instead reference the *previous* completed bar.
2. Audit indicator warm-up periods explicitly: rolling averages, ATR, RSI, or any windowed indicator has a warm-up period before its values are meaningful; ensure the backtest either starts trading only after warm-up completes, or explicitly excludes/flags early bars, rather than including a partially-formed indicator value as if it were a real signal.
3. Check any data join operations (e.g., merging fundamental data, corporate actions, or option-chain data onto a price series) for point-in-time correctness — data that is available to a database *today* but was revised or restated after the fact (e.g., some fundamentals data providers backfill/restate values) will silently leak future information if joined naively by date rather than by "as-of" availability timestamp.
4. Verify that any parameter selection, threshold tuning, or feature selection performed "on the full dataset" wasn't inadvertently performed using data that spans into the test period — this is a subtler, dataset-level form of lookahead bias distinct from per-bar signal computation, and it requires strict train/test separation (see `walk-forward-validation-setup`).
5. Check order/fill logic timing specifically: if a signal is generated using bar T's close, the earliest a live system could actually place an order is at bar T+1's open (or later, accounting for actual execution latency) — a backtest that fills the signal at bar T's own close is assuming instantaneous, same-bar execution that cannot exist live.
6. For options/derivatives strategies specifically, verify that strike selection and Greeks used in the backtest were computable from information available at decision time — e.g., using an end-of-day IV surface to select a strike for a signal generated intraday is a common, easy-to-miss leak.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Using `df['close']` to compute a signal and also using `df['close']` as the fill price for the same row/bar — this is same-bar lookahead disguised as normal-looking code.
- Computing indicators over the entire dataframe in one vectorized pass (common in pandas-based backtests) without verifying that rolling/window functions are backward-looking only — some vectorized operations (e.g., centered rolling windows, certain resample/interpolate calls) look forward by default unless explicitly configured not to.
- Joining point-in-time-sensitive data (fundamentals, restated economic indicators, some options data) by calendar date rather than by as-of/publish timestamp.
- Tuning strategy parameters against the full historical dataset (including what will later be called the "out-of-sample" period) and then reporting out-of-sample performance as if it were untouched.
- Assuming zero latency between signal generation and order fill, rather than modeling a realistic delay (see `execution-realistic-simulation`).

## Verification

- Run the backtest with a "leak-detector" pass: for a sample of signals, manually trace which data points fed the decision and confirm every timestamp involved is ≤ the decision timestamp.
- Deliberately introduce a known one-bar-forward leak into a test copy of the strategy and confirm it measurably (and often dramatically) inflates backtest performance — if introducing an obvious cheat doesn't change results, the audit process itself may not be sensitive enough to catch real leaks.
- Compare in-sample vs strict walk-forward out-of-sample performance (see `walk-forward-validation-setup`); a large, unexplained gap is a strong indicator of residual lookahead bias even after the above checks.

## Related Skills

- `walk-forward-validation-setup`
- `execution-realistic-simulation`
- `feature-engineering-without-leakage`
