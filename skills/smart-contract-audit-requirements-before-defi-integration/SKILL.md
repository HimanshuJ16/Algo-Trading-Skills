---
name: smart-contract-audit-requirements-before-defi-integration
description: >-
  Production-grade DeFi smart contract audit & governance due diligence engine evaluating independent security audits (Trail of Bits, OpenZeppelin), unaddressed vulnerability findings, timelock delay periods (>= 48h), multisig threshold signers, and TVL bug bounty programs before capital integration.
domain: Crypto Custody & DeFi Risk Governance
subdomain: Smart Contract Audit & Governance Due Diligence
tags: ["smart-contract-audit", "defi-integration", "trail-of-bits", "openzeppelin", "timelock-governance", "multisig-threshold", "bug-bounty"]
brokers_frameworks: ["DeFi Due Diligence Framework", "Immunefi Bug Bounty Standards", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when evaluating third-party DeFi protocols (lending pools, DEX aggregators, yield vaults) before integrating automated trading strategies or allocating institutional capital. Integrating unverified smart contracts exposes funds to catastrophic exploits, flash loan attacks, or rug pulls. This engine evaluates independent audit reports from Tier-1 security firms (Trail of Bits, OpenZeppelin), verifies zero unresolved Critical/High findings, enforces $\ge 48$-hour timelock upgrade delays, requires $\ge 3$-of-$5$ admin multisig signers, and verifies active TVL-proportional bug bounty coverage.

## Prerequisites

- Protocol specification (`DeFiProtocolSpec`: `protocol_name`, `contract_address`, `tvl_usd`, `mainnet_days_active`, `audits`, `has_active_bug_bounty`, `bug_bounty_max_payout_usd`, `admin_timelock_delay_hours`, `admin_multisig_threshold_required`).
- Minimum Tier-1 audits required (default 2).

## Workflow

1. **Tier-1 Audit Verification**:
   - Verify at least 2 independent security audits from top-tier firms (Trail of Bits, OpenZeppelin, Consensys, Spearbit).
2. **Unresolved Findings Audit**:
   - Ensure all Critical and High severity findings are remediated and confirmed by fix verification reports.
3. **Governance & Timelock Inspection**:
   - Enforce $\ge 48$-hour admin upgrade timelock delay and $\ge 3$ required multisig signers.
4. **Mainnet Longevity & Bug Bounty Audit**:
   - Require $\ge 90$ days mainnet activity and active bug bounty ($\ge \$100,000$).
5. **Execution Output**: Output structured `DeFiIntegrationGateReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Relying on Single / Unverified Audits**: Accepting a single audit from an unknown or non-reputable auditor as sufficient proof of security.
- **Ignoring Un-Remediated Audit Findings**: Deploying capital into contracts with published Critical or High audit findings that were never patched.
- **Zero-Timelock Upgradeable Proxies**: Allocating capital to protocols where admins can change contract logic instantly without a 48-hour delay window.

## Verification

- Instantiate `SmartContractAuditRequirementsBeforeDeFiIntegrationEngine`. Evaluate fully approved protocol (Aave V3, 2 Tier-1 audits, 72h timelock, 4-of-7 multisig) $\implies$ verify `is_approved=True` and 100% score. Evaluate protocol with un-remediated findings $\implies$ verify `UNRESOLVED_VULNERABILITIES` violation. Evaluate protocol with 0h timelock and 1-of-2 multisig $\implies$ verify `DANGEROUS_TIMELOCK` and `WEAK_MULTISIG` violations.
- Run `python scripts/test_smart_contract_audit_requirements_before_defi_integration.py`.

## Related Skills

- `smart-contract-approval-scope-minimization`
- `custody-solution-vendor-due-diligence-checklist`
---
