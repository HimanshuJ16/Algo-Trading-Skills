---
name: degiro-unofficial-api-risk-assessment
description: Use when connecting to DEGIRO via reverse-engineered Web API endpoints
  for European retail trading to evaluate ToS compliance, manage session tokens, calculate
  pre-trade order fees, and enforce fallback circuit breakers.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- degiro
- european-markets
- unofficial-api
- risk-assessment
- euronext
brokers_frameworks:
- DEGIRO Web API
- Python requests
- Custom Risk Engine
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever operating automated trading bots targeting European financial exchanges (Euronext Amsterdam/Paris, Xetra, LSE) through DEGIRO. DEGIRO does not maintain a public API SDK — algorithmic integrations utilize reverse-engineered internal web endpoints (`trader.degiro.nl`). This skill provides automated risk scoring, session lifecycle management, order confirmation dry-runs (`checkOrder`), and circuit breaker isolation against account lockout risks.

## Prerequisites

- Active DEGIRO account credentials and 2FA authenticator app.
- Explicit agreement to ToS and legal risk parameters for reverse-engineered API access.

## Workflow

1. **Session Authentication & Token Extraction**:
   - Authenticate via `POST https://trader.degiro.nl/login/secure/login/totp` or standard login.
   - Extract `sessionId` and `JSESSIONID` cookies.
   - Retrieve client information and `intAccount` ID from `/pa/secure/client`.

2. **Evaluate Unofficial API Risk Profile**:
   - Compute `RiskScore` considering 2FA challenge frequency, session expiration, and rate-limiting metrics.

3. **Pre-Trade Order Dry-Run (`checkOrder`)**:
   - Issue POST to `/trading/secure/v5/checkOrder` with product ID, buy/sell action, quantity, price, and order type (`LIMITED` / `MARKET`).
   - Parse estimated fees (`freeCategory`), total value, and transaction validation status before executing.

4. **Order Execution & Circuit Breaker Guard**:
   - If `checkOrder` succeeds and risk score remains within threshold, dispatch to `/trading/secure/v5/order`.

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Unannounced Schema Revisions**: DEGIRO regularly updates Web UI endpoints without deprecation notices.
- **Aggressive Session Invalidation**: Re-logging in too frequently triggers automated security locks on retail accounts.
- **Product ID Discrepancies**: Product IDs in DEGIRO are internal integers, not ISINs or tickers. Must resolve product IDs via `/product_search`.

## Verification

- Simulate session authentication and verify `sessionId` and `intAccount` extraction.
- Perform pre-trade `checkOrder` dry-run and verify fee estimation and validation output.
- Run `python scripts/test_degiro_client.py` and confirm 100% pass rate.

## Related Skills

- `robinhood-unofficial-api-integration`
- `broker-account-margin-call-handling`
- `token-lifecycle-live-probing`
---
