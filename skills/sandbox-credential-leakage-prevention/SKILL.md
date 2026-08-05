---
name: sandbox-credential-leakage-prevention
description: >-
  Production-grade runtime environment guard enforcing strict isolation between paper/sandbox test credentials and live production broker endpoints to prevent credential leakage and accidental live trade execution.
domain: DevSecOps & Security Governance
subdomain: Environment Isolation & Credential Security
tags: ["sandbox-isolation", "credential-leakage", "devsecops", "secret-guard", "broker-endpoints", "alpaca-paper"]
brokers_frameworks: ["Credential Environment Guard", "Python Dataclasses", "DevSecOps Standards"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying automated trading applications that operate in dual modes (paper trading / sandbox vs live production trading). Mixing sandbox credentials with live production broker endpoints or using production API keys against testnet endpoints causes catastrophic live capital losses or paper order rejections. This engine enforces strict runtime validation over API key prefixes and target gateway URLs, throwing `SecurityViolationError` if cross-environment leakage occurs.

## Prerequisites

- Declared trading environment (`SANDBOX` or `PRODUCTION`).
- Broker environment rules (`BrokerEnvironmentRules`: `broker_name`, `sandbox_key_prefixes`, `production_key_prefixes`, `sandbox_url_keywords`, `production_url_keywords`).

## Workflow

1. **Environment Initialization**:
   - Instantiate `CredentialEnvironmentGuard` with target `TradingEnvironment` (`SANDBOX` or `PRODUCTION`).
2. **Request Boundary Inspection**:
   - Inspect broker name, API key string, and target endpoint URL before issuing HTTP requests.
3. **Prefix & URL Pattern Matching**:
   - If in `SANDBOX` mode: verify API key does not start with production prefixes (`AK_`, `LIVE_`) and target URL does not match live gateways (`api.alpaca.markets`).
   - If in `PRODUCTION` mode: verify API key does not start with sandbox prefixes (`PK_`, `PAPER_`) and target URL does not match paper gateways (`paper-api.alpaca.markets`).
4. **Security Exception Enforcement**: Throw `SecurityViolationError` on any breach.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Hardcoding Production API Keys in Test Suites**: Checking in live API keys into GitHub or sandbox unit tests.
- **Shared Environment Config Files**: Using a single `.env` file for both paper testing and production live trading.
- **Bypassing Pre-Request Guard Checks**: Calling HTTP libraries directly without routing through the `CredentialEnvironmentGuard`.

## Verification

- Instantiate `CredentialEnvironmentGuard(SANDBOX)`. Validate paper key to paper URL $\implies$ returns True. Validate paper key to live production URL $\implies$ throws `SecurityViolationError("ENDPOINT LEAK DETECTED")`. Instantiate `CredentialEnvironmentGuard(PRODUCTION)` with paper key $\implies$ throws `SecurityViolationError("CREDENTIAL LEAK DETECTED")`.
- Run `python scripts/test_credential_guard.py`.

## Related Skills

- `robinhood-unofficial-api-integration`
- `binance-futures-testnet-to-mainnet-promotion`
---
