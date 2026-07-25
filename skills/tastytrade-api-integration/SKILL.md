---
name: tastytrade-api-integration
description: >-
  Use when connecting to Tastytrade (Tastyworks) API for options and futures trading to handle session token auth, option chain OCC ticker resolution, multi-leg complex option order construction, and account position tracking.
domain: algorithmic-trading
subdomain: broker-integration
tags: ["broker-integration", "tastytrade", "tastyworks", "options-trading", "multi-leg-orders", "futures"]
brokers_frameworks: ["Tastytrade API", "Python requests", "tastytrade-sdk"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when building options-focused or futures algorithmic trading strategies on Tastytrade (formerly Tastyworks). Tastytrade specializes in options trading with native support for multi-leg complex option spreads (verticals, iron condors, straddles) and futures derivatives via its REST API.

## Prerequisites

- Tastytrade developer credentials (login email & password).
- Account number for the target trading account.
- Option leg specification (OCC format: e.g. `AAPL  240816C00200000`).

## Workflow

1. **Session Authentication (`/sessions`)**:
   - Issue POST to `/sessions` with username/email and password.
   - Extract `session-token` and store in HTTP headers (`Authorization: {session-token}`).

2. **Retrieve Customer Accounts (`/customers/me/accounts`)**:
   - Ingest account list and identify target `account_number`.

3. **Option Chain Resolution (`/option-chains/{symbol}`)**:
   - Resolve root equity tickers (`AAPL`, `SPY`) to specific expiration dates and OCC option symbol strings.

4. **Multi-Leg Option Order Placement (`/accounts/{account_number}/orders`)**:
   - Construct multi-leg payload specifying `order-type` (`Limit`, `Market`), `time-in-force` (`Day`, `GTC`), net debit/credit `price`, and `legs` array (action: `Buy to Open`, `Sell to Close`, `Sell to Open`, `Buy to Close`).

> Full step-by-step procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Incorrect Net Price Sign**: In Tastytrade API, a credit order requires a positive price, while a debit order requires a positive price specified alongside the `price-effect` field (`Credit` vs `Debit`).
- **OCC Symbol Padding**: Option OCC symbols must follow exact 21-character format standards (6 char root ticker padded with spaces, 6 char YYMMDD, 1 char C/P, 8 char strike * 1000).
- **Session Expiry**: Session tokens expire after 24 hours of inactivity. Must implement periodic heartbeat or re-authentication.

## Verification

- Simulate session authentication and verify `session-token` extraction.
- Construct 2-leg vertical spread and 4-leg iron condor option orders and verify payload structure.
- Run `python scripts/test_tastytrade_client.py` and confirm 100% pass rate.

## Related Skills

- `options-margin-span-calculation-global`
- `broker-agnostic-adapter-interface`
- `paper-to-live-promotion-checklist`
---
