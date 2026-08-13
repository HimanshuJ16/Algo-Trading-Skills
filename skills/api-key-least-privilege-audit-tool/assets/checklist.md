# Pre-Flight / Sign-off Checklist — api-key-least-privilege-audit-tool

Use this before considering the skill's implementation complete.

- [ ] **Security Policy Definition:** Confirm role policies specify required, allowed, and forbidden scopes.
- [ ] **ADMIN_SUPERVISOR Policy:** Confirm admin role is defined and still forbids withdrawal/transfer permissions.
- [ ] **Key Scope Probing:** Confirm API key scopes are ingested from broker endpoint or config.
- [ ] **Wildcard Detection:** Confirm `*` permission is always flagged as a critical violation.
- [ ] **Case-Insensitive Matching:** Confirm all scope comparisons normalize to lowercase.
- [ ] **Forbidden Scope Detection:** Confirm keys with `withdraw_funds` or `transfer` trigger security alarms.
- [ ] **Insufficient Permission Detection:** Confirm keys missing required execution permissions are flagged.
- [ ] **Report Immutability:** Confirm `KeyAuditReport` and `RoleSecurityPolicy` are frozen dataclasses.
- [ ] **Automated Testing:** Run `python scripts/test_key_auditor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
