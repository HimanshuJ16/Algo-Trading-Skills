---
name: employee-offboarding-procedure-for-custody-access
description: Quantitative crypto custody operational risk engine for executing mandatory
  5-step employee offboarding procedures (SSO revocation, exchange API key destruction,
  MPC key shard rotation, hardware token wiping).
domain: Crypto Custody & Security
subdomain: Key Management & Operational Risk
tags:
- offboarding-procedure
- custody-security
- key-rotation
- mpc-custody
- multi-sig
- api-key-revocation
- soc-2
brokers_frameworks:
- Fireblocks API
- Anchorage Custody
- Python Dataclasses
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in crypto quantitative funds, digital asset custodians, and exchange trading infrastructure. When an employee holding crypto custody access (key custodian, DevOps engineer, quantitative trader) leaves the firm, their access must be irrevocably revoked across identity providers, exchange API keys, and custody portals. Crucially, if the employee holds a multi-sig key or MPC shard, the wallet $M$-of-$N$ threshold policy MUST be reconfigured and keys rotated within $24\text{ hours}$.

## Prerequisites

- Employee details (`employee_id`, `role` e.g. `'KEY_CUSTODIAN'`, `'QUANT_DEV'`, `termination_timestamp_utc`).
- Wallet/custody access inventory (`held_mpc_shards`: True/False, `assigned_exchange_api_keys`: List[str]).

## Workflow

1. **Access Revocation Ingestion**:
   - Execute Step 1: `IDP_SSO_REVOKED` (Okta/Google Workspace SSO & VPN).
   - Execute Step 2: `EXCHANGE_API_KEYS_REVOKED` (Cancel API keys created by employee).
   - Execute Step 3: `CUSTODY_PORTAL_REVOKED` (Revoke Fireblocks/Anchorage portal access).
2. **Multi-Sig / MPC Key Rotation**:
   - Execute Step 4: `MULTISIG_MPC_KEY_ROTATED` (Rotate private key / MPC shard to new $M$-of-$N$ address).
3. **Hardware Token Sanitization**:
   - Execute Step 5: `HARDWARE_TOKEN_WIPED` (Wipe YubiKeys and hardware wallets).
4. **Compliance & Risk Audit**:
   - Calculate completion score ($0\%$ to $100\%$).
   - If key rotation is pending $> 24\text{ hours} \implies$ Flag `CRITICAL_KEY_EXPOSURE_RISK`.
5. **Audit Report Generation**: Output structured `CustodyOffboardingAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Leaving Orphaned Exchange API Keys Active**: Revoking employee SSO but leaving standalone exchange API keys (Binance/Coinbase) active in trading bots.
- **Delaying Multi-Sig Key Rotation**: Revoking user logins but postponing multi-sig key rotation, leaving the departing employee with a valid signing key shard.
- **Incomplete Audit Trails**: Failing to log timestamps of each offboarding step for SOC 2 and institutional custodian audits.

## Verification

- Instantiate `CustodyOffboardingEngine`. Submit offboarding request for key custodian holding Fireblocks MPC shard. Complete all 5 steps (`SSO`, `API`, `CUSTODY_PORTAL`, `MPC_ROTATED`, `HSM_WIPED`). Verify engine calculates 100% completion score and issues `OFFBOARDING_COMPLIANT`. Submit partial request with un-rotated MPC key $> 24\text{ hours}$. Verify engine flags `CRITICAL_KEY_EXPOSURE_RISK`.
- Run `python scripts/test_employee_offboarding_procedure_for_custody_access.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `key-rotation-schedule-for-hot-wallet-keys`
---
