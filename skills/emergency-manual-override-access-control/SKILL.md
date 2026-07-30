---
name: emergency-manual-override-access-control
description: Quantitative infrastructure security engine for managing break-glass
  emergency manual overrides (kill switches, algo halts), dual-signature authorization,
  role-based access control (RBAC), and immutable audit logging.
domain: Infrastructure & Security
subdomain: Access Control & Emergency Overrides
tags:
- emergency-override
- break-glass
- kill-switch
- rbac
- dual-sign-off
- audit-logging
- compliance
brokers_frameworks:
- RBAC Framework
- SHA-256 Audit Trail
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative trading infrastructure, risk management platforms, and exchange connectivity adapters. During market flash crashes, software bugs, or algorithm rogue loops, human operators must trigger manual overrides (e.g. `KILL_SWITCH_ALL_ALGOS`, `HALT_STRATEGY`). To prevent unauthorized or accidental intervention, critical overrides require **Break-Glass Access Control** with Role-Based Access Control (RBAC), dual operator sign-off, and immutable audit logs.

## Prerequisites

- Requesting operator credentials (`operator_id`, `role` e.g. `'RISK_OFFICER'`, `'HEAD_TRADER'`).
- Secondary approver credentials (`secondary_operator_id`, `secondary_role`) for critical actions.
- Target system ID (`target_system_id`) and mandatory justification reason string.

## Workflow

1. **RBAC & Action Severity Classification**:
   - Determine action severity: `SEVERITY_HIGH` (`HALT_STRATEGY`) vs `SEVERITY_CRITICAL` (`KILL_SWITCH_ALL_ALGOS`).
2. **Dual Sign-Off & Break-Glass Audit**:
   - For `SEVERITY_CRITICAL`: Audit presence of secondary authorized approver OR valid Break-Glass token.
   - Validate operator roles against authorized RBAC matrix (`RISK_OFFICER`, `HEAD_TRADER`, `CTO`).
3. **Immutable Audit Hash Generation**:
   - Compute SHA-256 hash over (`operator_id`, `timestamp`, `target_system_id`, `justification`).
4. **Override Execution & TTL Expiry Setup**:
   - Execute override command and set time-to-live auto-expiry (e.g. 60 mins).
5. **Audit Report Generation**: Output structured `OverrideControlReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single-Point-of-Failure Unprotected Kill Switches**: Allowing any junior developer to trigger a firm-wide kill switch without secondary sign-off or MFA.
- **Un-Audited Manual Overrides**: Executing manual database or process overrides without recording justification reasons or audit logs.
- **Indefinite Overrides**: Leaving trading algorithms manually halted indefinitely after a market disturbance without automated TTL expiry notifications.

## Verification

- Instantiate `EmergencyOverrideAccessEngine`. Request `KILL_SWITCH_ALL_ALGOS` with single junior dev operator. Verify engine rejects `UNAUTHORIZED_SEVERITY_CRITICAL`. Submit dual sign-off (`RISK_OFFICER` + `HEAD_TRADER`) with valid justification. Verify engine approves override, generates SHA-256 audit hash, and sets 60-minute TTL.
- Run `python scripts/test_override_access_control.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
---
