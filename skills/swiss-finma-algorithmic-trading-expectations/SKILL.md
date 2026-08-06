---
name: swiss-finma-algorithmic-trading-expectations
description: >-
  Production-grade Swiss FINMA Algorithmic Trading Compliance Engine auditing FinfraG / FMIA mandatory baseline controls including pre-trade risk limits, emergency Kill Switches, algorithm inventory registration, message throttling, and microsecond audit trails.
domain: Regulatory Compliance & Governance
subdomain: Swiss Financial Market Regulation (FinfraG / FMIA)
tags: ["swiss-finma", "finfrag", "fmia", "compliance-audit", "pre-trade-controls", "kill-switch", "algo-registration"]
brokers_frameworks: ["Swiss FINMA Framework", "FinfraG Compliance Matrix", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying automated algorithmic trading strategies operating on Swiss exchanges (SIX Swiss Exchange, BX Swiss) or under Swiss Financial Market Supervisory Authority (FINMA) regulatory supervision. Under FinfraG (Financial Market Infrastructure Act / FMIA), institutions using algorithmic trading must satisfy strict organizational and risk management mandates. This engine audits 5 mandatory baseline controls: Pre-trade risk controls (Ctrl 1), Non-bypassable emergency Kill Switch (Ctrl 2), Institutional Algorithm Inventory Registration (Ctrl 3), Message Rate Throttling ($\le 100$ msgs/s) (Ctrl 4), and Microsecond Timestamp Audit Trails (Ctrl 5).

## Prerequisites

- Algorithmic trading system audit spec (`AlgoTradingSystemAuditSpec`: `algo_id`, `strategy_version`, `governance_owner`, `has_pre_trade_risk_limits`, `has_independent_kill_switch`, `has_algo_inventory_registration`, `max_message_rate_per_sec`, `has_microsecond_audit_trail`).

## Workflow

1. **Baseline Control Audit**:
   - Evaluate Pre-Trade Limits (price bands $\pm 5\%$, max size caps).
   - Verify Independent Kill Switch (emergency order purge & trading halt).
   - Audit Algorithm Inventory Registration (FinfraG formal change control registry).
   - Check Message Rate Throttling ($\le 100$ messages/second).
   - Verify Microsecond Audit Trail Precision.
2. **Score & Compliance Calculation**:
   - Compute FINMA compliance score ($0-100\%$).
   - Flag non-compliant controls and output remediation instructions.
3. **Execution Output**: Output structured `ComplianceRecord`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Bypassable Pre-Trade Controls**: Allowing trader manual overrides to bypass automated pre-trade price collar or size checks.
- **Embedded Kill Switches**: Implementing kill switches inside the main strategy trading loop rather than as an isolated high-priority supervisor daemon.
- **Unregistered Algorithm Variations**: Deploying minor parameter variations without updating the institutional FinfraG algorithm inventory registry.

## Verification

- Instantiate `SwissFINMAComplianceEngine`. Audit fully compliant algorithm specification $\implies$ verify `is_compliant = True` and `finma_score_pct = 100.0%`. Audit system missing kill switch and microsecond audit trail $\implies$ verify `is_compliant = False` with score $40.0\%$ and failed controls listed.
- Run `python scripts/test_swiss_finma_algorithmic_trading_expectations.py`.

## Related Skills

- `singapore-mas-notice-on-cyber-hygiene-for-trading-systems`
- `execution-algorithm-kill-switch-integration`
---
