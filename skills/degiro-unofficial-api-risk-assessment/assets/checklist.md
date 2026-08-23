# Pre-Flight / Sign-off Checklist — degiro-unofficial-api-risk-assessment

Use this before considering the skill's implementation complete.

## Contractual

- [ ] **ToS acknowledged in writing:** DEGIRO states that API wrappers and custom scripts violate its terms of service. Has the account holder accepted restriction/termination as a possible outcome?
- [ ] **Own account only:** no third-party or client money is being traded through prohibited automation.
- [ ] **Official-API alternative considered** and documented as rejected.

## Authentication

- [ ] **TOTP routing:** with a TOTP code, the request goes to `/login/secure/login/totp` carrying `oneTimePassword`.
- [ ] **2FA-required is distinguishable** from a wrong-credentials failure.
- [ ] **No defaulted identifiers:** a login response missing `intAccount` or `clientInfo.id` fails rather than substituting a placeholder.
- [ ] **No secrets in errors/logs:** auth failures do not echo the response body.

## Cost handling

- [ ] **Absent fees read as unknown, never zero** — verified against a `checkOrder` response containing only `confirmationId`.
- [ ] **All cost blocks summed:** scalar `transactionFee` plus `transactionFees`, `transactionTaxes`, `transactionOppositeFees`, and auto-FX surcharges.
- [ ] **Total consideration computed**, not read from a nonexistent `total` field.

## Order safety

- [ ] **Two-step flow implemented:** `checkOrder` → confirm at `/trading/secure/v5/order/<confirmationId>`.
- [ ] **Confirmation id is single-use** and marked consumed before dispatch.
- [ ] **No retry on timeout:** the runbook says reconcile against order history instead of resubmitting.
- [ ] **Risk gate blocks** confirmation when the score exceeds `max_acceptable_risk_score` (default 0.70).
- [ ] **Session guard** raises when no active session exists, independent of the risk threshold.

## Validation

- [ ] **Order parameters validated:** positive quantity, positive price for LIMIT, known `buy_sell` and `order_type`, positive integer product id.
- [ ] **Risk weights reviewed:** the burst window, burst threshold, and session-staleness value are unsourced heuristics — calibrated or consciously accepted.
- [ ] **Automated testing:** `python -m unittest discover -s skills/degiro-unofficial-api-risk-assessment/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
