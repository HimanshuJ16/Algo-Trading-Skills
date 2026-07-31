---
name: peg-order-types-for-passive-execution
description: >-
  Pegged order types engine dynamically repositioning limit orders relative to Primary, Midpoint, and Market NBBO quotes with discretionary offsets and limit price caps.
domain: Algorithmic Execution & Order Routing
subdomain: Passive Liquidity Provision & Pegged Order Routing
tags: ["pegged-orders", "primary-peg", "midpoint-peg", "market-peg", "passive-execution", "nbbo", "dark-pools"]
brokers_frameworks: ["FIX Protocol 4.4 / 5.0 (OrdType=P)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when executing passive market-making or order slicing strategies requiring automated price tracking relative to National Best Bid and Offer (NBBO) quotes. Manually updating limit order prices in volatile markets incurs high order message rates and latency. Pegged orders automatically adjust limit prices to track the Primary (same side), Midpoint ($0.5 \times (\text{Bid} + \text{Ask})$), or Market (opposite side) quotes, with optional discretionary offsets and protective limit price caps.

## Prerequisites

- Pegged order specification (`order_id`, `symbol`, `side`, `peg_type`, `offset`, `limit_cap`, `quantity`).
- Real-time NBBO market quote feed (`best_bid`, `best_ask`).

## Workflow

1. **Reference Price Determination**:
   - **Primary Peg**: BUY $\rightarrow \text{BestBid}$, SELL $\rightarrow \text{BestAsk}$.
   - **Midpoint Peg**: $\frac{\text{BestBid} + \text{BestAsk}}{2.0}$.
   - **Market Peg**: BUY $\rightarrow \text{BestAsk}$, SELL $\rightarrow \text{BestBid}$.
2. **Offset & Limit Cap Application**:
   - Compute uncapped price: $P_{\text{raw}} = P_{\text{ref}} + \text{Offset}$ (BUY) or $P_{\text{ref}} - \text{Offset}$ (SELL).
   - Enforce Limit Price Cap:
     $$P_{\text{pegged}} = \min(P_{\text{raw}}, P_{\text{limit\_cap}}) \quad \text{(BUY)}$$
     $$P_{\text{pegged}} = \max(P_{\text{raw}}, P_{\text{limit\_cap}}) \quad \text{(SELL)}$$
3. **Repositioning & Repricing Dispatch**:
   - Trigger price modification if $P_{\text{pegged}}$ differs from active order price by $\ge 1$ tick.
4. **Audit Report Generation**: Output structured `PegOrderReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Omitting Protective Limit Caps**: Submitting pegged orders without a limit price cap, allowing buys to chase runaway market spikes.
- **Midpoint Peg Round-Off Errors**: Rounding midpoint prices incorrectly on odd sub-penny spreads ($0.005$ increment).
- **Excessive Repricing Message Spam**: Repricing pegged orders on every microsecond tick change without minimum tick threshold filters.

## Verification

- Instantiate `PegOrderTypesForPassiveExecutionEngine`. Input NBBO ($\text{Bid}=\$100.00, \text{Ask}=\$100.10$). Evaluate Primary Peg BUY with $+\$0.01$ offset $\implies$ verify limit price $\$100.01$. Evaluate Midpoint Peg $\implies$ verify limit price $\$100.05$. Evaluate Limit Cap constraint ($\text{Cap}=\$100.03$) $\implies$ verify price clamped to $\$100.03$.
- Run `python scripts/test_peg_order_types_for_passive_execution.py`.

## Related Skills

- `post-only-limit-repricing-under-fast-markets`
- `iceberg-order-native-broker-support-vs-simulation`
---
