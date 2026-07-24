---
name: feature-store-for-live-and-backtest-parity
description: >-
  Use when building ML feature pipelines to implement a single shared feature computation engine guaranteeing bit-for-bit parity between offline batch backtesting and online live streaming inference
domain: algorithmic-trading
subdomain: financial-ml
tags: ["financial-ml", "feature-store", "backtest-live-parity", "train-test-skew", "feature-engineering"]
brokers_frameworks: ["Feast", "Hopsworks", "Pandas", "NumPy", "Custom Feature Engines"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever building machine learning or quantitative alpha models where features are trained offline on historical datasets and executed online on live market data streams. Implementing separate code paths for offline backtesting (e.g. Pandas vectorization) and online live trading (e.g. stateful streaming loops) introduces Train-Test Skew. Differences in NaN handling, rolling window boundaries, or floating-point precision cause model predictions to diverge in production. Implementing a single unified feature calculation core with automated parity validation ($\epsilon \le 10^{-6}$) is mandatory.

## Prerequisites

- Shared feature calculation class instantiated by both backtest and live runtimes.
- Rolling window buffer for streaming online calculation.
- Automated unit test suite verifying feature matrix equivalence.

## Workflow

1. **Implement Unified Feature Core (`ParityFeatureStoreEngine`)**:
   - Define all financial ML features (e.g. RSI, Bollinger Band %B, Volatility Z-scores) in a single shared module consumed by both backtest and live environments.

2. **Execute Batch Backtest Mode (`compute_batch_features`)**:
   - Compute full feature matrix $X_{\text{batch}}$ over historical OHLCV bar series using vectorized operations.

3. **Execute Streaming Live Mode (`compute_online_feature`)**:
   - Ingest incoming streaming ticks/bars into a rolling ring buffer ($N=\text{lookback}$).
   - Compute online feature vector $x_{\text{online}, t}$ using identical feature logic.

4. **Verify Parity via Automated Assertion (`validate_parity`)**:
   - Run online streaming calculation over the same historical bar series used for batch computation.
   - Assert $|X_{\text{batch}}[t] - x_{\text{online}, t}| \le 10^{-6}$ for all timestamps $t$.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Dual Code Paths**: Maintaining separate feature calculation scripts for backtesting and live trading, causing subtle divergence over time.
- **Lookahead Leakage in Rolling Windows**: Computing batch features using future bars (`shift(-1)`), causing backtests to appear artificially profitable.
- **Inconsistent Warm-up Data**: Failing to feed online streaming buffers with sufficient historical warm-up bars, producing NaN or distorted features during initial live startup.

## Verification

- Compute batch feature matrix $X_{\text{batch}}$ over 100 historical bars.
- Stream the same 100 bars through `compute_online_feature()` and verify `validate_parity()` confirms equivalence with tolerance $\epsilon \le 10^{-6}$.
- Inject deliberate logic mismatch and verify `validate_parity()` raises `FeatureParityMismatchError`.
- Run unit test suite `python scripts/test_feature_store.py` and confirm 100% pass rate.

## Related Skills

- `ensemble-signal-combination-without-overfitting`
- `regime-detection-for-strategy-switching`
- `walk-forward-optimization-window-management`
---
