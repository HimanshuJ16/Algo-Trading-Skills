# Deep Workflow Reference — api-key-least-privilege-audit-tool

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Role Security Policy Definition**:
   - Define role-based permission sets (`MARKET_DATA_ONLY`, `EXECUTION_BOT`, `PORTFOLIO_MONITOR`).
   - Flag critical forbidden permissions (`withdraw`, `transfer`, `account_admin`).

2. **Probe Key Metadata**:
   - Ingest API key granted scopes via broker key details endpoint or config.

3. **Audit Privilege Compliance**:
   - Verify all `required_permissions` are present.
   - Verify zero `forbidden_permissions` or excess scopes are granted.

4. **Security Enforcement & Alarm**:
   - If forbidden permissions are detected, raise critical alert and revoke key.

## Production Implementation Reference

- Reference code: `scripts/key_auditor.py` (`APIKeyLeastPrivilegeAuditor`, `BotRole`, `KeyAuditReport`).
- Automated unit tests: `scripts/test_key_auditor.py`.
