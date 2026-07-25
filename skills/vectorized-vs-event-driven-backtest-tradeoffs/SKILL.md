---
name: vectorized-vs-event-driven-backtest-tradeoffs
description: >-
  Use when selecting backtesting engine architecture to evaluate strategy characteristics, run fast vectorized matrix backtests for initial parameter discovery, and cross-validate against high-fidelity event-driven backtest engines to measure execution drag.
domain: algorithmic-trading
subdomain: backtesting-methodology
tags: ["backtesting-methodology", "vectorized-backtest", "event-driven-backtest", "tradeoff-analysis", "execution-drag", "performance-parity"]
brokers_frameworks: ["Dual Engine Backtest Selector", "NumPy", "Pandas"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when designing or validating backtesting infrastructure for quantitative strategies. Vectorized backtests (NumPy/Pandas array operations) process years of daily or minute data in milliseconds, making them ideal for initial alpha hypothesis testing and parameter grid searches. However, vectorized backtests suffer from execution leakage (assuming instant fills at exact close/open prices). Event-driven backtests simulate discrete order queues, partial fills, and slippage at the cost of slower compute runtime. This skill provides dual-engine execution and convergence auditing.

## Prerequisites

- Historical price dataframe (Open, High, Low, Close, Volume).
- Strategy signal generation function.

## Workflow

1. **Audit Strategy Characteristics**:
   - High turnover, intraday limit orders, or complex portfolio rebalancing $\longrightarrow$ **Event-Driven Engine**.
   - Low turnover daily momentum or multi-year parameter sweeps $\longrightarrow$ **Vectorized Engine**.

2. **Execute Vectorized Fast Engine**:
   - Compute signals using matrix operations ($S = \text{sign}(\text{indicator})$) and evaluate vector P&L:
     $$R_{\text{vector}} = S_{t-1} \cdot r_t - c_{\text{comm}}$$

3. **Execute High-Fidelity Event-Driven Engine**:
   - Process bar-by-bar through event loop, simulating order placement, limit order matching, fill delays, and transaction costs.

4. **Audit Engine Convergence & Execution Drag**:
   - Calculate performance divergence delta:
     $$\Delta_{\text{Sharpe}} = \text{Sharpe}_{\text{vector}} - \text{Sharpe}_{\text{event}}$$
   - Quantify execution leakage drag due to realistic fill assumptions.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying Solely on Vectorized Backtests for HFT**: Assuming limit orders fill instantaneously at bar close without event-driven queue simulation.
- **Running Massive Grid Searches on Event-Driven Engines**: Running 1,000,000 parameter combinations on slow event loops, taking days instead of seconds.
- **Ignoring Execution Drag Delta**: Reporting vectorized returns without measuring the 10-30% performance haircut introduced by event-driven fill mechanics.

## Verification

- Run vectorized and event-driven engines on benchmark dataset and verify performance parity metrics.
- Measure compute runtime speedup ratio ($\ge 50\times$ vector speedup).
- Run `python scripts/test_engine_selector.py` and confirm 100% pass rate.

## Related Skills

- `execution-realistic-simulation`
- `lookahead-bias-elimination`
- `transaction-cost-analysis-tca-integration`
---
