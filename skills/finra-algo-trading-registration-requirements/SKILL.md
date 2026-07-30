---
name: finra-algo-trading-registration-requirements
description: >-
  Broker-dealer compliance engine for auditing FINRA Rule 1220(b)(4) registration requirements, verifying Series 57 licensing for algo developers/supervisors, and enforcing deployment controls.
domain: Regulatory Compliance & Governance
subdomain: FINRA Broker-Dealer Registration & Oversight
tags: ["finra-rule-1220b4", "series-57", "algorithmic-trading-registration", "securities-trader", "compliance-audit", "cicd-governance"]
brokers_frameworks: ["FINRA Rule 1220(b)(4)", "Series 57 / SIE", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in broker-dealer compliance platforms, developer onboarding workflows, and CI/CD release pipelines. Under **FINRA Rule 1220(b)(4)** (effective 2018), any associated person of a FINRA member firm who is primarily responsible for the design, development, or significant modification of an algorithmic trading strategy—or who directly supervises such personnel—must pass the **Securities Industry Essentials (SIE)** exam and register as a **Securities Trader (Series 57)**. Deploying algorithmic code authored or approved by non-registered personnel creates severe regulatory violation penalties.

## Prerequisites

- Personnel developer registry (`personnel_id`, `name`, `role`, `is_series_57_active`, `is_sie_active`).
- Algorithmic code change classification (`is_significant_modification`, `affects_automated_order_routing`).
- Code author and approving supervisor IDs.

## Workflow

1. **Rule 1220(b)(4) Scope Audit**:
   - Determine if code change modifies an automated order routing/execution algorithm (excluding purely manual idea generation or raw order pass-through).
2. **Developer & Supervisor Licensing Check**:
   - Verify active Series 57 and SIE registration for code author(s) and approving supervisor.
3. **CI/CD Deployment Gate Action**:
   - If significant algo modification is authored or approved by non-Series 57 personnel $\implies$ Flag `REGISTRATION_VIOLATION_BLOCKED` (blocks build deployment).
   - Else $\implies$ Flag `COMPLIANCE_APPROVED`.
4. **Audit Report Generation**: Output structured `FinraRegistrationAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Exempting Quantitative Software Engineers**: Assuming software developers are exempt because they are "IT staff" rather than traders; Rule 1220(b)(4) explicitly targets developers.
- **Treating Minor Bugfixes as Exemption**: Failing to define what constitutes a "significant modification," allowing major logic updates to bypass compliance checks.
- **Lacking Developer Registration Logs**: Omitting developer-to-algorithm audit mappings during FINRA regulatory examinations.

## Verification

- Instantiate `FinraAlgoRegistrationEngine`. Register Developer A (Series 57 Active) and Developer B (Unregistered). Scenario 1: Developer A submits significant modification to VWAP router $\implies$ verify engine outputs `COMPLIANCE_APPROVED`. Scenario 2: Developer B submits modification to market-making engine $\implies$ verify engine flags `REGISTRATION_VIOLATION_BLOCKED` and blocks deployment.
- Run `python scripts/test_finra_algo_trading_registration_requirements.py`.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `record-keeping-requirements-for-tax-audit-defense`
---
