---
name: market-data-simulator-for-offline-development
description: >-
  Synthetic market data generation engine using Geometric Brownian Motion (GBM) and bid-ask spread synthesis to produce offline tick and order book feeds for sandbox strategy development.
domain: Data Management Global
subdomain: Market Data Simulation & Offline Testing
tags: ["market-data", "simulator", "offline-development", "geometric-brownian-motion", "synthetic-ticks", "bid-ask-spread", "reproducible-backtest"]
brokers_frameworks: ["NumPy / SciPy Stochastics", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when developing, debugging, or stress-testing trading algorithms and risk controls offline without active exchange connections or paid live subscriptions. This module generates **Synthetic Market Data Ticks** using Geometric Brownian Motion (GBM) with configurable drift ($\mu$), annual volatility ($\sigma$), bid-ask spreads (bps), and deterministic random seeds to produce 100% reproducible test streams.

## Prerequisites

- Simulation configuration (`symbol`, `initial_price`, `drift_mu`, `volatility_sigma`, `time_step_sec`, `num_steps`, `spread_bps`, `random_seed`).

## Workflow

1. **Deterministic Random Generation Initialization**:
   - Seed random number generator with `random_seed` for exact test repeatability.
2. **Geometric Brownian Motion (GBM) Price Step**:
   - For each tick $t$, compute next price:
     $$S_{t+1} = S_t \times \exp\left( (\mu - 0.5\sigma^2)\Delta t + \sigma \sqrt{\Delta t} Z \right)$$
     where $Z \sim \mathcal{N}(0,1)$ is a standard normal random variable.
3. **Bid-Ask Spread & Volume Synthesis**:
   - Calculate half-spread: $\delta = S_t \times \frac{\text{spread\_bps}}{20,000.0}$.
   - $P_{\text{bid}} = S_t - \delta$, $P_{\text{ask}} = S_t + \delta$.
   - Synthesize bid/ask volume depth.
4. **Audit Report Generation**: Output structured `SimulationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Non-Reproducible Random Seeds**: Omitting random seeds during simulation, making regression test failures impossible to reproduce.
- **Negative Price Spikes**: Using simple additive Gaussian noise ($S_{t+1} = S_t + \epsilon$) instead of log-normal GBM, resulting in illegal negative price values.
- **Ignoring Bid-Ask Spread Dynamics**: Generating mid-prices without bid/ask spreads, obscuring transaction costs during strategy evaluation.

## Verification

- Instantiate `MarketDataSimulatorEngine`. Run 1,000-tick simulation ($S_0 = \$100.00$, $\mu=0.05$, $\sigma=0.20$, $\text{spread}=10\text{ bps}$, `random_seed=42`) $\implies$ verify exact 1,000 ticks generated, non-negative prices, valid bid $< \text{ask}$, and 100% seed reproducibility across runs.
- Run `python scripts/test_market_data_simulator.py`.

## Related Skills

- `market-data-replay-harness-for-integration-testing`
- `historical-tick-data-storage-and-compaction`
---
