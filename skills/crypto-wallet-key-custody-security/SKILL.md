---
name: crypto-wallet-key-custody-security
description: >-
  Use when a trading bot has any access to crypto private keys, exchange API keys with withdrawal permission, or wallet infrastructure, to bound the damage a compromised bot or leaked credential can cause
domain: algorithmic-trading
subdomain: crypto-custody-security
tags: ["crypto-custody-security"]
brokers_frameworks: []
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any crypto trading system where compromise of the bot's credentials could result in irreversible loss of funds — unlike a traditional equities broker (where a compromised API key typically allows unauthorized trades but not direct fund withdrawal to an arbitrary destination, and trades are generally reversible or insurable through the broker/exchange), a compromised crypto exchange API key with withdrawal permission, or a leaked private key, can result in an instant, irreversible transfer of funds to an attacker-controlled address. This skill treats key/credential custody as a distinct, higher-stakes concern from general API-credential handling.

## Prerequisites

- A clear map of exactly which systems/processes need which permissions (read-only market data, trade-only, or trade-plus-withdrawal) — most trading bot logic never needs withdrawal permission at all
- An exchange or custody solution that supports API key permission scoping and, ideally, withdrawal address whitelisting

## Workflow

1. Never grant withdrawal permission to an API key used by the bot's trading logic — create separate keys scoped to exactly the permissions each process needs (market-data-only keys for signal generation, trade-only keys for order placement), so that a compromise of the trading bot's runtime environment cannot directly move funds out even if the credential leaks.
2. Where the exchange supports withdrawal address whitelisting, enable it and restrict withdrawals to pre-approved addresses set up through a separate, more secure process (ideally requiring manual/human action, not automatable by the bot) — this means even a compromised key with withdrawal permission (if one must exist for some operational reason) cannot redirect funds to an arbitrary attacker address.
3. Split operational balance from total holdings: keep only the capital actively needed for the bot's current trading activity in "hot" (exchange-accessible, bot-reachable) balance, and hold the majority of capital in cold storage (offline private keys, or a custody provider) that requires a separate, deliberately higher-friction process to move funds into the hot balance — this bounds maximum loss from any single compromise to the hot-balance amount, not the portfolio's full value.
4. If the bot or any associated infrastructure ever handles private keys directly (rather than delegating custody entirely to an exchange), treat key storage with the same rigor as the broker credential handling in `headless-broker-auth-patterns` at minimum, and strongly prefer a dedicated secrets-management solution (hardware security module, cloud KMS, or a vault service) over storing keys in plaintext config files or environment variables, even for a "small" operational balance.
5. Require multi-signature approval for any transfer above a defined threshold, structurally separate from the bot's own automated logic — this mirrors the pattern in `kill-switch-and-drawdown-circuit-breakers` of keeping risk-critical decisions in a module the strategy logic cannot unilaterally override, applied here to fund movement rather than trading risk.
6. Monitor wallet/exchange-account balances and any outbound transfer activity via an independent alerting channel (not solely the bot's own logging), so an unauthorized transfer — should one occur despite the above controls — is detected within minutes rather than discovered at the next manual balance check.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Granting a single API key both trading and withdrawal permissions "for convenience," eliminating the protective boundary that scoped permissions would otherwise provide.
- Not enabling withdrawal address whitelisting where the exchange supports it, leaving a compromised withdrawal-capable key free to redirect funds anywhere.
- Keeping the full portfolio balance in hot, bot-accessible storage rather than splitting into operational hot balance and cold storage for the majority of capital.
- Storing private keys or exchange API secrets in plaintext environment variables or config files committed to a repository (even a private one) rather than in a dedicated secrets-management solution.
- Relying solely on the bot's own logs to notice unauthorized transfer activity, rather than an independent monitoring/alerting channel that would catch a compromise even if the bot's own logging were also compromised or disabled by the attacker.

## Verification

- Audit every API key in use and confirm none used by trading-logic processes carry withdrawal permission; confirm any key that does carry withdrawal permission is used only by a separate, more restricted process.
- Confirm withdrawal address whitelisting is enabled where supported, and that a test withdrawal attempt to a non-whitelisted address is rejected by the exchange.
- Confirm hot-wallet/operational balance is bounded to a defined, deliberately limited amount relative to total portfolio value, verified by checking actual balances against the documented policy.
- Confirm an independent alert fires (tested via a controlled test transfer, if the exchange/custody solution supports a sandbox) when an outbound transfer occurs, verified to work even if the bot's primary process is stopped.

## Related Skills

- `headless-broker-auth-patterns`
- `kill-switch-and-drawdown-circuit-breakers`
- `crypto-exchange-api-integration`
