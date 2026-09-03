# Deep Workflow Reference — api-key-least-privilege-audit-tool

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

1. **Role Security Policy Definition**:
   - Define role-based permission sets (`MARKET_DATA_ONLY`, `EXECUTION_BOT`, `PORTFOLIO_MONITOR`, `ADMIN_SUPERVISOR`).
   - Each role carries three sets: `required_permissions`, `allowed_permissions` (a superset of required), and `forbidden_permissions`.
   - The check is **deny-by-default**: a scope absent from `allowed_permissions` is a violation even if it is not named in `forbidden_permissions`. This is what makes unrecognised broker-native scope names (`enableWithdrawals`, `can_transfer`, `Funding`) fail closed instead of passing unnoticed.
   - `ADMIN_SUPERVISOR` may hold admin scopes but still forbids every money-movement scope.
   - Full matrix as implemented: `references/standards.md`.

2. **Establish the Granted Scope Set — probed or declared**:
   - **Probed (strong form)**: read the key's own permissions from the venue.
     - Binance Spot: `GET /sapi/v1/account/apiRestrictions`.
     - Coinbase Advanced Trade: `GET /api/v3/brokerage/key_permissions`.
   - **Declared (weak form)**: for venues with no key-permission introspection endpoint — Kraken and Zerodha Kite among them — take the scope set from an operator-maintained record of how the key was configured. This proves only that the record is compliant, not that the live key is. Record which form was used.
   - Do **not** substitute an account-status endpoint for a key-permission endpoint. Binance `GET /api/v3/account` returns `canTrade` / `canWithdraw` / `canDeposit` describing the *account*, not the key's scopes; Coinbase `GET /api/v3/brokerage/accounts` lists accounts, not permissions. Auditing either reads a payload that cannot contain a violation and produces a false pass.
   - Map broker-native scope names onto the canonical names in the policy matrix before auditing. An unmapped name is not silently dropped — it is reported as a violation, which is the intended failure direction.
   - **Filter on the flag value, not the field name.** Both introspection endpoints return booleans (`{"enableWithdrawals": false, ...}`, `{"can_transfer": false, ...}`), not a list of granted scopes. Passing the response — or its keys — to the auditor audits the *field names*, so every key appears to hold every permission the venue can express. Build the granted set from the flags whose value is true, then map those names.

3. **Audit Privilege Compliance**:
   - Normalize all granted scopes to lowercase and strip whitespace for case-insensitive comparison; blank entries are discarded.
   - Reject malformed input rather than auditing it: a bare `str`/`bytes` is iterable and would otherwise be split into single-character "scopes", producing a confident-looking report about scopes that do not exist.
   - Detect wildcard `*` — always a critical violation regardless of role.
   - Verify all `required_permissions` are present.
   - Verify zero forbidden or otherwise-unallowed scopes are granted.

4. **Security Enforcement & Alarm**:
   - Branch on `KeyAuditReport.severity`, not on the text of `security_warning`:
     - `CRITICAL_VIOLATION` — the key holds scopes it must not have. Block deployment **and revoke the key**; it is dangerous whether or not it is deployed.
     - `INSUFFICIENT_PERMISSIONS` — the key lacks scopes the role needs. Block deployment and re-issue the key. This is not a revocation event; treating it as one sends operators to revoke a key that is merely too weak.
     - `COMPLIANT` — scope policy satisfied. This is not a clean bill of health on its own; see the paired controls below.
   - `report.has_critical_violation` is the convenience predicate for the revoke-now case.
   - A key can be both over- and under-privileged at once. Severity reports `CRITICAL_VIOLATION`, and the warning names both conditions so the remediation is not half-applied.

5. **Record the audit**:
   - `missing_required` and `excess_violations` are sorted, so two audits of the same key produce identical records across processes and can be diffed run-to-run.
   - Store the report alongside the deployment record, together with which form (probed or declared) the scope set came from.

## Paired controls this skill does not cover

A scope audit is necessary, not sufficient. It does not check, and must not be reported as evidence of:

- **IP access restriction.** On Binance, adding IPv4 access restrictions is mandatory to enable withdrawal permission — but that coupling is venue product policy, not a general rule. Verify IP binding separately on every venue.
- **Where the secret is stored and how it rotates** — see `secrets-rotation-without-bot-downtime`.
- **Whether the key is the one actually deployed.** A compliant audit of key A says nothing about key B sitting in the production environment.
- **OAuth token scopes** — a different model, see `token-lifecycle-live-probing`.

## Failure Modes Observed in Production

- **Auditing the wrong endpoint:** reading account status (`/api/v3/account`) instead of key permissions (`/sapi/v1/account/apiRestrictions`) and concluding the key is safe because the response contained no scope list at all.
- **Empty scope set read as "unprivileged":** a failed or partially-authenticated probe returns nothing, and an empty granted set contains no forbidden scope. A gate that only inspects `excess_violations` sees an empty list and proceeds. The auditor guards against this by never reporting an empty set as compliant and by naming the failed-probe possibility in the warning.
- **Wildcard Defaults:** broker "Full Access" or `*` settings granting unrestricted access to automated bots.
- **Unmonitored Sub-Account Keys:** keys on master accounts inheriting withdrawal rights.
- **Case Mismatches:** broker returning scopes in a different case than the policy definitions, causing false-negative audit passes if not normalized.
- **Nondeterministic audit records:** report lists built from set iteration reorder between processes, so re-running an audit produces a spurious diff and genuine drift is lost in the noise.

## Production Implementation Reference

- Reference code: `scripts/key_auditor.py` (`APIKeyLeastPrivilegeAuditor`, `BotRole`, `RoleSecurityPolicy`, `KeyAuditReport`, `SEVERITY_*`).
- Automated unit tests: `scripts/test_key_auditor.py`.
