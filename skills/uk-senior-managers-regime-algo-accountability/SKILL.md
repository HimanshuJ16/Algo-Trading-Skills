---
name: uk-senior-managers-regime-algo-accountability
description: "Institutional regulatory governance skill for UK FCA Senior Managers & Certification Regime (SM&CR under SUP 10C & FG18/9), mapping SMF functions (SMF24, SMF16, SMF4), developer Certification F&P checks, Reasonable Steps sign-offs, and Management Responsibilities Map (MRM) reports."
domain: Global Regulatory Compliance & Risk Governance
subdomain: Senior Managers Regime & Executive Accountability (UK FCA)
tags:
- smcr
- uk-fca
- smf24
- smf16
- smf4
- fg18-9
- reasonable-steps
- fitness-and-propriety
- algo-governance
brokers_frameworks:
- fca-handbook-sup10c
- fca-fg18-9
- pra-rulebook
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when establishing, managing, or auditing statutory accountability for algorithmic trading systems operating under the **UK FCA Senior Managers and Certification Regime (SM&CR)** (FCA SUP 10C, FG18/9, and MiFID II RTS 6).

This skill provides institutional mechanisms to:
- Map algorithmic trading strategies to designated Senior Management Functions (**SMF24 Chief Operations**, **SMF16 Compliance Oversight**, **SMF4 Chief Risk Officer**).
- Manage the annual **Certification Function** Fitness & Propriety (F&P) register for quantitative developers and algo traders.
- Execute formal pre-production **Deployment Sign-Offs** documenting "Reasonable Steps" taken by the responsible SMF holder.
- Generate **Management Responsibilities Map (MRM)** compliance audit reports for FCA inspection under statutory enforcement duty.

## Prerequisites

- Python 3.9+
- Understanding of FCA Handbook SUP 10C (Senior Managers Regime) and FG18/9 (Algorithmic Trading Compliance).
- Official FCA Individual Reference Numbers (IRNs) for all registered Senior Managers.

## Workflow

1. **Register Senior Management Functions (SMFs)**: Register SMF holders (`SeniorManager`) specifying SMF role (`SMF24_CHIEF_OPERATIONS`, `SMF16_COMPLIANCE_OVERSIGHT`, `SMF4_CHIEF_RISK`), FCA IRN, and contact details.
2. **Certify Quantitative Developers**: Register Certified Function developers (`CertifiedDeveloper`) after annual Fitness & Propriety (F&P) assessment, linking each developer to an accrediting SMF.
3. **Register Algorithmic Strategies**: Map each trading algorithm (`AlgoStrategyRegistration`) to its responsible SMF holder, certified developers, pre-trade risk approvals, kill-switch test records, and RTS 6 stress tests.
4. **Execute Reasonable Steps Sign-Off**: Prior to production deployment, the responsible SMF executes `execute_deployment_sign_off()` providing detailed "Reasonable Steps" audit documentation.
5. **Verify Deployment Readiness & Audit**: Call `verify_algo_deployment_readiness()` and `generate_mrm_report()` to generate immutable regulatory audit logs.

## Common Pitfalls

- **Unassigned Algorithmic Strategies (No Responsible SMF)**: Deploying algorithms without a named SMF holder leaves senior leadership exposed to personal statutory liability under the FCA Duty of Responsibility.
- **Uncertified Developers in Production**: Allowing developers without active Fitness & Propriety (F&P) certification to write or commit production algorithm code violates FCA Certification Function rules.
- **Superficial "Reasonable Steps" Documentation**: Logging vague sign-off notes (e.g. "Approved") fails FCA audit scrutiny. Reasonable steps notes must detail pre-trade collar reviews, stress test results, and kill switch latency verification.
- **Failing to Re-certify Developers Annually**: Certification Function F&P status expires after 12 months. Automatic expiration checks are mandatory.

## Verification

Run the unit test suite to validate SMF registration, developer F&P checks, deployment sign-offs, reasonable steps validation, and MRM report generation:

```bash
python -m unittest discover -s skills/uk-senior-managers-regime-algo-accountability/scripts
```

## Related Skills

- `uk-fca-algorithmic-trading-systems-controls`
- `sec-rule-15c3-5-risk-controls-us`
- `kill-switch-and-drawdown-circuit-breakers`
- `transfer-pricing-considerations-for-multi-entity-trading-operations`

