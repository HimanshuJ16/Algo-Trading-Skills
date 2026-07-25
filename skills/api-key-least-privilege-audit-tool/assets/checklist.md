# Pre-Flight / Sign-off Checklist — api-key-least-privilege-audit-tool

Use this before considering the skill's implementation complete.

- [ ] **Security Policy Definition:** Confirm role policies specify required, allowed, and forbidden scopes.
- [ ] **Key Scope Probing:** Confirm API key scopes are ingested from broker endpoint or config.
- [ ] **Forbidden Scope Detection:** Confirm keys with `withdraw_funds` or `transfer` trigger security alarms.
- [ ] **Insufficient Permission Detection:** Confirm keys missing required execution permissions are flagged.
- [ ] **Automated Testing:** Run `python scripts/test_key_auditor.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
