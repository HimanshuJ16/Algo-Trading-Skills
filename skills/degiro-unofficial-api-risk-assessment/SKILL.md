---
name: degiro-unofficial-api-risk-assessment
description: >-
  Use when assessing automated access to DEGIRO through reverse-engineered Web API
  endpoints. DEGIRO states that API wrappers and custom scripts violate its terms, so
  the contractual finding comes before session handling or the checkOrder flow.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, degiro, european-markets, unofficial-api, risk-assessment, euronext
  brokers_frameworks: "DEGIRO Web API (unofficial); degiro-connector (community reference); Custom Risk Engine"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when evaluating — or, having accepted the consequences, building — automated access to a DEGIRO account for European exchanges (Euronext, Xetra, LSE). DEGIRO publishes no official trading API. **Its own helpdesk states that DEGIRO "does not support the use of external solutions, such as API wrappers or custom scripts, that can interface with your DEGIRO account", and that using third-party automation tools violates its terms of service.** Read that as the primary finding of this skill: the first output of a DEGIRO automation assessment is that the activity is contractually prohibited by the broker, with account restriction or termination as the realistic downside.

If you proceed anyway, this skill covers what an unofficial integration must get right: session and 2FA handling, the two-step `checkOrder` → confirm order flow, cost fields that are frequently absent, and the duplicate-order hazard that follows from a single-use confirmation id.

## When NOT to Use

- **As a green light.** The `RiskEvaluation` score models *operational* risk (lockout, stale session, cost blindness). Contractual risk is not a gradient and no score reduces it. A `LOW` reading does not mean the integration is permitted.
- **For client money or third-party accounts.** Operating someone else's DEGIRO account through prohibited automation compounds a ToS breach with a likely regulatory problem; DEGIRO is flatexDEGIRO Bank AG (BaFin-supervised), whose Dutch branch is registered with DNB and supervised by AFM and DNB.
- **Where an official API exists.** If the strategy can run at a broker with a supported API, use `broker-agnostic-adapter-interface` and route there instead. Nothing in this skill compensates for building on endpoints that can change without notice.
- **As a market-data source.** These endpoints are not a licensed data feed; see `market-data-entitlement-and-licensing-per-venue`.

## Prerequisites

- DEGIRO credentials, plus the TOTP code if 2FA is enabled on the account (pass it as `totp_code` — it routes to a different login endpoint).
- A `http_fn` transport supplied by the caller — required, so timeouts and TLS verification stay under caller control.
- Product IDs resolved to DEGIRO's internal integers via `/product_search/secure/v5/products/lookup`. They are not ISINs or tickers.

## Workflow

1. **Authenticate.** POST to `/login/secure/login`, or to `/login/secure/login/totp` with a `oneTimePassword` field when a TOTP code is supplied. A TOTP code posted to the plain login endpoint is silently ignored and fails on any 2FA-enabled account.
   - If the response omits `intAccount` or `clientInfo.id`, **fail** — fetch them from `/pa/secure/client`. Never default an account identifier: a guessed `intAccount` addresses a different customer's account on every subsequent request.
2. **Evaluate operational risk.** Score login-burst frequency, session presence, and session age. Weights are this skill's heuristics, not published DEGIRO limits — DEGIRO documents no rate limit, lockout threshold, or session TTL, so they are constructor-overridable for calibration against observed behaviour.
3. **Pre-trade dry run.** POST to `/trading/secure/v5/checkOrder;jsessionid=<sid>?intAccount=<acct>&sessionId=<sid>`.
   - Only `confirmationId` is reliably returned. Every cost field — `transactionFee`, `transactionFees[]`, `transactionTaxes[]`, `transactionOppositeFees[]`, and the auto-FX surcharge blocks — is optional and has been observed absent.
   - **Decision point:** if no cost field is present, fees are *unknown*, not zero. Default behaviour (`require_complete_cost_fields=True`) refuses the order rather than letting a bot size on a fabricated 0.00 fee. Opt out only if the strategy tolerates unknown costs.
   - Sum every cost block, not just the scalar `transactionFee` — taxes and FX surcharges arrive separately.
4. **Confirm the order.** POST to `/trading/secure/v5/order/<confirmationId>;jsessionid=<sid>?intAccount=<acct>&sessionId=<sid>`.
   - **Decision point:** the confirmation id is single-use, so this step is deliberately *not* retry-safe. The client marks the id consumed before dispatch; a retry after a timeout raises rather than resubmitting. If the response is lost, the order may already have reached DEGIRO — reconcile against order history instead of resubmitting.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading an absent fee as a zero fee.** `checkOrder` has been observed returning only `confirmationId` and `responseDatetime`. `data.get("transactionFee", 0.0)` turns that into a confident "€0.00 commission" and every position gets sized on costs that were never disclosed.
- **Summing only `transactionFee`.** Taxes (e.g. UK stamp duty, French FTT) and auto-FX surcharges are separate list blocks. The scalar alone understates the true cost of a cross-currency trade.
- **Retrying a confirm after a network timeout.** The POST may have been accepted before the response was lost; resubmitting the same order is how one signal becomes two positions. Reconcile, don't retry.
- **Defaulting `intAccount` when login doesn't return it.** A hard-coded fallback silently points every subsequent request at an account number that isn't yours.
- **Passing a TOTP code to the plain login endpoint.** It is dropped without error; the account then appears to have wrong credentials rather than a missing second factor.
- **Login-retry storms.** Re-authenticating aggressively after failures is the fastest route to an automated security lock on a retail account — the burst counter exists to make that visible before it happens.
- **Treating product IDs as tickers.** DEGIRO product IDs are internal integers; resolve them via product lookup or you will place an order in the wrong instrument.
- **Assuming the schema is stable.** These endpoints are undocumented and change without deprecation notice. Every response field must be treated as optional, which is why field access here is defensive throughout.

## Verification

- Instantiate `DEGIROUnofficialRiskManager` with a mock transport. Log in and verify `sessionId` and `intAccount` extraction; supply `totp_code` and verify the request goes to `/login/secure/login/totp` carrying `oneTimePassword`.
- Return a login response without `intAccount` and verify `DEGIROAuthError` is raised and no session is created.
- Run `check_order_dry_run` against a response containing only `confirmationId` and verify `estimated_fee is None`, `cost_fields_complete is False`, and `is_valid is False` under the default policy.
- Return `transactionFee` plus `transactionFees`/`transactionTaxes`/FX-surcharge blocks and verify the fee is the sum of all of them.
- Call `confirm_order` twice with the same check result and verify the second call raises `DEGIRORiskThresholdBreached`; repeat with a transport that raises mid-dispatch and verify the id is still consumed.
- Run `python -m unittest discover -s skills/degiro-unofficial-api-risk-assessment/scripts`.

## Related Skills

- `robinhood-unofficial-api-integration`
- `broker-api-deprecation-notice-monitoring`
- `order-placement-idempotency`
- `token-lifecycle-live-probing`
