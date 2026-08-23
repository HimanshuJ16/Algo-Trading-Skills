# Deep Workflow Reference — degiro-unofficial-api-risk-assessment

This file holds the full technical procedure referenced by `SKILL.md`.

**Precondition:** DEGIRO states that API wrappers and custom scripts interfacing
with a DEGIRO account violate its terms of service. The procedure below
describes how such an integration must behave to avoid *self-inflicted* damage;
it does not make the integration permitted.

## Full Procedure

1. **Login & token extraction**:
   - With a TOTP code: POST `https://trader.degiro.nl/login/secure/login/totp`
     with `username`, `password`, `oneTimePassword`.
   - Without: POST `https://trader.degiro.nl/login/secure/login`.
   - A `status` of 6 or a `statusText` mentioning TOTP means the account has 2FA
     enabled and the second factor is missing — surface this distinctly from a
     wrong-password failure.
   - Store `sessionId`. If `intAccount` or `clientInfo.id` is missing from the
     response, fetch them from `/pa/secure/client`. Never substitute a default.

2. **Continuous risk evaluation**:
   - Track login attempts inside a burst window, session presence, and session
     age; combine into a composite score in [0.0, 1.0].
   - All weights and thresholds are operational heuristics with no DEGIRO
     source; calibrate them against observed behaviour.

3. **Pre-trade dry run (`checkOrder`)**:
   - POST `/trading/secure/v5/checkOrder;jsessionid=<sid>?intAccount=<acct>&sessionId=<sid>`
     with `buySell`, `orderType`, `price`, `productId`, `quantity`, `timeType`.
   - Extract `confirmationId` — the only field guaranteed present.
   - Sum all cost blocks: scalar `transactionFee` plus the `transactionFees`,
     `transactionTaxes`, `transactionOppositeFees`, and auto-FX surcharge lists.
   - If no cost field is present, the fee is **unknown**. Refuse the order under
     the default policy rather than reporting zero.

4. **Order confirmation**:
   - POST `/trading/secure/v5/order/<confirmationId>;jsessionid=<sid>?intAccount=<acct>&sessionId=<sid>`
     with the same order body.
   - Mark the `confirmationId` consumed **before** dispatch. It is single-use,
     and a retry after a lost response is a duplicate order, not a retry.
   - On any non-200 or missing `orderId`, reconcile against order history before
     considering any resubmission — the order may already have been accepted.

## Production Implementation Reference

- Reference code: `scripts/degiro_client.py` (`DEGIROUnofficialRiskManager`,
  `PreTradeCheckResult`, `OrderConfirmation`, `RiskEvaluation`).
- Automated unit tests: `scripts/test_degiro_client.py`.
