---
name: backtest-reporting-standardized-tearsheet
description: Producing a standardized performance tearsheet.
domain: Reporting
subdomain: Performance Metrics
tags:
  - Tearsheet
  - Performance
  - Backtesting
brokers_frameworks:
  - General
version: 1.0.0
author: System
license: MIT
---

# When to Use
Use when you need a consistent reporting format (Sharpe, drawdown, etc.) for comparing multiple strategies.

# Prerequisites
- Array or series of historical portfolio returns.

# Workflow
1. Collect daily portfolio returns.
2. Initialize `StandardizedTearsheetGenerator`.
3. Generate the tearsheet metrics.
4. Export or display the structured report.

# Common Pitfalls
- Calculating annualized metrics without proper `periods_per_year`.
- Overlooking compounding in cumulative return calculations.

# Verification
Run the associated test script `test_tearsheet_generator.py`.

# Related Skills
- `benchmark-selection-for-strategy-evaluation`
