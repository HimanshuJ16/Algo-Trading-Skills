---
name: sandbox-credential-leakage-prevention
description: Use when building broker adapters to enforce strict runtime isolation
  between sandbox/paper test credentials and live production endpoints, preventing
  test keys from reaching live gateways and blocking accidental real-money order routing.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- sandbox-prevention
- credential-leakage
- environment-guard
- security
- production-safety
brokers_frameworks:
- Credential Security Guard
- Python Security
version: '1.0'
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever initializing broker API adapters or setting up deployment pipelines. Mixing sandbox (paper/testnet) credentials with live production gateway URLs — or vice versa — leads to severe operational hazards: paper test runs accidentally sending real-money orders to exchanges, or live bots crashing due to invalid sandbox keys. This skill enforces key prefix validation, endpoint gateway URL matching, and runtime execution vetoes.

## Prerequisites

- Active trading environment mode (`SANDBOX` or `PRODUCTION`).
- Key prefix and URL pattern rules per broker (e.g. Alpaca Paper `PK...` vs Live `AK...`).

## Workflow

1. **Configure Environment Mode**:
   - Explicitly define `TRADING_ENVIRONMENT` (`SANDBOX` vs `PRODUCTION`).

2. **Validate Key Signature & Prefix**:
   - Inspect API key pattern (e.g. Alpaca paper keys `PK*`, Binance testnet keys).

3. **Verify Target Endpoint URL Boundary**:
   - Confirm target API gateway URL matches environment rules (e.g. `paper-api.alpaca.markets` for `SANDBOX`, `api.alpaca.markets` for `PRODUCTION`).

4. **Runtime Execution Veto**:
   - Intercept outbound HTTP requests. If a sandbox key attempts to reach a live production URL, trip security alarm and abort execution.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Environment Variable Contamination**: Inheriting production `.env` credentials in staging or paper testing containers.
- **Hardcoded Gateway Fallbacks**: Broker SDKs defaulting to live production endpoints when base URL parameters are omitted.
- **Dynamic URL Redirection**: OAuth redirects sending sandbox-authenticated sessions to live production endpoints.

## Verification

- Attempt to dispatch an order with sandbox `PK...` key to live production `api.alpaca.markets` and verify execution veto.
- Confirm valid sandbox key to sandbox URL and live key to live URL pass environment guard.
- Run `python scripts/test_credential_guard.py` and confirm 100% pass rate.

## Related Skills

- `alpaca-paper-live-key-separation`
- `sandbox-vs-production-endpoint-drift`
- `api-key-least-privilege-audit-tool`
---
