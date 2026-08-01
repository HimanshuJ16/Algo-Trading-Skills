---
name: risk-control-bypass-audit-logging
description: >-
  Risk control bypass audit logging engine recording every manual override of pre-trade and intra-trade risk controls, classifying bypass severity, flagging unauthorized or unjustified bypasses, and generating immutable audit trail reports.
domain: Risk & Compliance Governance
subdomain: Risk Override Audit & Accountability
tags: ["risk-bypass", "audit-logging", "risk-override", "compliance-audit", "kill-switch", "position-limits"]
brokers_frameworks: ["SOX Audit Trail Requirements", "MiFID II Risk Control Overrides", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when operating algorithmic trading systems with pre-trade risk controls (position limits, daily loss limits, spread vetoes, kill switches) that can be manually overridden by authorized personnel. Regulators (SEC, ESMA, FCA) require immutable audit trails documenting every risk control bypass — who authorized it, why, and what was overridden. This engine logs bypass events, classifies severity (CRITICAL for kill switches and loss limits), flags unauthorized principals or missing justifications, and generates compliance audit reports.

## Prerequisites

- Bypass event details (`event_id`, `bypassed_control`, `original_limit_value`, `override_value`, `authorized_by`, `justification`).
- Authorized principal allowlist (default: `risk_officer`, `cro`, `head_of_trading`, `system_admin`).
- Critical control set (default: `MAX_POSITION_SIZE`, `DAILY_LOSS_LIMIT`, `PORTFOLIO_VAR_LIMIT`, `KILL_SWITCH`, `MARGIN_CALL_HALT`).

## Workflow

1. **Bypass Event Logging**:
   - Record every manual override with timestamp, control name, original/override values, authorizer, and justification.
2. **Severity Classification**:
   - Classify as `CRITICAL` (kill switch, loss limits), `HIGH` (any limit/cap), or `MEDIUM` (other controls).
3. **Suspicious Pattern Detection**:
   - Flag unauthorized principals (not in allowlist) or missing/insufficient justifications.
4. **Audit Report Generation**: Output structured `RiskBypassAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **No Immutable Logging**: Allowing bypass logs to be edited or deleted post-hoc defeats regulatory audit requirements.
- **Overly Broad Authorization**: Granting all traders bypass authority instead of restricting to senior risk officers.
- **Missing Justification Fields**: Logging the override but not requiring a written justification, making post-incident forensics impossible.

## Verification

- Instantiate `RiskControlBypassAuditEngine`. Log bypass by `risk_officer` with justification $\implies$ verify `CRITICAL` severity, not suspicious. Log bypass by `junior_trader` $\implies$ verify flagged suspicious with unauthorized principal reason. Generate audit report $\implies$ verify `SUSPICIOUS_BYPASSES_DETECTED`.
- Run `python scripts/test_risk_override_audit_logger.py`.

## Related Skills

- `regulatory-capital-requirement-tracking`
- `reinforcement-learning-safety-constraints-for-execution`
---
