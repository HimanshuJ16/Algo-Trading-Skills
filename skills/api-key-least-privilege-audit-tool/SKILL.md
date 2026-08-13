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
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill prior to deploying any live trading bot or data pipeline to verify that API credentials strictly adhere to the principle of least privilege. Granting unnecessary permissions (such as account withdrawal, funds transfer, or administrative management) to an execution or data-ingestion bot creates catastrophic security vulnerabilities. If a key is leaked or compromised, excess privileges enable external theft.

## When NOT to Use

- **Brokers without granular permission scopes**: If the broker exposes only a single all-or-nothing API key with no scope metadata to audit, this tool cannot perform meaningful analysis. Use network-level isolation (IP allowlisting, VPN) instead.
- **Pre-deployment key creation**: This skill audits existing keys, it does not create or modify broker API key scopes. Use broker-specific key creation tooling.
- **Secrets storage or rotation**: This skill does not handle credential storage, encryption, or rotation. See `secrets-rotation-without-bot-downtime` and `crypto-wallet-key-custody-security`.
- **OAuth or session-based auth**: The auditor checks static API key scopes, not OAuth token scopes or session permissions. OAuth flows require their own scope validation.

## Prerequisites

- Broker API key metadata or active permissions response.
- Target bot role definition (`MARKET_DATA_ONLY`, `EXECUTION_BOT`, `PORTFOLIO_MONITOR`, or `ADMIN_SUPERVISOR`).

## Workflow

1. **Define Role Security Policy**:
   - Establish minimum required permissions per bot role (e.g. `EXECUTION_BOT` requires `["read_market_data", "place_orders", "cancel_orders"]` and **forbids** `["withdraw_funds", "account_admin", "transfer"]`).
   - `ADMIN_SUPERVISOR` is the only role permitted administrative scopes (`account_admin`, `api_key_manage`), but still forbids withdrawal/transfer permissions.

2. **Probe Key Permissions**:
   - Query broker API key details endpoint (e.g., Binance `GET /api/v3/account`, Coinbase `/api/v3/brokerage/accounts`, Zerodha profile).

3. **Audit Excess Privileges**:
   - Compare granted key scopes against allowed role policy.
   - All comparisons are case-insensitive.
   - A wildcard `*` permission is always flagged as a critical violation regardless of role — unrestricted access must never be granted to an automated bot.

4. **Flag Violations & Halt Pipeline**:
   - If key possesses forbidden permissions (e.g. `withdraw_funds`) or a wildcard scope, trip security alarm and block bot execution.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Wildcard Permission Defaults**: Using default broker "Full Access" or `*` permission settings during key creation. The auditor always flags `*` as a critical violation.
- **Ignoring IP Whitelisting**: Treating scope reduction as sufficient while failing to bind keys to specific static IP addresses.
- **Unmonitored Sub-Account Keys**: Overlooking API keys created on master accounts that inherit master withdrawal rights.
- **Case-Sensitive Scope Mismatch**: Broker APIs may return scopes in varying case (e.g., `Read_Market_Data` vs `read_market_data`). The auditor normalizes all scopes to lowercase before comparison.
- **Admin Keys with Withdrawal Rights**: Even `ADMIN_SUPERVISOR` roles must not possess withdrawal or transfer permissions — admin access is for oversight, not capital movement.

## Verification

- Submit over-privileged key metadata (possessing `withdraw_funds` on an execution bot) and verify security violation detection.
- Submit a key with wildcard `*` permission and verify it is flagged as a critical violation.
- Verify properly scoped execution key passes security policy check.
- Verify case-insensitive matching (`Read_Market_Data` is accepted for `read_market_data`).
- Run `python scripts/test_key_auditor.py` and confirm 100% pass rate.

## Related Skills

- `crypto-wallet-key-custody-security`
- `secrets-rotation-without-bot-downtime`
- `token-lifecycle-live-probing`
---
