---
name: japan-fsa-high-speed-trading-registration
description: >-
  Regulatory compliance engine for Japan Financial Services Agency (FSA) and Financial Instruments and Exchange Act (FIEA), auditing High-Speed Trader (HST) registration, co-location access, pre-trade risk controls, and kill switches.
domain: Regulatory Compliance Global
subdomain: Japanese Market Regulation & FSA HST Governance
tags: ["japan-fsa", "fiea", "high-speed-trading", "hst-registration", "tse", "co-location", "pre-trade-risk", "kill-switch"]
brokers_frameworks: ["Japan FSA FIEA Guidelines", "TSE / OSE Exchange Rules", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying automated high-frequency trading (HFT) strategies, market making algorithms, or co-located trading systems on Japanese exchanges (Tokyo Stock Exchange TSE, Osaka Exchange OSE). Under the amended **Financial Instruments and Exchange Act (FIEA Article 2, Para 42)**, any trading entity operating automated low-latency co-located orders MUST register as a **High-Speed Trader (HST)** with the Japan Financial Services Agency (FSA / Kanto Local Finance Bureau). Unregistered HST trading is illegal and results in immediate account termination.

## Prerequisites

- Entity & Order payload (`trader_id`, `fsa_hst_reg_id`, `is_registered_with_fsa`, `is_algo_automated`, `is_colocated`, `latency_ms`, `order_value_jpy`, `has_kill_switch_enabled`, `has_resident_compliance_manager`).
- Pre-trade order value limit (e.g. $\text{JPY } 100,000,000$).

## Workflow

1. **FIEA HST Criteria Audit**:
   - Evaluate `is_algo_automated == True`, `is_colocated == True`, and `latency_ms <= 20`. If met, entity is classified as a **High-Speed Trader**.
2. **Japan FSA HST Registration Audit**:
   - Audit `is_registered_with_fsa == True` and valid `fsa_hst_reg_id` (e.g., `"Kanto_KLFB_No_3120"`). If unregistered $\implies$ Trigger `REJECTED_UNREGISTERED_HST`.
3. **Pre-Trade Risk Control & Kill Switch Audit**:
   - Audit `has_kill_switch_enabled == True`.
   - Audit `order_value_jpy <= max_order_value_limit_jpy`.
   - Audit `has_resident_compliance_manager == True`.
4. **Audit Report Generation**: Output structured `JapanFsaHstReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Operating Co-Located Algorithms Without FSA HST Registration**: Sending co-located automated orders to TSE/OSE without obtaining a Japan FSA High-Speed Trader registration number, violating FIEA laws.
- **Lacking Automated Kill Switches**: Deploying HST algorithms without software/hardware kill switch controls, failing FSA operational risk audits.
- **Failing to Appoint Resident Compliance Representation**: Operating foreign HFT entities on Japanese exchanges without appointing a resident compliance officer or legal agent in Japan.

## Verification

- Instantiate `JapanFsaHstComplianceEngine`. Audit Registered HST Operator (`fsa_reg_id="KLFB_3120"`, `is_registered=True`, `is_colocated=True`, `latency=2ms`, `kill_switch=True`) $\implies$ verify `FSA_HST_APPROVED`. Audit Unregistered Co-Located HFT (`is_registered=False`) $\implies$ verify `REJECTED_UNREGISTERED_HST`. Audit Missing Kill Switch $\implies$ verify `REJECTED_MISSING_KILL_SWITCH`.
- Run `python scripts/test_japan_fsa_high_speed_trading_registration.py`.

## Related Skills

- `hong-kong-sfc-algorithmic-trading-guidelines`
- `execution-algorithm-kill-switch-integration`
---
