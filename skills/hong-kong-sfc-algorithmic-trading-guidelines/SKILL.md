---
name: hong-kong-sfc-algorithmic-trading-guidelines
description: >-
  Hong Kong Securities and Futures Commission (SFC) compliance engine enforcing Schedule 7 Code of Conduct rules for algorithmic trading, pre-trade controls, short selling locate checks, and kill switch mass cancels.
domain: Regulatory Compliance Global
subdomain: Hong Kong SFC Schedule 7 & Algorithmic Governance
tags: ["sfc", "schedule-7", "hong-kong-regulation", "algorithmic-trading", "pre-trade-controls", "covered-short-selling", "kill-switch"]
brokers_frameworks: ["Hong Kong SFC Code of Conduct", "Schedule 7", "HKEX Exchange Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying algorithmic trading systems or Direct Market Access (DMA) gateways operating on the Hong Kong Exchange (HKEX). The Hong Kong Securities and Futures Commission (SFC) **Code of Conduct Schedule 7** mandates strict pre-trade risk controls (max order value, price deviation limits), mandatory **covered short selling locate pre-checks** (prohibiting illegal naked shorting), certified developer/algo registrations, emergency **kill switches**, and 2-year audit trail record keeping.

## Prerequisites

- Algo configuration (`algo_id`, `developer_registration_active`, `uat_signoff_completed`).
- Pre-trade risk parameters (`max_order_value_hkd = 10,000,000`, `max_price_deviation_pct = 5.0%`).
- Order payload (`stock_code`, `side`, `price`, `quantity`, `is_short_sell`, `has_locate_borrow`).

## Workflow

1. **Qualification & UAT Authorization Audit (Schedule 7, Para 1-2)**:
   - Audit `algo_id` registration and `developer_registration_active`. Reject unregistered algorithms.
2. **Pre-Trade Risk Control Audit (Schedule 7, Para 3)**:
   - Order Value Check: Verify $\text{Order Value HKD} \le 10,000,000$.
   - Price Deviation Check: Verify $|\frac{P_{\text{order}} - P_{\text{market}}}{P_{\text{market}}}| \le 5.0\%$.
   - Covered Short Selling Check: If `is_short_sell = True`, verify `has_locate_borrow = True`. Reject naked short orders.
3. **Emergency Kill Switch Audit (Schedule 7, Para 4)**:
   - Provide `trigger_sfc_kill_switch()` to immediately cancel all open orders and lock entry gates.
4. **Audit Report Generation**: Output structured `HkSfcComplianceReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Naked Short Selling Violations**: Submitting short sale orders without pre-confirming stock borrow locate, violating Hong Kong Securities and Futures Ordinance Section 170.
- **Un-Capped Pre-Trade Limits**: Routing algorithms without hard Max Order Value or Max Price Deviation controls, risking runaway algo market disruption.
- **Lacking Emergency Kill Switches**: Deploying HKEX algorithms without an automated kill switch capable of mass-cancelling resting orders.

## Verification

- Instantiate `HkSfcAlgorithmicTradingEngine`. Test Valid Order (Tencent 00700, Value HKD 5M, Dev Reg Active) $\implies$ verify `SFC_COMPLIANT_APPROVED`. Test Naked Short Sale (`is_short_sell=True`, `has_locate_borrow=False`) $\implies$ verify `REJECTED_ILLEGAL_NAKED_SHORT`. Test Price Deviation breach ($8\% > 5\%$) $\implies$ verify `REJECTED_PRICE_DEVIATION_LIMIT`.
- Run `python scripts/test_hong_kong_sfc_algorithmic_trading_guidelines.py`.

## Related Skills

- `finra-algo-trading-registration-requirements`
- `execution-algorithm-kill-switch-integration`
---
