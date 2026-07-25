# Deep Workflow Reference — sandbox-credential-leakage-prevention

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Environment Configuration**:
   - Explicitly declare `TradingEnvironment` (`SANDBOX` vs `PRODUCTION`).

2. **API Key Prefix Verification**:
   - Inspect API key pattern against registered broker key prefixes (e.g. `PK...` vs `AK...`).

3. **Target Gateway URL Boundary Validation**:
   - Inspect target URL against allowed sandbox and production domain rules.

4. **Runtime Security Veto**:
   - Intercept request; if sandbox keys attempt to reach live production gateways or vice versa, raise `SecurityViolationError` and abort request.

## Production Implementation Reference

- Reference code: `scripts/credential_guard.py` (`CredentialEnvironmentGuard`, `TradingEnvironment`, `SecurityViolationError`).
- Automated unit tests: `scripts/test_credential_guard.py`.
