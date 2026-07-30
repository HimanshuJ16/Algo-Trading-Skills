---
name: cross-strategy-signal-reuse-and-licensing
description: Quantitative alpha governance engine for managing cross-strategy signal
  reuse, entitlement access control, internal transfer pricing fee attribution, and
  AUM capacity limits.
domain: Signal Governance & Licensing
subdomain: Alpha Marketplace & Transfer Pricing
tags:
- signal-reuse
- signal-licensing
- entitlement
- alpha-marketplace
- transfer-pricing
- fee-attribution
- aum-capacity
brokers_frameworks:
- OECD Transfer Pricing
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in multi-strategy platforms, quantitative research hubs, and internal alpha marketplaces where proprietary signals or alt-data features (e.g. NLP Sentiment, Options Flow, Satellite Spend) are shared across multiple sub-strategy pods or legal entities. Reusing signals scales R&D efficiency, but requires strict entitlement access control, capacity tracking ($\text{AUM} \le \text{Capacity}_{\text{max}}$), and arm's-length transfer pricing fee attribution ($\text{Fee} = \text{Base Fee} + \text{PnL Share} \times \text{PnL}$).

## Prerequisites

- Registered signal metadata (`signal_id`, `base_fee_usd`, `pnl_share_pct`, `max_aum_capacity_usd`).
- Strategy pod subscription requests (`strategy_id`, `signal_id`, `allocated_aum_usd`).

## Workflow

1. **Signal Catalog & Capacity Registration**: Register signal licensing parameters and capacity caps.
2. **Entitlement Access Verification**:
   - Check if strategy subscription is active.
   - Verify total subscribed AUM: $\sum \text{AUM}_{\text{sub}} \le \text{Max Capacity}$.
   - Verify cross-border transfer pricing entitlement rules (OECD arm's length principle).
3. **Internal Fee Attribution Calculation**:
   - $\text{Fee}_{\text{total}} = \text{Base Fee} + \left(\text{PnL Share Pct} \times \max(0, \text{Strategy PnL})\right)$.
4. **Audit Reporting**: Generate structured `SignalLicensingAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-capped Signal Capacity**: Allowing too many strategy pods to trade the same alpha signal, degrading signal Sharpe ratio due to self-cannibalization.
- **Ignoring Cross-Border Transfer Pricing**: Transferring proprietary signals between US research hubs and UK/HK execution desks without arm's-length fee attribution, incurring severe tax audit penalties.
- **Un-tracked Subscriptions**: Consuming proprietary alt-data signals without logging entitlement permissions, violating third-party vendor redistribution contracts.

## Verification

- Instantiate `SignalReuseAndLicensingEngine`. Register `Sentiment_Alpha` (Base Fee \$10,000, 5% PnL Share, Max AUM \$50M). Subscribe `Pod_Alpha` (\$20M AUM) and `Pod_Beta` (\$25M AUM). Verify both subscriptions pass entitlement. Attempt to subscribe `Pod_Gamma` (\$15M AUM, total \$60M > \$50M cap) and verify engine blocks entitlement due to capacity breach. Calculate fee attribution for `Pod_Alpha` with \$1M PnL (\$60,000 total fee).
- Run `python scripts/test_cross_strategy_signal_reuse_and_licensing.py`.

## Related Skills

- `cross-strategy-tax-lot-optimization`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`
---
