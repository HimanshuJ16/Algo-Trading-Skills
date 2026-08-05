---
name: risk-limit-breach-escalation-matrix
description: >-
  Production-grade risk limit breach escalation matrix evaluating metric breaches against multi-tier severity thresholds (AMBER, RED, CRITICAL), duration auto-escalations, notification channel routing, and automated mitigation actions (WARN, REDUCE, HALT, FLATTEN).
domain: Risk Management & Compliance Governance
subdomain: Risk Escalation & Incident Response
tags: ["risk-escalation", "limit-breach", "escalation-matrix", "pagerduty", "risk-governance", "automated-flatten"]
brokers_frameworks: ["Risk Limit Escalation Matrix", "Python Dataclasses", "Unittest"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when defining or enforcing automated escalation protocols for risk limit breaches (daily drawdown, max position caps, leverage limits, VAR breaches). In algorithmic trading, unhandled or delayed limit breaches lead to catastrophic drawdowns and regulatory violations. An escalation matrix provides a deterministic mapping from breach ratio ($100\%$, $120\%$, $150\%$, $200\%$) and breach duration to specific response actions (`WARN`, `REDUCE`, `HALT`, `FLATTEN`) and notification routing (Slack, Email, PagerDuty, Compliance Ticket).

## Prerequisites

- Breach event specification (`event_id`, `metric_name`, `strategy_id`, `current_value`, `limit_value`, `timestamp_iso`, `duration_seconds`).
- Multi-tier policy configuration (`ratio_threshold`, `severity`, `action`, `channels`, `ack_timeout_seconds`).

## Workflow

1. **Breach Ratio Calculation**:
   - Compute breach ratio: $\text{Ratio} = \frac{\text{Current Metric Value}}{\text{Statutory Limit Value}}$.
2. **Policy Threshold Matching**:
   - Map ratio to multi-tier policy thresholds ($1.0 \implies \text{INFO/WARN}$, $1.2 \implies \text{AMBER/REDUCE}$, $1.5 \implies \text{RED/HALT}$, $2.0 \implies \text{CRITICAL/FLATTEN}$).
3. **Duration Auto-Escalation**:
   - If breach duration exceeds 300s, automatically escalate action to next severity tier.
4. **Notification Routing & Audit Trail**:
   - Route alerts to designated channels (PagerDuty, Slack, Email) and append decision to immutable audit log.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Manual-Only Escalations**: Relying on human risk managers to manually flatten positions during fast market crashes without automated enforcement.
- **Ignoring Breach Duration**: Treating a 105% breach sustained for 2 hours as a low-priority warning instead of escalating it.
- **Unrouted Critical Notifications**: Sending 200% drawdown breach alerts to Slack channels instead of PagerDuty or compliance ticketing.

## Verification

- Instantiate `RiskEscalationMatrix`. Process 2.5x drawdown breach $\implies$ verify `CRITICAL` severity, `FLATTEN` action, and PagerDuty routing. Process 1.05x breach with 360s duration $\implies$ verify auto-escalated from `WARN` to `REDUCE`.
- Run `python scripts/test_risk_escalation_matrix.py`.

## Related Skills

- `risk-control-bypass-audit-logging`
- `kill-switch-and-drawdown-circuit-breakers`
---
