---
name: centralized-secrets-management-vault-integration
description: Secure, centralized API key management using HashiCorp Vault. Enforces
  AppRole authentication, environment isolation, and secret caching for trading bots.
domain: Infrastructure
subdomain: Security
tags:
- vault
- hashicorp
- secrets
- api-keys
- approle
- security
brokers_frameworks:
- HashiCorp Vault
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to completely eliminate hardcoded API keys, environment variables (.env files), or unencrypted config files in your trading infrastructure. A centralized Vault handles the storage, encryption, access control, and auditing of all sensitive data (Exchange API keys, Database passwords, Cloud tokens).

## Prerequisites

- A running instance of HashiCorp Vault (or a compatible KMS like AWS Secrets Manager).
- Trading bots must be configured with an `AppRole` Role ID and Secret ID.
- The `hvac` Python library (or standard `requests` if wrapping the API directly).

## Workflow

1. **Vault Configuration**: The security team provisions an `AppRole` for a specific bot (e.g., `binance-market-maker-bot`). The policy restricts access strictly to the `secret/data/prod/binance/market-maker` path.
2. **Bot Initialization**: The trading bot boots and uses its `role_id` and `secret_id` to authenticate with Vault, receiving a temporary client token.
3. **Secret Retrieval**: The bot requests the required API keys using the token.
4. **Caching**: The bot caches the keys in protected memory (RAM only) so it does not hammer the Vault API on every single network request.
5. **Execution**: The bot injects the keys directly into the Exchange API client (e.g., CCXT or proprietary FIX engine).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Root Tokens**: Passing the Vault Root Token into a trading bot. If the bot is compromised, the attacker has full control over all firm secrets.
- **Leaking Secrets in Logs**: Accidentally printing `print(exchange.config)` or logging exceptions that contain the raw API key.
- **Hammering Vault**: Requesting the API key from Vault for *every single order*. Vault will rate-limit you or crash. Retrieve the key once on boot and cache it in memory.

## Verification

- Simulate the `VaultSecretsManager`. Initialize it using AppRole mock credentials, retrieve a secret, and ensure an exception is thrown if the bot tries to access a path outside its allowed environment.
- Run `python scripts/test_vault_secrets_manager.py`.

## Related Skills

- `api-key-least-privilege-audit-tool`
- `secrets-rotation-without-bot-downtime`
