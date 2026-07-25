# Pre-Flight / Sign-off Checklist — sandbox-credential-leakage-prevention

Use this before considering the skill's implementation complete.

- [ ] **Environment Scope Declaration:** Confirm active mode (`SANDBOX` vs `PRODUCTION`) is explicitly configured.
- [ ] **Key Prefix Inspection:** Confirm API key prefix is checked against environment rules.
- [ ] **Gateway URL Boundary Check:** Confirm target domain matches environment mode.
- [ ] **Runtime Execution Veto:** Confirm sandbox keys attempting to reach live gateways raise `SecurityViolationError`.
- [ ] **Automated Testing:** Run `python scripts/test_credential_guard.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
