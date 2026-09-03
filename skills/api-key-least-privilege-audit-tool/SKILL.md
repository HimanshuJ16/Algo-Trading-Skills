---
name: api-key-least-privilege-audit-tool
description: >-
  Use before deploying a live bot, to audit existing broker API keys for scopes they do
  not need. Flags withdrawal, transfer and admin rights that turn a leaked execution key
  into theft.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, api-keys, security-audit, least-privilege, withdrawal-protection, credential-hygiene
  brokers_frameworks: "Security Audit Engine; Python Security"
  version: "1.2.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill prior to deploying any live trading bot or data pipeline to verify that API credentials strictly adhere to the principle of least privilege. Granting unnecessary permissions (such as account withdrawal, funds transfer, or administrative management) to an execution or data-ingestion bot creates catastrophic security vulnerabilities. If a key is leaked or compromised, excess privileges enable external theft.

## When NOT to Use

- **Brokers without granular permission scopes**: If the broker exposes only a single all-or-nothing API key with no scope metadata to audit, this tool cannot perform meaningful analysis. Use network-level isolation (IP allowlisting, VPN) instead.
- **Pre-deployment key creation**: This skill audits existing keys, it does not create or modify broker API key scopes. Use broker-specific key creation tooling.
- **Secrets storage or rotation**: This skill does not handle credential storage, encryption, or rotation. See `secrets-rotation-without-bot-downtime` and `crypto-wallet-key-custody-security`.
- **OAuth or session-based auth**: The auditor checks static API key scopes, not OAuth token scopes or session permissions. OAuth flows require their own scope validation.
- **As an enforcement control**: This is a client-side gate. It cannot revoke, downgrade or constrain a key, and an attacker holding a withdrawal-capable key calls the broker directly without ever running it. It catches misconfiguration before deployment; the broker's own scope selection and IP restriction are what actually constrain the key.

## Prerequisites

- Broker API key metadata or active permissions response, from either the venue's key-permission endpoint (**probed**) or an operator-maintained record of how the key was configured (**declared**). Not every venue offers the former — see `references/standards.md` for which do.
- Target bot role definition (`MARKET_DATA_ONLY`, `EXECUTION_BOT`, `PORTFOLIO_MONITOR`, or `ADMIN_SUPERVISOR`).
- A mapping from the venue's native scope names onto the canonical scope names used by the policy matrix.

## Workflow

1. **Define Role Security Policy**:
   - Establish required, allowed and forbidden permissions per bot role (e.g. `EXECUTION_BOT` requires `["read_market_data", "place_orders", "cancel_orders"]` and **forbids** `["withdraw_funds", "account_admin", "transfer"]`).
   - The check is **deny-by-default**: any scope absent from the role's allowed set is a violation even when it is not explicitly forbidden. This is what makes an unmapped broker-native scope name fail closed.
   - `ADMIN_SUPERVISOR` is the only role permitted administrative scopes (`account_admin`, `api_key_manage`), and still forbids withdrawal/transfer permissions.

2. **Establish the Granted Scope Set** — and read the *key's* permissions, not the *account's*:
   - Binance Spot: `GET /sapi/v1/account/apiRestrictions` (returns `enableWithdrawals`, `enableInternalTransfer`, `permitsUniversalTransfer`, `ipRestrict`, …).
   - Coinbase Advanced Trade: `GET /api/v3/brokerage/key_permissions` (returns `can_view`, `can_trade`, `can_transfer`).
   - Kraken and Zerodha Kite document **no** endpoint returning the calling key's own permissions. Take the scope set from an operator-maintained record instead, and mark the audit as *declared* — it proves the record is compliant, not that the live key is.
   - Map native scope names onto the canonical names before auditing. Both venues above return **boolean flags, not a scope list** — pass only the flags whose value is true. Handing the raw response (or its keys) to the auditor audits the field names, so a key with `enableWithdrawals: false` is flagged as holding withdrawal rights.

3. **Audit Excess Privileges**:
   - Compare granted key scopes against the role policy.
   - All comparisons are case-insensitive; blank entries are discarded.
   - A wildcard `*` permission is always flagged as a critical violation regardless of role — unrestricted access must never be granted to an automated bot.
   - Malformed input is rejected, not audited: a bare string is iterable, and auditing `"read_market_data"` character by character would yield a confident report about scopes that do not exist.

4. **Branch on Severity, Not on Warning Text**:
   - `CRITICAL_VIOLATION` (`report.has_critical_violation`) — the key holds scopes it must not have. Block deployment **and revoke the key**; it is dangerous whether or not it ships.
   - `INSUFFICIENT_PERMISSIONS` — the key lacks scopes the role needs. Block deployment and re-issue. Not a revocation event.
   - `COMPLIANT` — scope policy satisfied. Verify IP binding separately before treating this as a clean bill of health.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Auditing the account instead of the key**: Binance `GET /api/v3/account` returns `canTrade` / `canWithdraw` / `canDeposit`, which describe the *account*, not the API key's granted scopes — Binance does not document them as key-permission flags. Coinbase `GET /api/v3/brokerage/accounts` likewise lists accounts, not permissions. Auditing either reads a payload that cannot contain a violation, so every key passes.
- **Treating an empty scope set as a safe key**: a failed or partially-authenticated probe returns nothing, and an empty set contains no forbidden scope. A gate that inspects only `excess_violations` sees an empty list and deploys. The auditor never reports an empty set as compliant and says so explicitly in the warning.
- **Wildcard Permission Defaults**: Using default broker "Full Access" or `*` permission settings during key creation. The auditor always flags `*` as a critical violation.
- **Ignoring IP Whitelisting**: Treating scope reduction as sufficient while failing to bind keys to specific static IP addresses. Binance makes IPv4 access restriction mandatory to enable withdrawal permission — but that coupling is venue product policy, not a general rule, so verify IP binding on every venue independently.
- **Unmonitored Sub-Account Keys**: Overlooking API keys created on master accounts that inherit master withdrawal rights.
- **Case-Sensitive Scope Mismatch**: Broker APIs may return scopes in varying case (e.g., `Read_Market_Data` vs `read_market_data`). The auditor normalizes all scopes to lowercase before comparison.
- **Admin Keys with Withdrawal Rights**: Even `ADMIN_SUPERVISOR` roles must not possess withdrawal or transfer permissions — admin access is for oversight, not capital movement.
- **String-matching the warning message**: `security_warning` is human-readable prose and its wording changes. Gate on `report.severity` / `report.has_critical_violation`.

## Verification

- Submit over-privileged key metadata (possessing `withdraw_funds` on an execution bot) and verify security violation detection with `severity == "CRITICAL_VIOLATION"`.
- Submit a key with wildcard `*` permission and verify it is flagged as a critical violation for every role.
- Verify a properly scoped execution key passes with `severity == "COMPLIANT"`.
- Verify an under-privileged key reports `INSUFFICIENT_PERMISSIONS` and **not** a critical violation.
- Verify an empty scope set is non-compliant and its warning names the failed-probe possibility.
- Verify an unmapped broker-native scope (`enableWithdrawals`, `can_transfer`) is flagged rather than ignored.
- Verify case-insensitive matching (`Read_Market_Data` is accepted for `read_market_data`).
- Verify a bare string or `None` passed as `granted_permissions` raises `TypeError`.
- Verify report list ordering is identical across `PYTHONHASHSEED` values.
- Run `python -m unittest discover -s skills/api-key-least-privilege-audit-tool/scripts` and confirm 100% pass rate.

## Related Skills

- `crypto-wallet-key-custody-security`
- `secrets-rotation-without-bot-downtime`
- `token-lifecycle-live-probing`
- `sandbox-credential-leakage-prevention`
- `exchange-withdrawal-whitelist-enforcement`
