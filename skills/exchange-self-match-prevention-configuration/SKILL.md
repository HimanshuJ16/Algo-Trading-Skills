---
name: exchange-self-match-prevention-configuration
description: >-
  Quantitative exchange order routing engine for configuring native Self-Match Prevention (SMP / STP) FIX attributes (Tag 7928 SMP ID, Tag 8000 Instruction), auditing self-collisions, and preventing wash trades.
domain: Venue Integration & Protocols
subdomain: Wash Trade Prevention & Order Routing (SMP/STP)
tags: ["smp", "stp", "self-match-prevention", "wash-trade-prevention", "fix-tag-7928", "cme-ilink", "order-routing"]
brokers_frameworks: ["CME iLink SMP", "Nasdaq INET STP", "Coinbase SMP", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in algorithmic execution algorithms, proprietary market making bots, and Smart Order Routers (SOR). To prevent accidental self-execution and comply with regulatory wash trading bans (CFTC Rule 1.38, EU MAR Article 12), exchanges provide native **Self-Match Prevention (SMP / STP)** features. This module configures SMP Group IDs and handling instructions (**Cancel Resting**, **Cancel Aggressive**, **Cancel Both**, **Decrement and Cancel**) on FIX/ETI order headers and audits pre-trade self-collisions.

## Prerequisites

- Registered Firm Self-Match Prevention ID (`smp_id`, e.g. `'SMP_FIRM_8810'`).
- SMP Instruction Mode (`smp_instruction`: `'CANCEL_RESTING'`, `'CANCEL_AGGRESSIVE'`, `'CANCEL_BOTH'`).
- Proposed order details (`cl_ord_id`, `symbol`, `side`, `price`, `qty`).

## Workflow

1. **SMP Header Field Configuration**:
   - Inject FIX Tag 7928 / Tag 2362 (`SelfMatchPreventionID = smp_id`).
   - Inject FIX Tag 8000 (`SelfMatchPreventionInstruction = smp_instruction`).
2. **Pre-Trade Self-Collision Audit**:
   - Inspect resting orders on the opposite side of the order book with matching `smp_id`.
3. **Execution Behavior Simulation & Action**:
   - If collision detected:
     - `CANCEL_RESTING`: Mark resting order for cancellation; allow aggressive order matching.
     - `CANCEL_AGGRESSIVE`: Block incoming aggressive order; preserve resting order.
     - `CANCEL_BOTH`: Cancel both resting and incoming orders.
4. **Audit Report Generation**: Output structured `SmpAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Unregistered SMP IDs**: Submitting unregistered SMP IDs in FIX Tag 7928, triggering instant exchange session order rejections.
- **Configuring Mismatched SMP Modes Across Sub-Strategies**: Mixing `CANCEL_RESTING` and `CANCEL_AGGRESSIVE` across strategies within the same firm SMP group, causing unexpected queue drops.
- **Relying Solely on Local Self-Collision Checks**: Assuming local strategy checks prevent all self-matches without enabling native exchange SMP header flags.

## Verification

- Instantiate `ExchangeSelfMatchPreventionEngine`. Configure `smp_id = 'SMP_PROP_100'`, `smp_instruction = 'CANCEL_RESTING'`. Populate resting SELL order (100 shares @ $150.00, `smp_id = 'SMP_PROP_100'`). Submit incoming BUY order (100 shares @ $150.00, `smp_id = 'SMP_PROP_100'`). Verify engine detects self-collision, marks resting order for cancellation, allows aggressive order routing, and formats FIX Tags 7928/8000. Switch to `CANCEL_AGGRESSIVE` and verify incoming order is blocked.
- Run `python scripts/test_exchange_self_match_prevention_configuration.py`.

## Related Skills

- `eu-market-abuse-regulation-mar-surveillance`
- `conditional-order-logic-for-execution-triggers`
---
