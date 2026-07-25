---
name: synthetic-data-generation-for-backtest-augmentation
description: Generating synthetic price paths to augment limited historical data for backtest robustness testing.
domain: Data
subdomain: Augmentation
tags:
  - Synthetic Data
  - GBM
  - Bootstrap
brokers_frameworks:
  - General
version: 1.0.0
author: System
license: MIT
---

# When to Use
Use when you have limited historical data and need to test strategy robustness on alternative possible paths.

# Prerequisites
- Basic statistics and stochastic calculus understanding.

# Workflow
1. Initialize `SyntheticDataGenerator`.
2. Generate paths using Geometric Brownian Motion (GBM) or bootstrapping methods.
3. Use generated paths for out-of-sample backtesting.

# Common Pitfalls
- Over-relying on standard normal assumptions for returns (GBM limitation).
- Destroying autocorrelation structure when bootstrapping single days.

# Verification
Run the associated test script `test_synthetic_data_generator.py`.

# Related Skills
- `backtest-overfitting-prevention`
