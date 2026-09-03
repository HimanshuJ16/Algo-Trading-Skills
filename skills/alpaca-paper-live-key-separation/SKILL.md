---
name: alpaca-paper-live-key-separation
description: Use when connecting a bot to Alpaca Trading API to strictly segregate
  paper and live credentials, enforce base URL endpoint matching, validate account
  status and order-blocking flags, and prevent accidental live capital loss
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
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot connects to the Alpaca Trading API (or any broker with distinct paper/live environments). Connecting a paper strategy to Alpaca's live endpoint (`https://api.alpaca.markets`) using live API keys — or passing live order signals into a paper endpoint — is a catastrophic operational error. Pinning the base URL per environment, inspecting the key prefix, probing `/v2/account` for tradability, and requiring an explicit `ALLOW_LIVE_TRADING` environment variable are all mandatory before any order is submitted.

**The base URL is the control that actually separates the environments.** Alpaca serves paper accounts from `https://paper-api.alpaca.markets` and live accounts from `https://api.alpaca.markets`; a live account is not reachable through the paper host. Every other check in this skill is defence-in-depth layered on that pin — treat them as corroboration, never as a substitute.

## When NOT to Use

- **Brokers without separate paper/live endpoints**: If a single API base URL serves both environments and differentiation is done purely via credentials, this skill's URL-matching logic does not apply. Use broker-specific auth patterns instead (see `headless-broker-auth-patterns`).
- **Backtesting or simulation engines**: When running historical replays or simulated fills with no network calls to a real broker API, environment segregation is irrelevant.
- **Non-Alpaca brokers with different credential schemes**: The `PK...`/`AK...` prefix convention is Alpaca-specific. Brokers using different key formats (e.g., IBKR account IDs `DU...`/`U...`) require their own validation logic. For a broker-agnostic host allow-list, see `sandbox-credential-leakage-prevention`.
- **Read-only market data access**: If the integration only consumes market data endpoints (not order routing), live capital loss is not a risk and the order guard is unnecessary.

## Prerequisites

- Distinct environment variable names for paper vs live credentials (e.g. `ALPACA_PAPER_KEY_ID` vs `ALPACA_LIVE_KEY_ID`).
- Base URL configuration (`https://paper-api.alpaca.markets` for paper vs `https://api.alpaca.markets` for live).
- Explicit `ALLOW_LIVE_TRADING=true` environment flag for live execution mode.

## Workflow

1. **Load & Normalise the Environment Mode**:
   - Coerce the configured environment into a known `PAPER`/`LIVE` value before any comparison. A value matching neither must raise, never fall through — an unrecognised mode that skips both branches is an authorisation, not a no-op.

2. **Inspect Credential Prefixes**:
   - `PK...` indicates Alpaca paper credentials; `AK...` indicates live credentials.
   - Use this to *reject* a credential carrying the opposite environment's prefix. Do **not** require a positive prefix match: the convention is widely observed but is not documented by Alpaca, so an unrecognised key format must not be treated as proof of anything (see `references/standards.md`).

3. **Base URL & Mode Verification**:
   - Match configuration mode against endpoint URLs:
     - `PAPER` mode → `https://paper-api.alpaca.markets`
     - `LIVE` mode → `https://api.alpaca.markets`
   - Match the *exact* URL against an allow-list, case-normalised. Anything else — including a look-alike host such as `https://api.alpaca.markets.attacker.example` — is rejected. Never use a substring or `startswith` test here.

4. **Live Execution Safety Gate (`ALLOW_LIVE_TRADING`)**:
   - Block initialization in `LIVE` mode unless `ALLOW_LIVE_TRADING=true` is explicitly set. Strip the value before comparing — a trailing newline from a `.env` loader should not silently block a legitimate live deployment.
   - Emit a WARNING when live trading is authorised, so the transition is visible in the log record.

5. **Account API Probe**:
   - Issue GET `/v2/account` on startup and reject a response that is not a mapping.
   - Verify the account is tradable: `status` must be `ACTIVE` (or `PAPER_ONLY`, which is valid **only** in paper mode). A *missing* `status` is a veto, not an assumed `ACTIVE`.
   - Veto if any order-blocking flag is set: `trading_blocked`, `account_blocked`, or `trade_suspended_by_user`. Alpaca documents the first and last as "the account is not allowed to place orders" — a guard that ignores them authorises orders the broker will reject.
   - **Resolve the environment only from signals that actually exist.** GET `/v2/account` does **not** return an `is_paper` field (verified against Alpaca's account schema and the official `alpaca-py` `TradeAccount` model). Use, in order: an `is_paper` bool if your SDK wrapper injects one; `status == "PAPER_ONLY"`; an `account_number` beginning `PA` (observed, unofficial — treat as a positive *paper* signal only, never as proof an account is live).
   - If none of those resolve, the environment is **undeterminable** — log it and fall back on the already-verified base URL. Do not treat "undeterminable" as live: that vetoes every legitimate paper deployment, because the real API never supplies the field.

6. **Order Submission Veto Guard**:
   - Wrap order routing calls in `AlpacaEnvironmentManager.guard_order()`, vetoing any outbound order if environment checks fail or key/URL mismatches occur.
   - Validate the order itself at the gate — reject a non-positive, non-finite, or non-numeric `qty`, an empty `symbol`, and any `side` outside `buy`/`sell`.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hardcoding Base URLs**: Hardcoding `https://api.alpaca.markets` in code and relying only on switching API keys in `.env`.
- **Shared Credential Variable Names**: Using generic `ALPACA_KEY_ID` for both paper and live testing, leading to accidental live deployment.
- **Trusting a nonexistent `is_paper` field**: Alpaca's `/v2/account` response has no `is_paper` key. Code written against it reads `None` on every call — so a guard that treats a missing value as live bricks paper trading, and one that treats it as paper waves live accounts through. Resolve the environment from fields the API really returns, and treat "unknown" as unknown.
- **Letting an unrecognised environment fall through**: `if mode == PAPER … elif mode == LIVE …` with no `else` returns success for any third value, so a typo'd or foreign enum member authorises a live order with no `ALLOW_LIVE_TRADING` check. Normalise the mode up front and raise on anything unknown.
- **Defaulting a missing `status` to `ACTIVE`**: A truncated or error-shaped account payload then reads as a healthy account. An unreadable account is a veto.
- **Ignoring the blocked flags**: `trading_blocked` / `account_blocked` / `trade_suspended_by_user` mean the broker will refuse the order. Catching that locally turns a confusing broker rejection into a clear pre-trade veto.
- **Strict `is True` on a loosely-typed payload**: An adapter that hands back `"true"`/`"false"` as strings defeats `if account_data.get("trading_blocked") is True`, so an explicitly blocked account reads as unblocked. Accept the string forms and fail closed on a value you cannot interpret.
- **Substring URL matching**: `base_url.startswith("https://api.alpaca.markets")` also accepts `https://api.alpaca.markets.attacker.example`. Compare against an exact allow-list.
- **Missing Live Confirmation Flag**: Allowing live trading without an explicit boolean environment variable guard (`ALLOW_LIVE_TRADING=true`).

## Verification

- Configure paper keys with the live URL and confirm `AlpacaEnvironmentManager` raises `EnvironmentMismatchError`.
- Attempt live mode initialization without `ALLOW_LIVE_TRADING=true` and confirm execution is blocked.
- Construct a config with an unrecognised environment value and confirm it raises rather than validating.
- Simulate an account probe returning `is_paper=False` when configured in paper mode and confirm the startup veto.
- Simulate a realistic paper payload with **no** `is_paper` field (`status=ACTIVE`, `account_number=PA…`) and confirm it is accepted, not vetoed.
- Simulate `trading_blocked=true` and confirm the order guard vetoes.
- Simulate a probe response with no `status` field and confirm the veto.
- Submit `qty=0`, `qty=NaN`, and `side="long"` through `guard_order()` and confirm each is rejected.
- Run the unit test suite `python -m unittest discover -s skills/alpaca-paper-live-key-separation/scripts` and confirm a 100% pass rate.

## Related Skills

- `paper-to-live-promotion-checklist`
- `headless-broker-auth-patterns`
- `kill-switch-and-drawdown-circuit-breakers`
- `sandbox-credential-leakage-prevention`
- `sandbox-vs-production-endpoint-drift`
