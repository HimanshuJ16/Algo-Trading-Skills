---
name: post-only-limit-repricing-under-fast-markets
description: >-
  Adaptive fast-market post-only limit repricing engine preventing exchange order rejection churn during high-velocity price movements.
domain: Execution Algorithms
subdomain: Market Microstructure & Order Type Management
tags: ["post-only", "limit-repricing", "fast-markets", "rejection-churn", "maker-taker", "microstructure", "execution-algo"]
brokers_frameworks: ["Exchange Native Post-Only APIs", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying passive liquidity-providing execution algorithms in fast-moving market regimes (e.g. CPI news releases, flash volatility spikes). In fast markets, bid-ask spreads shift within microseconds. If an algorithm submits a naive Post-Only limit order at a stale price that crosses the newly updated spread, the exchange matching engine immediately rejects the order (`Post-or-Cancel`). Repeated naive retries cause "Rejection Churn" (exhausting API message limits and missing execution priority). This engine dynamically reprices Post-Only orders to current passive BBO boundaries.

## Prerequisites

- Real-time market state (`symbol`, `best_bid`, `best_ask`, `tick_size`, `market_velocity_ticks_per_sec`).
- Order request spec (`order_id`, `side`: `'BUY'`/`'SELL'`, `quantity`, `desired_price`).
- Reprice config (`max_reprice_attempts`: default 3, `min_tick_offset`: default 1).

## Workflow

1. **Spread Crossing Detection**:
   - Check if proposed limit price crosses the spread ($\text{BUY} \ge \text{best\_ask}$ or $\text{SELL} \le \text{best\_bid}$).
2. **Passive BBO Boundary Repricing**:
   - For BUY orders, reprice to $\text{best\_bid}$.
   - For SELL orders, reprice to $\text{best\_ask}$.
3. **Rejection Churn & Rate Limit Protection**:
   - Audit reprice attempts ($N_{\text{reprice}} \le \text{MaxAttempts}$) to prevent runaway API messaging.
4. **Audit Report Generation**: Output structured `FastMarketRepriceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naive Rejection Retry Loops**: Repeatedly resubmitting rejected Post-Only orders at stale prices, triggering exchange rate limit penalties.
- **Taker Fee Contamination**: Disabling Post-Only flags during fast markets to force execution, incurring unexpectedly high Taker fees.
- **Ignoring Tick Size Boundaries**: Repricing to arbitrary floating point values not aligned with exchange tick size increments.

## Verification

- Instantiate `FastMarketPostOnlyRepricer`. Submit BUY order @ $\$100.10$ when `best_bid = $100.00` and `best_ask = $100.05` (spread crossed) under fast market velocity ($25$ ticks/sec) $\implies$ verify passive repricing to $\$100.00$ (`POST_ONLY_PASSIVE_REPRICED`) and rejection churn prevention.
- Run `python scripts/test_post_only_limit_repricing_under_fast_markets.py`.

## Related Skills

- `post-only-and-maker-taker-fee-optimization`
- `message-rate-limit-vs-latency-tradeoff-tuning`
---
