---
name: regulatory-sandbox-programs-for-fintech-testing
description: >-
  Regulatory sandbox monitoring engine auditing live fintech testing telemetry against statutory sandbox boundaries (FCA UK, MAS Singapore, SEBI India) including client caps, volume caps, AUM caps, and duration limits.
domain: Regulatory & Financial Compliance
subdomain: Regulatory Sandbox & Innovation Governance
tags: ["regulatory-sandbox", "fintech-testing", "fca-sandbox", "mas-sandbox", "sebi-sandbox", "compliance-boundaries"]
brokers_frameworks: ["FCA Regulatory Sandbox", "MAS FinTech Regulatory Sandbox", "SEBI Innovation Sandbox", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when testing new algorithmic trading strategies, innovative financial products, or DLT-based market infrastructure within a regulator-approved sandbox framework (e.g., FCA UK, MAS Singapore, SEBI India). Sandbox programs grant temporary regulatory relief to test live innovations with real clients, but enforce strict boundaries on client count, trading volume, AUM, duration, and exit plans. Exceeding sandbox limits voids regulatory protection and exposes the firm to severe penalties. This engine audits live testing telemetry against statutory sandbox boundaries.

## Prerequisites

- Sandbox program parameters (`program_name`, `max_allowed_clients`, `max_transaction_volume_usd`, `max_aum_usd`, `max_duration_months`).
- Live testing telemetry (`active_clients`, `cumulative_volume_usd`, `current_aum_usd`, `elapsed_months`, `has_exit_plan`).

## Workflow

1. **Telemetry Ingestion & Capacity Calculation**:
   - Calculate capacity utilization percentages for clients, transaction volume, and AUM.
2. **Boundary Breach Checks**:
   - Check if `active_clients` > `max_allowed_clients` (`CLIENT_LIMIT_BREACH`).
   - Check if `cumulative_volume_usd` > `max_transaction_volume_usd` (`VOLUME_CAP_BREACH`).
   - Check if `current_aum_usd` > `max_aum_usd` (`AUM_CAP_BREACH`).
   - Check if `elapsed_months` > `max_duration_months` (`SANDBOX_EXPIRED`).
3. **Exit Plan & Risk Mitigation Audit**:
   - Verify documented testing exit and client protection plan.
4. **Audit Report Generation**: Output structured `SandboxAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unmonitored Client Onboarding**: Exceeding the client count cap during viral growth, breaching sandbox terms.
- **Ignoring Cumulative Volume Caps**: Tracking only active position sizes while exceeding cumulative trading volume limits.
- **Testing Expiry Overrun**: Continuing live trading past the statutory sandbox cohort duration without formal extension.

## Verification

- Instantiate `RegulatorySandboxProgramsForFintechTestingEngine`. Feed FCA sandbox telemetry within limits $\implies$ verify `SANDBOX_COMPLIANT`. Feed telemetry with 600 clients vs 500 cap and $6M volume vs $5M cap $\implies$ verify `SANDBOX_BREACHED` with 2 breaches. Feed elapsed time exceeding max duration $\implies$ verify `SANDBOX_EXPIRED`.
- Run `python scripts/test_regulatory_sandbox_programs_for_fintech_testing.py`.

## Related Skills

- `regulatory-custody-requirements-by-jurisdiction`
- `regulatory-capital-requirement-tracking`
---
