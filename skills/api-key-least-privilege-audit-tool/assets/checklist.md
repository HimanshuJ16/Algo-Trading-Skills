# Pre-Flight / Sign-off Checklist — api-key-least-privilege-audit-tool

Use this before considering the skill's implementation complete.

## Scope sourcing

- [ ] **Correct Endpoint:** Confirm the scope set came from a *key-permission* endpoint (Binance `GET /sapi/v1/account/apiRestrictions`, Coinbase `GET /api/v3/brokerage/key_permissions`) and **not** from an account-status endpoint such as `GET /api/v3/account`.
- [ ] **Probed vs Declared:** Confirm the audit record states which form the scope set came from. For Kraken, Zerodha Kite and any other venue without introspection, the audit is *declared* and proves only that the operator record is compliant.
- [ ] **Scope Name Mapping:** Confirm broker-native scope names were mapped onto the canonical policy names, and that unmapped names surface as violations rather than being dropped.
- [ ] **Boolean Flags Filtered by Value:** Confirm a boolean-flag response (`enableWithdrawals`, `can_transfer`) was reduced to the flags that are **true** before mapping — not passed as raw keys, which would audit field names instead of granted scopes.
- [ ] **Policy Satisfiability:** Confirm every role policy has `required ⊆ allowed` and `required ∩ forbidden = ∅`, so no correctly-configured key is reported as a critical violation.

## Policy

- [ ] **Security Policy Definition:** Confirm role policies specify required, allowed, and forbidden scopes.
- [ ] **Deny by Default:** Confirm a scope absent from the role's allowed set is flagged even when not explicitly forbidden.
- [ ] **ADMIN_SUPERVISOR Policy:** Confirm the admin role is defined and still forbids withdrawal/transfer permissions.
- [ ] **Wildcard Detection:** Confirm `*` is always flagged as a critical violation, for every role.
- [ ] **Case-Insensitive Matching:** Confirm all scope comparisons normalize to lowercase.
- [ ] **Forbidden Scope Detection:** Confirm keys with `withdraw_funds` or `transfer` trigger security alarms.

## Report quality

- [ ] **Severity Is Machine-Readable:** Confirm the deployment gate branches on `report.severity` / `report.has_critical_violation`, not on the text of `security_warning`.
- [ ] **Critical vs Insufficient:** Confirm an under-privileged key reports `INSUFFICIENT_PERMISSIONS` and is not escalated to a revocation event.
- [ ] **Empty Set Is Not a Pass:** Confirm an empty granted scope set is non-compliant and the warning names the failed-probe possibility.
- [ ] **Both Conditions Reported:** Confirm a key that is simultaneously over- and under-privileged has both conditions named in the warning.
- [ ] **Deterministic Records:** Confirm `missing_required` and `excess_violations` are sorted, so repeat audits of the same key are byte-identical and diffable.
- [ ] **Malformed Input Rejected:** Confirm a bare `str`/`bytes`, `None`, or a non-string element raises `TypeError` rather than being audited.
- [ ] **Report Immutability:** Confirm `KeyAuditReport` and `RoleSecurityPolicy` are frozen dataclasses.

## Paired controls (not covered by this auditor)

- [ ] **IP Restriction Verified Separately:** Confirm the key is bound to static IPs at the venue. A compliant scope audit is not evidence of this.
- [ ] **Deployed Key Identity:** Confirm the key that was audited is the key actually present in the production environment.

## Testing

- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/api-key-least-privilege-audit-tool/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
