---
name: futures-expiry-week-liquidity-and-volatility-handling
description: >-
  Microstructure execution engine for auditing futures expiry week liquidity fragmentation, wider spreads, and Quad-Witching volatility, enforcing position size haircuts and roll mandates.
domain: Market Microstructure & Risk
subdomain: Expiry Week Volatility & Order Book Safeguards
tags: ["futures-expiry", "liquidity-fragmentation", "quad-witching", "order-book-depth", "bid-ask-spread", "position-haircut", "microstructure-risk"]
brokers_frameworks: ["CME Group", "ICE Futures", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in algorithmic execution engines, market-making algorithms, and futures risk managers. During futures expiry week (particularly Quadruple Witching weeks), liquidity migrates away from the front-month contract, causing bid-ask spreads to widen ($> 2.0\text{ ticks}$) and top-of-book depth to drop by $60\% - 90\%$. Trading front-month contracts near expiration without adaptive risk controls causes severe slippage. This module applies mandatory position size haircuts ($50\%$), blocks market orders during wide-spread conditions, and forces contract rolls when $DBE \le 2$ days.

## Prerequisites

- Expiring contract order book metrics (`symbol`, `days_to_expiration`, `bid_ask_spread_ticks`, `top_of_book_depth`, `is_quad_witching`).
- Baseline normal-market depth and spread thresholds.

## Workflow

1. **Microstructure Risk Audit**:
   - Audit Spread: If `bid_ask_spread_ticks > 2.0` $\implies$ Block market orders, mandate limit-only execution.
   - Audit Depth: If `top_of_book_depth < 0.30 * baseline_depth` $\implies$ Apply $50\%$ position size haircut.
   - Audit Expiration Hurdle: If $\text{DBE} \le 2$ days $\implies$ Reject new entries, mandate roll.
2. **Execution Size Adjustment**:
   - $\text{Max Order Qty} = \lfloor \text{Base Order Qty} \times \text{Haircut Factor} \rfloor$.
3. **Execution Mode Determination**:
   - If $\text{DBE} \le 2 \implies$ `MANDATORY_ROLL_REQUIRED`.
   - Else if haircut or spread restricted $\implies$ `EXPIRY_WEEK_RESTRICTED`.
   - Else $\implies$ `NORMAL_EXECUTION`.
4. **Audit Report Generation**: Output structured `FuturesExpiryRiskReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Market Orders in Thinning Expiry Books**: Sending market orders when bid-ask spreads double during expiry week, suffering severe slippage.
- **Ignoring Quad-Witching Settlement Volatility**: Trading un-haircut position sizes during 3rd Friday expiry hours, getting caught in pin risk order imbalances.
- **Failing to Enforce Entry Bans near Expiry**: Opening new long-term positions in contracts expiring in $< 48$ hours.

## Verification

- Instantiate `FuturesExpiryRiskHandlerEngine`. Input normal contract (DBE=15, Spread=1.0, Depth=1000) $\implies$ verify `NORMAL_EXECUTION` and 100% size allowance. Input expiry week contract (DBE=2, Spread=3.5, Depth=200, Quad-Witching=True) $\implies$ verify engine flags `MANDATORY_ROLL_REQUIRED`, blocks market orders, and applies 50% position haircut.
- Run `python scripts/test_futures_expiry_week_liquidity_and_volatility_handling.py`.

## Related Skills

- `futures-contract-roll-automation`
- `order-book-microstructure-signal-research`
---
