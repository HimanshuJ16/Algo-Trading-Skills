---
name: leverage-limit-enforcement-across-instruments
description: >-
  Pre-trade risk gateway enforcing global Gross Leverage, Net Directional Leverage, and asset-class specific leverage caps across multi-instrument portfolios (Equities, Crypto, FX, Futures).
domain: Portfolio Multi-Strategy
subdomain: Cross-Asset Leverage & Risk Governance
tags: ["leverage-limit", "gross-leverage", "net-leverage", "risk-governance", "cross-margin", "pre-trade-risk", "crypto-futures"]
brokers_frameworks: ["Reg T Margin Rules", "AIFMD Leverage Framework", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing multi-asset portfolios containing equities, crypto perpetual futures, FX swaps, and commodity futures. Trading across heterogeneous asset classes requires strict pre-trade enforcement of **Gross Leverage** ($L_{\text{gross}} = \frac{\sum |\text{Notional}|}{\text{Equity}}$) and **Net Directional Leverage** ($L_{\text{net}} = \frac{|\sum \text{Signed Notional}|}{\text{Equity}}$). This module intercepts proposed orders, projects resulting gross/net leverage ratios, enforces asset-class specific leverage caps (Crypto $3.0\times$, Equities $2.0\times$, FX $10.0\times$), and vetoes breaching orders.

## Prerequisites

- Portfolio balance payload (`portfolio_equity_usd`, `active_positions`: list of `PositionSpec`).
- Proposed order payload (`symbol`, `asset_class`: `EQUITY`/`CRYPTO`/`FX`/`FUTURES`, `side`: `BUY`/`SELL`, `order_notional_usd`).
- Leverage limits config (`max_gross_leverage`, `max_net_leverage`, `asset_class_limits`).

## Workflow

1. **Active Portfolio Exposure Audit**:
   - Compute current Gross Notional $\sum |\text{Notional}_i|$ and Net Notional $|\sum \text{Signed Notional}_i|$.
   - Calculate current $L_{\text{gross}}$ and $L_{\text{net}}$.
2. **Projected Order Leverage Impact Modeling**:
   - Add proposed order notional to active positions.
   - Compute projected $L_{\text{projected, gross}}$ and $L_{\text{projected, net}}$.
   - Compute projected per-asset-class leverage $L_{\text{projected, asset}}$.
3. **Pre-Trade Veto Audit Rules**:
   - Audit $L_{\text{projected, gross}} \le \text{max\_gross\_leverage}$ (e.g. $3.0\times$). If breached $\implies$ Trigger `REJECTED_GROSS_LEVERAGE_BREACH`.
   - Audit $L_{\text{projected, net}} \le \text{max\_net\_leverage}$ (e.g. $1.5\times$). If breached $\implies$ Trigger `REJECTED_NET_LEVERAGE_BREACH`.
   - Audit $L_{\text{projected, asset}} \le \text{max\_asset\_leverage}$. If breached $\implies$ Trigger `REJECTED_ASSET_CLASS_LEVERAGE_BREACH`.
4. **Audit Report Generation**: Output structured `LeverageEnforcementReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Confusing Gross and Net Leverage**: Allowing an account with $5.0\times$ gross leverage to trade because net directional leverage is $0.0\times$, ignoring correlation breakdown and liquidation risks during stress events.
- **Ignoring Derivative Synthetic Leverage**: Counting option premium paid instead of total underlying delta-equivalent notional exposure.
- **Applying Uniform Leverage Caps**: Applying a single $10\times$ leverage cap across both stable FX pairs and highly volatile crypto perpetuals.

## Verification

- Instantiate `LeverageLimitEnforcerEngine`. Audit $\$100\text{k}$ Equity Portfolio with $\$150\text{k}$ Longs and $\$50\text{k}$ Shorts ($L_{\text{gross}} = 2.0\times$). Propose $\$50\text{k}$ Equity Buy ($L_{\text{projected, gross}} = 2.5\times \le 3.0\times$) $\implies$ verify `ORDER_LEVERAGE_APPROVED`. Propose $\$150\text{k}$ Crypto Buy ($L_{\text{projected, gross}} = 3.5\times > 3.0\times$) $\implies$ verify `REJECTED_GROSS_LEVERAGE_BREACH`.
- Run `python scripts/test_leverage_limit_enforcer.py`.

## Related Skills

- `portfolio-level-stop-loss-independent-of-strategy-stops`
- `broker-account-margin-call-handling`
---
