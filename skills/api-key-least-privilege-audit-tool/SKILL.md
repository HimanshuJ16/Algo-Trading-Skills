---
name: api-key-least-privilege-audit-tool
description: Use when auditing broker API keys and credentials to inspect granted
  permission scopes, enforce the principle of least privilege, and flag over-privileged
  keys (e.g., withdrawal rights or admin access) before live deployment.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- api-keys
- security-audit
- least-privilege
- withdrawal-protection
- credential-hygiene
brokers_frameworks:
- Security Audit Engine
- Python Security
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to deploying any live trading bot or data pipeline to verify that API credentials strictly adhere to the principle of least privilege. Granting unnecessary permissions (such as account withdrawal, funds transfer, or administrative management) to an execution or data-ingestion bot creates catastrophic security vulnerabilities. If a key is leaked or compromised, excess privileges enable external theft.

## Prerequisites

- Broker API key metadata or active permissions response.
- Target bot role definition (`READ_ONLY_DATA`, `EXECUTION_BOT`, `PORTFOLIO_AUDITOR`).

## Workflow

1. **Define Role Security Policy**:
   - Establish minimum required permissions per bot role (e.g. `EXECUTION_BOT` requires `["read_data", "place_orders", "cancel_orders"]` and **forbids** `["withdraw_funds", "account_admin", "transfer"]`).

2. **Probe Key Permissions**:
   - Query broker API key details endpoint (e.g., Binance `GET /api/v3/account`, Coinbase `/api/v3/brokerage/accounts`, Zerodha profile).

3. **Audit Excess Privileges**:
   - Compare granted key scopes against allowed role policy.

4. **Flag Violations & Halt Pipeline**:
   - If key possesses forbidden permissions (e.g. `withdraw_funds`), trip security alarm and block bot execution.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Wildcard Permission Defaults**: Using default broker "Full Access" or `*` permission settings during key creation.
- **Ignoring IP Whitelisting**: Treating scope reduction as sufficient while failing to bind keys to specific static IP addresses.
- **Unmonitored Sub-Account Keys**: Overlooking API keys created on master accounts that inherit master withdrawal rights.

## Verification

- Submit over-privileged key metadata (possessing `withdraw_funds` on an execution bot) and verify security violation detection.
- Verify properly scoped execution key passes security policy check.
- Run `python scripts/test_key_auditor.py` and confirm 100% pass rate.

## Related Skills

- `crypto-wallet-key-custody-security`
- `secrets-rotation-without-bot-downtime`
- `token-lifecycle-live-probing`
---
