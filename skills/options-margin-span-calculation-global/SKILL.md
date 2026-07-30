---
name: options-margin-span-calculation-global
description: Use when a strategy trades options/futures and needs to estimate margin
  requirements accurately across brokers/exchanges using SPAN-style or broker-specific
  portfolio-margin methodologies, to avoid a strategy that looks fine on paper but
  is unplaceable due to margin
domain: algorithmic-trading
subdomain: multi-asset-derivatives
tags:
- multi-asset-derivatives
- span-(standard-portfolio-analysis-of-risk)
- broker-specific-portfolio-margin-models
brokers_frameworks:
- SPAN (Standard Portfolio Analysis of Risk)
- broker-specific portfolio margin models
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any options or futures strategy where margin requirement, not just notional exposure, determines whether a position is actually placeable and how much capital it ties up. A backtest or strategy design that only tracks notional exposure or a naive per-contract margin estimate can produce a strategy that appears well within risk limits on paper but cannot actually be placed live because the broker's real margin methodology (commonly SPAN-based, though many brokers layer their own portfolio-margin logic on top) requires substantially more or less capital than assumed — and margin requirements for multi-leg options positions in particular are not a simple sum of each leg's individual margin, since offsetting legs can reduce net margin significantly.

## Prerequisites

- Access to the broker's actual margin calculator (many brokers expose a margin-calculator API or downloadable tool) rather than relying on a generic formula, since margin methodology varies by broker even when both nominally use SPAN as a base
- Understanding of whether the account is on SPAN margining, broker-specific portfolio margining, or simple Reg-T-style margining (US) — these produce meaningfully different capital requirements for the same position

## Workflow

1. Never estimate multi-leg options margin as the sum of each leg's individual margin requirement — SPAN and portfolio-margin methodologies evaluate a position's risk across a matrix of price and volatility scenarios and net offsetting legs against each other, so a defined-risk spread (e.g. an iron condor) typically requires margin closer to its maximum loss than to the sum of its legs' naive individual margins.
2. Where the broker exposes a margin-calculator API, call it directly with the proposed multi-leg order before placing it, and treat the returned figure as authoritative over any internally-estimated approximation — internal approximations are useful for fast pre-screening of candidate trades, not as the final gate before order placement.
3. Where no margin-calculator API exists, implement a local SPAN-methodology approximation (scanning a defined set of price/volatility shock scenarios and taking the worst-case loss across the scenario matrix) as a conservative estimate, and explicitly flag it as an approximation subject to broker reconciliation — do not present an internal SPAN approximation as equivalent to the broker's authoritative figure without validating a sample of approximated figures against actual broker margin calls.
4. Account for margin requirement changes intraday — margin is generally recalculated by the broker at least once daily (often more frequently during volatile periods) based on updated volatility and price scenarios, meaning a position's margin requirement is not fixed at entry and can increase without the position itself changing, directly affecting available capital for new trades; monitor available margin as a live, changing figure rather than a static number computed once at position entry.
5. For backtests, model margin utilization explicitly if the strategy could ever be capital-constrained (i.e., if total margin required across concurrent positions could approach account capital) — a backtest that ignores margin constraints implicitly assumes infinite capital availability, which can materially overstate how many concurrent positions the strategy could actually hold live.
6. When trading across multiple brokers or exchanges with different margin methodologies for economically similar positions, do not assume margin figures are comparable or fungible between them — a spread that requires modest margin at one broker's methodology may require substantially more at another's, and aggregate portfolio risk-limit checks (see `correlation-aware-exposure-limits`) should account for actual margin-methodology-specific figures per venue, not a single blended assumption.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Estimating multi-leg options margin as a naive sum of individual leg margins, dramatically overestimating (and thus over-constraining) the capital a defined-risk spread actually requires.
- Treating margin requirement as fixed at position entry rather than a figure the broker recalculates periodically based on updated volatility/price scenarios, missing the possibility of a margin increase on an unchanged position eating into available capital for new trades.
- Building a backtest that ignores margin constraints entirely, implicitly assuming unlimited capital and overstating how many concurrent positions the strategy could realistically hold.
- Assuming margin figures are directly comparable across brokers/exchanges using different methodologies for the same or similar positions.
- Relying on an internal SPAN approximation as authoritative without ever validating it against actual broker-reported margin figures for real positions.

## Verification

- For a sample of representative multi-leg positions, compare an internal margin estimate against the broker's actual margin-calculator output (or actual margin charged on a paper/live position) and confirm they're reasonably close, with any systematic bias documented.
- Confirm the backtest's margin-utilization tracking (if implemented) produces a plausible concurrent-position ceiling that's consistent with the account's actual available capital, rather than allowing unlimited concurrent positions.
- Confirm live monitoring surfaces a margin-requirement change on an existing, unchanged position (e.g., following a volatility spike) as a live event, not something only noticed when a subsequent order is unexpectedly rejected for insufficient margin.

## Related Skills

- `correlation-aware-exposure-limits`
- `execution-realistic-simulation`
- `kill-switch-and-drawdown-circuit-breakers`
