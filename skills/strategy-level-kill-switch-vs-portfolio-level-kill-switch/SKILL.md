---
name: strategy-level-kill-switch-vs-portfolio-level-kill-switch
description: >-
  Production-grade hierarchical circuit breaker engine governing Strategy-Level vs Portfolio-Level kill switches, drawdown monitoring, order cancellation, and emergency liquidation actions.
domain: Risk Management & Circuit Breakers
subdomain: Hierarchical Kill Switch Governance
tags: ["strategy-kill-switch", "portfolio-kill-switch", "circuit-breaker", "drawdown-limit", "emergency-liquidation", "risk-governance"]
brokers_frameworks: ["Hierarchical Risk Framework", "Emergency Liquidation Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing multi-layered automated risk controls for quantitative trading infrastructure. A single strategy underperforming due to regime shifts must be isolated before it damages the fund. Strategy-Level Kill Switches halt or liquidate ONLY the affected strategy when its strategy-specific drawdown limit (e.g. 10%) is breached. Conversely, Master Portfolio-Level Kill Switches trigger when total fund drawdown breaches the master limit (e.g. 15%) or when multiple strategies experience cascading failures, halting ALL strategies across the fund.

## Prerequisites

- Individual strategy equity state (`StrategyState`: `strategy_id`, `peak_equity_usd`, `current_equity_usd`, `drawdown_limit_pct`).
- Master portfolio equity state (`PortfolioState`: `total_peak_equity_usd`, `total_current_equity_usd`, `portfolio_drawdown_limit_pct`, `max_tripped_strategies_limit`).

## Workflow

1. **Strategy-Level Drawdown Evaluation**:
   - Calculate strategy drawdown: $\text{DD}_{\text{strat}} = \frac{\text{Peak}_{\text{strat}} - \text{Equity}_{\text{strat}}}{\text{Peak}_{\text{strat}}}$.
   - If $\text{DD}_{\text{strat}} \ge \text{drawdown\_limit\_pct}$, trip strategy kill switch (`HARD_LIQUIDATE` or `SOFT_HALT`).
2. **Master Portfolio-Level Drawdown Evaluation**:
   - Calculate master portfolio drawdown: $\text{DD}_{\text{port}} = \frac{\text{Peak}_{\text{port}} - \text{Equity}_{\text{port}}}{\text{Peak}_{\text{port}}}$.
   - Check cascading failures: count of tripped strategies $\ge \text{max\_tripped\_strategies\_limit}$.
3. **Emergency Action Dispatch**:
   - If portfolio kill switch trips, halt all active strategies immediately and issue emergency liquidation orders.
4. **Execution Output**: Output structured `KillSwitchExecutionReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Strategy vs Portfolio Scopes**: Tripping a master portfolio kill switch and liquidating all fund strategies when only one small sub-strategy experienced a drawdown.
- **Soft Halt Delays in Fast Markets**: Using `SOFT_HALT` (passive order cancellation) during an extreme flash crash when `HARD_LIQUIDATE` (immediate market order liquidation) is required.
- **Lack of Cooldown Enforcement**: Manually resetting a tripped kill switch immediately without enforcing a 24-hour cool-off period, leading to repeated whipsaw losses.

## Verification

- Instantiate `HierarchicalKillSwitchEngine`. Evaluate strategy-level drawdown for `STAT_ARB` ($12\%$ DD vs $10\%$ limit) $\implies$ verify `STAT_ARB` tripped and liquidated while other strategies remain active. Evaluate portfolio-level drawdown ($20\%$ DD vs $15\%$ limit) $\implies$ verify master portfolio kill switch trips and all 3 strategies halted.
- Run `python scripts/test_strategy_level_kill_switch_vs_portfolio_level_kill_switch.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
---
