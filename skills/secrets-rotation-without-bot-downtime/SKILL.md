---
name: secrets-rotation-without-bot-downtime
description: >-
  Use when rotating broker API keys, secrets, or tokens on a schedule without requiring
  a full bot restart, ensuring continuous trading coverage during credential transitions.
domain: algorithmic-trading
subdomain: deployment-ops
tags: ["deployment", "secrets-rotation", "credentials", "zero-downtime", "api-keys"]
brokers_frameworks: ["Vault", "AWS Secrets Manager", "Custom Secrets Store"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever broker API keys, OAuth tokens, or secrets need periodic rotation
for security hygiene. Naive rotation (stop bot → update secret → restart bot) creates trading
gaps. This skill implements hot-swap credential rotation where:
- New credentials are validated before the old ones are revoked.
- The bot atomically switches to new credentials without restart.
- Failed rotation falls back to existing credentials with an alert.

## Prerequisites

- Secrets store (Vault, environment, config file) with versioned credential slots.
- Broker API that supports overlapping validity of old and new credentials.
- Health check to validate new credentials before switchover.

## Workflow

1. **Generate New Credentials**: Create new API key/secret pair at broker.
2. **Validate New Credentials**: Test connectivity with new credentials (read-only call).
3. **Hot-Swap**: Atomically update the bot's active credential reference.
4. **Verify Live Traffic**: Confirm orders/fills work with new credentials.
5. **Revoke Old Credentials**: Only after new credentials are confirmed working.
6. **Fallback**: If validation fails, keep old credentials and alert ops team.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Revoking Before Validating**: Deleting old key before confirming new key works.
- **Race Conditions**: In-flight requests using old credentials during switchover.
- **No Fallback**: Failing rotation with no way to revert to old credentials.

## Verification

- Rotate credentials and verify bot continues trading without restart.
- Simulate new credential validation failure and confirm fallback to old credentials.
- Run `python scripts/test_secrets_rotator.py` and confirm 100% pass rate.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `headless-broker-auth-patterns`
- `token-lifecycle-live-probing`
---
