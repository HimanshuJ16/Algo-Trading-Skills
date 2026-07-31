---
name: portfolio-level-stop-loss-independent-of-strategy-stops
description: >-
  Independent portfolio-level stop-loss engine monitoring aggregated daily and peak-to-trough drawdowns, triggering emergency global position flattening independent of sub-strategy stops.
domain: Portfolio Multi Strategy
subdomain: Risk Governance & Global Circuit Breakers
tags: ["portfolio-stop-loss", "drawdown-kill-switch", "risk-management", "circuit-breaker", "multi-strategy-risk", "nav-monitoring"]
brokers_frameworks: ["Global Portfolio Risk Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying multi-strategy or multi-asset trading systems where sub-strategies maintain their own internal stop-loss rules. During systemic market shocks, correlation spikes, or liquidity crunches, individual strategy stops may fail to trigger simultaneously or suffer severe slippage. An independent portfolio-level stop-loss continuously calculates Net Asset Value (NAV) across all strategies and enforces daily ($\text{DD}_{\text{daily}} \ge \text{MaxDailyDD}$) and peak-to-trough ($\text{DD}_{\text{peak}} \ge \text{MaxPeakDD}$) limits, flattening all active positions and locking out new orders when limits are breached.

## Prerequisites

- Sub-strategy position states (`strategy_id`, `symbol`, `quantity`, `current_price`, `unrealized_pnl`).
- Portfolio equity metrics (`start_of_day_equity`, `peak_equity`, `current_cash`).
- Risk policy config (`max_daily_drawdown_pct`: default 0.05, `max_peak_drawdown_pct`: default 0.10).

## Workflow

1. **Aggregated Portfolio NAV Calculation**:
   - Compute total NAV: $\text{NAV} = \text{CurrentCash} + \sum (\text{Qty}_i \cdot \text{Price}_i)$.
2. **Drawdown Evaluation**:
   - Compute Daily Drawdown:
     $$\text{DD}_{\text{daily}} = \frac{E_{\text{SOD}} - \text{NAV}}{E_{\text{SOD}}}$$
   - Compute Peak-to-Trough Drawdown:
     $$\text{DD}_{\text{peak}} = \frac{E_{\text{peak}} - \text{NAV}}{E_{\text{peak}}}$$
3. **Emergency Circuit Breaker Trigger**:
   - If $\text{DD}_{\text{daily}} \ge \text{MaxDailyDD}$ OR $\text{DD}_{\text{peak}} \ge \text{MaxPeakDD} \implies$ trigger emergency global flatten order, override all sub-strategies, and lock trading session.
4. **Audit Report Generation**: Output structured `PortfolioStopReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying Solely on Strategy-Level Stops**: Assuming strategy-level stops provide adequate portfolio-level protection during correlation breakdown.
- **Manual Intervention Delays**: Leaving portfolio drawdown response to manual human decision-making during fast market crashes.
- **Failing to Lock Trading Session**: Flattening positions upon stop-loss breach but allowing sub-strategies to immediately re-open new positions on subsequent signal cycles.

## Verification

- Instantiate `PortfolioLevelStopLossIndependentOfStrategyStops`. Input portfolio with $\$1,000,000$ start-of-day equity. Submit position drawdowns resulting in NAV of $\$940,000$ ($6\%$ daily drawdown vs $5\%$ max limit) $\implies$ verify `DAILY_DRAWDOWN_BREACH_FLATTEN` status and `is_trading_locked = True`.
- Run `python scripts/test_portfolio_level_stop_loss_independent_of_strategy_stops.py`.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
---
