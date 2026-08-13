# Deep Workflow Reference — api-key-least-privilege-audit-tool

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Role Security Policy Definition**:
   - Define role-based permission sets (`MARKET_DATA_ONLY`, `EXECUTION_BOT`, `PORTFOLIO_MONITOR`, `ADMIN_SUPERVISOR`).
   - Flag critical forbidden permissions (`withdraw`, `transfer`, `account_admin` for non-admin roles).
   - `ADMIN_SUPERVISOR` may hold admin scopes but still forbids withdrawal/transfer permissions.

2. **Probe Key Metadata**:
   - Ingest API key granted scopes via broker key details endpoint or config.

3. **Audit Privilege Compliance**:
   - Normalize all granted scopes to lowercase for case-insensitive comparison.
   - Detect wildcard `*` permission — always a critical violation regardless of role.
   - Verify all `required_permissions` are present.
   - Verify zero `forbidden_permissions` or excess scopes are granted.

4. **Security Enforcement & Alarm**:
   - If forbidden permissions or wildcard scope are detected, raise critical alert and block bot execution.
   - If required permissions are missing, raise warning and flag as non-compliant.

## Failure Modes Observed in Production

- **Wildcard Defaults:** Broker "Full Access" or `*` settings granting unrestricted access to automated bots.
- **Unmonitored Sub-Account Keys:** Keys on master accounts inheriting withdrawal rights.
- **Case Mismatches:** Broker returning scopes in different case than policy definitions, causing false-negative audit passes if not normalized.

## Production Implementation Reference

- Reference code: `scripts/key_auditor.py` (`APIKeyLeastPrivilegeAuditor`, `BotRole`, `RoleSecurityPolicy`, `KeyAuditReport`).
- Automated unit tests: `scripts/test_key_auditor.py`.
