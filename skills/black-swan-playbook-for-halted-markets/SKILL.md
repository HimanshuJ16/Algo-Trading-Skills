---
name: black-swan-playbook-for-halted-markets
description: >-
  Use when operating live trading bots to handle exchange trading halts (LULD, volatility halts, circuit breakers), executing automated fallback playbooks including proxy index hedging and post-halt auction resume management.
domain: algorithmic-trading
subdomain: risk-management
tags: ["risk-management", "trading-halt", "circuit-breaker", "black-swan", "proxy-hedging", "luld", "halt-playbook"]
brokers_frameworks: ["Black Swan Halted Market Engine", "Python"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating live automated trading systems across equities, futures, options, or crypto. Exchange trading halts (Limit-Up/Limit-Down LULD halts, market-wide circuit breakers Level 1/2/3, news pending halts) freeze order execution while underlying risk accumulates. Naive algos spam failed order retries or remain completely unhedged while correlated markets collapse. This skill executes an automated halt playbook: canceling un-filled working orders, deploying proxy index hedges, and managing auction resumption.

## Prerequisites

- Exchange Feed Halt Notification (`HALT`, `RESUME`, `RESUME_AUCTION`).
- Cross-asset correlation map (e.g. NVDA long position $\rightarrow$ QQQ ETF proxy hedge).

## Workflow

1. **Detect Trading Halt Event**: Ingest exchange status feed (`symbol`, `halt_reason`, `timestamp`).
2. **Cancel Pending Working Orders**: Immediately cancel all open limit/stop orders for halted symbol.
3. **Deploy Correlated Proxy Hedge**:
   If portfolio has open position $P_{\text{halt}}$ in halted asset, execute offsetting hedge in liquid proxy ETF/futures:
   $$\text{HedgeQty} = -\text{beta} \times P_{\text{halt}}$$
4. **Prepare Auction Resumption Order**: Calculate fair value post-halt price and place auction limit order upon `RESUME_AUCTION` signal.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Spamming Orders During Halt**: Continuing to send API order modifications during an exchange halt, incurring broker rate limit penalties or API bans.
- **Unhedged Market-Wide Circuit Breakers**: Failing to hedge individual stock holdings when index futures continue trading during a single-name halt.

## Verification

- Inject LULD halt event on open $1,000$ share long position, verify order cancelation, proxy hedge execution (QQQ short), and auction resume preparation.
- Run `python scripts/test_halted_market_engine.py` and confirm 100% pass rate.

## Related Skills

- `greeks-based-portfolio-hedging-automation`
- `fallback-and-redundancy-architecture`
- `kill-switch-and-drawdown-circuit-breakers`
---
