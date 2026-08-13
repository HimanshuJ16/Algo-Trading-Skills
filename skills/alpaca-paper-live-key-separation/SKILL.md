---
name: alpaca-paper-live-key-separation
description: Use when connecting a bot to Alpaca Trading API to strictly segregate
  paper and live credentials, enforce base URL endpoint matching, validate account
  environment flags, and prevent accidental live capital loss
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- alpaca-api
- paper-trading
- live-capital-guard
- credential-security
brokers_frameworks:
- Alpaca Trading API v2
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot connects to the Alpaca Trading API (or any broker with distinct paper/live environments). Connecting a paper strategy to Alpaca's live endpoint (`https://api.alpaca.markets`) using live API keys — or passing live order signals into a paper endpoint — is a catastrophic operational error. Implementing explicit key prefix inspection, base URL verification, account `is_paper` response probing, and an explicit `ALLOW_LIVE_TRADING` environment variable guard is mandatory before any order is submitted.

## When NOT to Use

- **Brokers without separate paper/live endpoints**: If a single API base URL serves both environments and differentiation is done purely via credentials, this skill's URL-matching logic does not apply. Use broker-specific auth patterns instead (see `headless-broker-auth-patterns`).
- **Backtesting or simulation engines**: When running historical replays or simulated fills with no network calls to a real broker API, environment segregation is irrelevant.
- **Non-Alpaca brokers with different credential schemes**: The `PK...`/`AK...` prefix convention is Alpaca-specific. Brokers using different key formats (e.g., IBKR account IDs `DU...`/`U...`) require their own validation logic.
- **Read-only market data access**: If the integration only consumes market data endpoints (not order routing), live capital loss is not a risk and the order guard is unnecessary.

## Prerequisites

- Distinct environment variable names for paper vs live credentials (e.g. `ALPACA_PAPER_KEY_ID` vs `ALPACA_LIVE_KEY_ID`).
- Base URL configuration (`https://paper-api.alpaca.markets` for paper vs `https://api.alpaca.markets` for live).
- Explicit `ALLOW_LIVE_TRADING=true` environment flag for live execution mode.

## Workflow

1. **Load & Inspect Credential Prefixes**:
   - Inspect API Key ID prefix: `PK...` indicates Alpaca paper credentials; `AK...` indicates live credentials.
   - Enforce that paper credentials can NEVER be passed to the live base URL endpoint.

2. **Base URL & Mode Verification**:
   - Match configuration mode against endpoint URLs:
     - `PAPER` mode $\rightarrow$ `https://paper-api.alpaca.markets`
     - `LIVE` mode $\rightarrow$ `https://api.alpaca.markets`
   - Both modes positively verify the base URL matches the expected endpoint — any other URL is rejected.

3. **Live Execution Safety Gate (`ALLOW_LIVE_TRADING`)**:
   - Check if mode is `LIVE`. Block initialization unless environment variable `ALLOW_LIVE_TRADING=true` is explicitly set.

4. **Account API Probe**:
   - Issue GET `/v2/account` probe call on startup.
   - Verify that the account `status` is `ACTIVE` and that `is_paper` matches the configured environment mode.
   - **Fail-safe**: if the `is_paper` field is missing from the API response, the probe treats the account as live (`is_paper=False`) rather than defaulting to paper.

5. **Order Submission Veto Guard**:
   - Wrap order routing calls in `AlpacaEnvironmentManager.guard_order()`, vetoing any outbound order if environment checks fail or key/URL mismatches occur.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hardcoding Base URLs**: Hardcoding `https://api.alpaca.markets` in code and relying only on switching API keys in `.env`.
- **Shared Credential Variable Names**: Using generic `ALPACA_KEY_ID` for both paper and live testing, leading to accidental live deployment.
- **Skipping Account Endpoint Probing**: Trusting environment variables without probing the `/v2/account` response to verify `is_paper` status.
- **Missing Live Confirmation Flag**: Allowing live trading without an explicit boolean environment variable guard (`ALLOW_LIVE_TRADING=true`).
- **Defaulting is_paper to True**: Assuming a missing `is_paper` field means paper — a live account can silently pass paper-mode checks. Always fail-safe to live.

## Verification

- Configure paper keys with live URL and confirm `AlpacaEnvironmentManager` raises `EnvironmentMismatchError`.
- Attempt live mode initialization without `ALLOW_LIVE_TRADING=true` and confirm execution is blocked.
- Simulate account probe returning `is_paper=False` when configured in paper mode and confirm startup veto.
- Simulate account probe with `is_paper` field missing and confirm it is treated as live (vetoed in paper mode).
- Run unit test suite `python scripts/test_alpaca_env_guard.py` and confirm 100% pass rate.

## Related Skills

- `paper-to-live-promotion-checklist`
- `headless-broker-auth-patterns`
- `kill-switch-and-drawdown-circuit-breakers`
---
