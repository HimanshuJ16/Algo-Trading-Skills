---
name: secrets-rotation-without-bot-downtime
description: >-
  Production-grade zero-downtime secret rotator for live algorithmic trading bots supporting hot-swapping credentials, dual-token overlap validation, emergency fallback, and old credential revocation.
domain: DevSecOps & High-Availability Operations
subdomain: Zero-Downtime Secret Rotation
tags: ["secrets-rotation", "zero-downtime", "hot-swap", "dual-token", "vault", "bot-reliability"]
brokers_frameworks: ["Dual-Token Rotation Pattern", "Secrets Rotator Engine", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when rotating API keys, OAuth secrets, or database credentials for live high-frequency or algorithmic trading bots without restarting the bot process or missing trading opportunities. Restarting trading bots to reload updated environment variables causes execution downtime, lost market data ticks, and unhedged positions. This engine implements the dual-token overlap pattern: validating new credentials before hot-swapping, retaining previous credentials as fallback, and revoking old keys once new credentials are confirmed.

## Prerequisites

- Initial active credential (`key_id`, `secret`).
- Pluggable validation function (`validate_fn`).

## Workflow

1. **New Credential Pre-Validation**:
   - Validate new API key against broker test endpoint before initiating switchover (`RotationState.VALIDATING_NEW`).
2. **Atomic Hot-Swap**:
   - Set new credential as active while retaining previous credential in memory (`RotationState.SWAPPED`).
3. **Emergency Fallback / Rollback**:
   - If post-swap API errors occur, instantly revert active credential back to previous valid credential (`RotationState.FAILED_ROLLBACK`).
4. **Old Credential Revocation**:
   - Revoke and purge old credential once new key is confirmed (`RotationState.REVOKED_OLD`).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Restarting Bots for Key Updates**: Killing bot processes to update environment variables, causing trade execution gaps and missed fills.
- **Revoking Old Key Before New Key Verification**: Deleting the old API key on the broker side before verifying that the bot can authenticate with the new key.
- **Uncached Fallback Credentials**: Discarding previous credentials immediately upon swap, preventing instant rollback during API error spikes.

## Verification

- Instantiate `SecretsRotator`. Rotate to valid key `key_v2` $\implies$ verify `state = RotationState.SWAPPED` and `active_key_id = "key_v2"`. Attempt rotation with invalid key $\implies$ verify `RotationState.FAILED_ROLLBACK` and old key retained active. Trigger `fallback_to_previous()` $\implies$ verify active key reverts to `key_v1`. Revoke previous key $\implies$ verify `RotationState.REVOKED_OLD`.
- Run `python scripts/test_secrets_rotator.py`.

## Related Skills

- `sandbox-credential-leakage-prevention`
- `blue-green-deployment-for-live-strategy-updates`
---
