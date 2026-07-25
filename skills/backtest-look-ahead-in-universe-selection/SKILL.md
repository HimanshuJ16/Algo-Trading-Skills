---
name: backtest-look-ahead-in-universe-selection
description: Auditing for lookahead bias specifically in universe-selection logic.
domain: Backtesting
subdomain: Bias Mitigation
tags:
  - Lookahead
  - Universe Selection
  - Backtesting
brokers_frameworks:
  - General
version: 1.0.0
author: System
license: MIT
---

# When to Use
Use when implementing or auditing universe selection rules to ensure point-in-time accuracy.

# Prerequisites
- Basic understanding of lookahead bias.
- Point-in-time historical universe data.

# Workflow
1. Load historical universe data and selection criteria.
2. Initialize `UniverseLookaheadAuditor`.
3. Pass historical rules and timestamps.
4. Review flagged lookahead violations.

# Common Pitfalls
- Using survivorship-biased datasets.
- Selecting top assets for the whole backtest using final-day market cap.

# Verification
Run the associated test script `test_universe_lookahead_auditor.py`.

# Related Skills
- `backtest-survivorship-bias-auditing`
