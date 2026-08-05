---
name: singapore-mas-notice-on-cyber-hygiene-for-trading-systems
description: >-
  Production-grade compliance audit engine enforcing the Monetary Authority of Singapore (MAS) Notice on Cyber Hygiene (FSM-N06 / FSM-N22) baseline security controls across trading infrastructure, mandating MFA, admin account restrictions, and 30-day security patch management.
domain: Compliance & Cybersecurity Governance
subdomain: MAS Cyber Hygiene Regulatory Controls
tags: ["mas-cyber-hygiene", "singapore-compliance", "mfa-mandate", "patch-management", "admin-account-security", "trading-infrastructure"]
brokers_frameworks: ["MAS Notice on Cyber Hygiene (FSM-N06 / FSM-N22)", "MAS Technology Risk Management (TRM)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when auditing or hardening trading infrastructure (order routers, market data gateways, trade databases) for financial institutions operating in Singapore under Monetary Authority of Singapore (MAS) regulation. The MAS Notice on Cyber Hygiene sets legally binding baseline cybersecurity requirements: administrative account access restriction, mandatory Multi-Factor Authentication (MFA), 30-day critical patch management, OS/DB baseline hardening, network perimeter defenses, and endpoint anti-malware protection.

## Prerequisites

- Trading system asset payload (`TradingSystemAsset`: `system_id`, `asset_type`, `has_mfa_enabled`, `admin_accounts_restricted`, `critical_patches_applied_within_30d`, `baseline_security_hardened`, `network_perimeter_firewalled`, `anti_malware_active`).

## Workflow

1. **Asset Security Control Inspection**:
   - Evaluate all 6 mandatory MAS baseline requirements:
     - Control 1: Administrative Account Restrictions
     - Control 2: Multi-Factor Authentication (MFA)
     - Control 3: Security Patch Management ($\le 30$ days)
     - Control 4: Baseline Security Hardening (CIS benchmarks)
     - Control 5: Network Perimeter Defenses (Firewalls / DMZ)
     - Control 6: Endpoint Anti-Malware Protection
2. **Compliance Scoring & Remediation Generation**:
   - Compute compliance score % ($\text{Passed Controls} / 6 \times 100\%$).
   - Generate mandatory remediation actions for failed controls.
3. **Execution Output**: Output structured `MASCyberHygieneAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unrestricted Administrative Logins**: Permitting shared or direct root/admin logins on production trading hosts without MFA.
- **Delayed Security Patching**: Allowing unpatched critical vulnerabilities to persist beyond the 30-day MAS SLA without implementing compensating network firewall controls.
- **Unverified Third-Party Trading Engines**: Assuming third-party vendor trading software automatically complies with MAS cyber hygiene without auditing.

## Verification

- Instantiate `SingaporeMASCyberHygieneEngine`. Audit fully compliant asset $\implies$ verify `is_compliant=True` and 100% score. Audit asset missing MFA and missing 30-day patch management $\implies$ verify `is_compliant=False`, 66.67% score, and 2 remediation items returned.
- Run `python scripts/test_singapore_mas_notice_on_cyber_hygiene_for_trading_systems.py`.

## Related Skills

- `phishing-resistant-authentication-for-custody-access`
- `sandbox-credential-leakage-prevention`
---
