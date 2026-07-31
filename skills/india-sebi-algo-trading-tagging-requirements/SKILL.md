---
name: india-sebi-algo-trading-tagging-requirements
description: >-
  Regulatory compliance engine for Securities and Futures Board of India (SEBI) algorithmic trading circulars, enforcing unique Exchange Algo IDs, PRO/CLI tagging, 10 OPS limits, and Order-to-Trade Ratio (OTR) penalties.
domain: Regulatory Compliance Global
subdomain: Indian Market Regulation & SEBI Algo Governance
tags: ["sebi", "algo-tagging", "nse", "bse", "algo-id", "otr-monitoring", "pro-cli-category", "ops-threshold"]
brokers_frameworks: ["SEBI Algorithmic Trading Guidelines", "NSE NOW / NEAT FIX", "BSE Bolt Plus", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying automated trading algorithms or API execution bots on Indian stock exchanges (NSE, BSE, MCX). SEBI circulars mandate that EVERY automated order MUST be tagged with a unique **Exchange-Approved Algo ID** (`algo_id`), classified under `PRO` (Proprietary) or `CLI` (Client) categories, operated from static IP addresses, and monitored for **Order-to-Trade Ratio (OTR)** breaches ($OTR \ge 500$ or $\ge 2,000$).

## Prerequisites

- Order payload (`algo_id`, `is_registered_with_exchange`, `client_category`: `PRO`/`CLI`, `symbol`, `exchange`: `NSE`/`BSE`, `ops_rate`).
- Daily Order-to-Trade Ratio (OTR) tracker metrics (`total_order_messages`, `total_executed_trades`).

## Workflow

1. **Exchange-Approved Algo ID & Tagging Audit**:
   - Audit `algo_id` presence and `is_registered_with_exchange == True`. Untagged or unregistered orders MUST be rejected.
   - Verify `client_category` is either `"PRO"` or `"CLI"`.
2. **Orders-Per-Second (OPS) Threshold Check**:
   - Verify `ops_rate <= 10` for Generic Algo ID tags.
3. **Daily Order-to-Trade Ratio (OTR) Monitoring**:
   - Compute $OTR = \frac{N_{\text{order\_messages}}}{N_{\text{executed\_trades}}}$.
   - If $OTR \ge 2,000 \implies$ Trigger Critical SEBI OTR Cooling-off Alert.
   - If $OTR \ge 500 \implies$ Trigger OTR Penalty Warning.
4. **Audit Report Generation**: Output structured `SebiAlgoTaggingReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Submitting Untagged Automated Orders**: Routing algorithmic orders without a valid SEBI-approved Algo ID, violating SEBI regulations and risking exchange fines.
- **Exceeding 2,000 Order-to-Trade Ratio (OTR)**: Submitting thousands of order modifications/cancellations per single fill, triggering SEBI cooling-off trading suspensions.
- **Misclassifying Proprietary Trading as Client**: Tagging PRO account orders as CLI or vice versa, violating SEBI account segregation rules.

## Verification

- Instantiate `SebiAlgoTaggingEngine`. Audit Valid Registered Order (`algo_id="NSE_ALGO_9981"`, `is_registered=True`, `category="PRO"`, $OTR = 40$) $\implies$ verify `SEBI_TAGGING_APPROVED`. Audit Untagged Order (`algo_id=""`) $\implies$ verify `REJECTED_UNTAGGED_ALGO`. Audit OTR Breach ($OTR = 2,500 > 2,000$) $\implies$ verify `SEBI_OTR_COOLING_OFF_PENALTY`.
- Run `python scripts/test_india_sebi_algo_trading_tagging_requirements.py`.

## Related Skills

- `finra-algo-trading-registration-requirements`
- `order-to-trade-ratio-fee-penalty-avoidance`
---
