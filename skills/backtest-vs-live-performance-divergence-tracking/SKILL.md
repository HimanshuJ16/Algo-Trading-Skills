---
name: backtest-vs-live-performance-divergence-tracking
description: Use when monitoring promoted strategies to systematically measure, decompose,
  and alert on divergence between backtested (hypothetical) performance and realized
  live trading performance across Sharpe ratio, drawdown, fill rate, and slippage
  dimensions.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags:
- backtesting-methodology
- backtest-live-divergence
- performance-tracking
- strategy-monitoring
- slippage-drift
- sharpe-decay
brokers_frameworks:
- Divergence Tracking Engine
- Python Statistics
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill after promoting a strategy from backtesting to live trading. Every strategy experiences some divergence between its backtested equity curve and realized live performance. Small divergence ($<20\%$ Sharpe decay) is expected due to execution friction. Large unexplained divergence ($>30\%$ Sharpe decay, or max drawdown $2\times$ backtest worst case) signals model overfitting, regime shift, or execution infrastructure failure. This skill provides a structured framework for tracking, decomposing, and alerting on backtest-vs-live divergence.

## Prerequisites

- Backtested performance metrics: Sharpe ratio, max drawdown, win rate, avg slippage assumption.
- Live performance metrics over equivalent observation window.

## Workflow

1. **Capture Paired Metric Snapshots**:
   - Record backtest baseline metrics $M_{\text{bt}}$ and live realized metrics $M_{\text{live}}$ at equivalent time horizons.

2. **Compute Divergence Scores**:
   - Sharpe Divergence: $\Delta_{\text{sharpe}} = \frac{S_{\text{bt}} - S_{\text{live}}}{S_{\text{bt}}} \times 100\%$
   - Drawdown Divergence: $\Delta_{\text{dd}} = \frac{DD_{\text{live}} - DD_{\text{bt}}}{DD_{\text{bt}}} \times 100\%$
   - Fill Rate Divergence: $\Delta_{\text{fill}} = \text{FillRate}_{\text{bt}} - \text{FillRate}_{\text{live}}$

3. **Classify Divergence Severity**:
   - `ACCEPTABLE`: All divergence metrics within tolerance thresholds.
   - `WARNING`: One or more metrics exceed soft threshold (e.g., Sharpe decay $>20\%$).
   - `CRITICAL`: Sharpe decay $>50\%$ or live drawdown $>2\times$ backtest drawdown — triggers strategy suspension review.

4. **Generate Divergence Report & Alerts**:
   - Emit structured divergence audit report with per-metric breakdown.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Comparing Mismatched Time Windows**: Comparing 3-year backtest Sharpe against 2-week live Sharpe, inflating noise-driven divergence.
- **Ignoring Survivorship Bias in Backtest**: Backtest includes delisted winners; live portfolio never held them.
- **Attributing All Divergence to Execution**: Assuming all Sharpe decay is slippage when it may be regime-driven alpha decay.

## Verification

- Submit paired metrics with 25% Sharpe decay, verify `WARNING` classification.
- Submit paired metrics with 60% Sharpe decay, verify `CRITICAL` classification.
- Run `python scripts/test_divergence_tracker.py` and confirm 100% pass rate.

## Related Skills

- `transaction-cost-analysis-tca-integration`
- `paper-to-live-promotion-checklist`
- `multi-year-regime-coverage-requirement`
---
