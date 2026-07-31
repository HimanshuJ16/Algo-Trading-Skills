---
name: on-call-rotation-and-escalation-for-trading-systems
description: >-
  Site Reliability Engineering (SRE) on-call rotation manager evaluating incident severity SLAs (SEV-1, SEV-2, SEV-3), multi-tier escalation timelines (Primary, Secondary, Executive), and notification routing.
domain: SRE & System Reliability
subdomain: On-Call Rotations & Automated Escalation Policies
tags: ["on-call", "sre", "escalation-policy", "incident-management", "sev1-sla", "pagerduty", "trading-reliability"]
brokers_frameworks: ["PagerDuty / Opsgenie Spec", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when designing on-call rotations and incident escalation procedures for live algorithmic trading systems. In high-frequency and multi-asset trading, unhandled system failures (e.g. broker disconnects, kill switch triggers, memory leaks, high latency) can cause catastrophic financial losses if unacknowledged. This manager enforces multi-tier escalation policies (`PRIMARY` $\rightarrow$ `SECONDARY` $\rightarrow$ `EXECUTIVE`) driven by strict severity SLAs (`SEV_1` $\le 5$ min, `SEV_2` $\le 15$ min, `SEV_3` $\le 60$ min).

## Prerequisites

- Roster of on-call engineers mapped to tiers (`PRIMARY`, `SECONDARY`, `EXECUTIVE`).
- System incident event (`incident_id`, `severity`, `title`, `created_at_utc`).

## Workflow

1. **On-Call Roster & Schedule Registration**:
   - Register engineers into tier roles (`PRIMARY`, `SECONDARY`, `EXECUTIVE`).
2. **Incident Creation & SLA Assignment**:
   - Assign response SLA based on incident severity:
     - `SEV_1` (Critical Halt / Disconnect): Primary $\rightarrow$ Secondary at 3 mins $\rightarrow$ Executive at 5 mins.
     - `SEV_2` (High Latency / Feed Error): Primary $\rightarrow$ Secondary at 10 mins.
     - `SEV_3` (Minor Warning): Primary $\rightarrow$ Secondary at 30 mins.
3. **Escalation Evaluation**:
   - Evaluate elapsed time $\Delta t = T_{\text{current}} - T_{\text{created}}$ for unacknowledged incidents. Advance assigned responder to higher tiers if unacknowledged.
4. **Acknowledgment & Resolution**:
   - Process `acknowledge_incident` and mark resolution status (`ACKNOWLEDGED` / `ESCALATED`).
5. **Audit Report Generation**: Output structured `OnCallEscalationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Single Point of Failure On-Call**: Relying on a single primary engineer without an automated secondary backup escalation policy.
- **Alert Fatigue from Non-Actionable Pager Noise**: Paging on-call engineers via phone for non-critical informational warnings (SEV-3) during off-hours.
- **Lack of Executive Escalation**: Failing to escalate severe unacknowledged SEV-1 incidents to engineering management or head of trading after 5 minutes.

## Verification

- Instantiate `OnCallEscalationManagerEngine`. Register Primary, Secondary, and Executive engineers. Submit SEV-1 incident. At $t=0$ $\implies$ assigned to Primary. At $t=4$ mins (unacknowledged) $\implies$ auto-escalates to Secondary. At $t=6$ mins (unacknowledged) $\implies$ auto-escalates to Executive (`SEV1_SLA_BREACH_ALERT`). Acknowledge incident $\implies$ verify status `ACKNOWLEDGED`.
- Run `python scripts/test_oncall_escalation_manager.py`.

## Related Skills

- `systemd-supervision-for-trading-bots`
- `execution-algorithm-kill-switch-integration`
---
