---
name: key-rotation-schedule-for-hot-wallet-keys
description: >-
  Cryptographic key lifecycle management engine enforcing a mandatory 90-day key rotation policy, signature/volume triggers, zero-downtime dual-key grace periods, and secure key shredding for trading bot hot wallets.
domain: Crypto Custody Security
subdomain: Hot Wallet Cryptographic Lifecycle & Key Governance
tags: ["key-rotation", "hot-wallet", "crypto-custody", "key-shredding", "api-keys", "grace-period", "kms"]
brokers_frameworks: ["AWS Secrets Manager", "HashiCorp Vault", "ECDSA / Ed25519 Keys", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when managing hot wallet private signing keys (ECDSA secp256k1, Ed25519) and broker API keys for live algorithmic trading bots. Hot wallet keys stored in memory or environment variables face exposure risks from server compromises, insider leaks, or memory dumps. Industry standards mandate a strict **90-day maximum key age**, signature usage limits ($100,000$ transactions), a **24-hour dual-key grace period** during rotation, and immediate cryptographic shredding upon revocation or compromise.

## Prerequisites

- Key metadata payload (`key_id`, `created_timestamp_epoch`, `total_signatures_count`, `total_volume_usd_signed`, `is_compromised`, `current_state`).
- Max Key Age threshold ($90$ days $= 7,776,000$ seconds).

## Workflow

1. **Key Metadata & Age Audit**:
   - Compute key age: $\text{Age}_{\text{days}} = \frac{\text{Current Time} - \text{Created Time}}{86400}$.
   - If $\text{Age}_{\text{days}} \ge 90 \implies$ Flag `ROTATION_REQUIRED_AGE_EXPIRED`.
2. **Usage & Volume Limit Audit**:
   - If `total_signatures_count >= 100,000` $\implies$ Flag `ROTATION_REQUIRED_USAGE_EXPIRED`.
   - If `total_volume_usd_signed >= $10,000,000` $\implies$ Flag `ROTATION_REQUIRED_VOLUME_EXPIRED`.
3. **Emergency Compromise Override**:
   - If `is_compromised == True` $\implies$ Immediately revoke and shred key (`EMERGENCY_SHRED_IMMEDIATE`).
4. **Dual-Key Grace Period Rotation Execution**:
   - Generate Key $N+1$.
   - Transition Key $N$ to `DEPRECATED_GRACE_PERIOD` ($24\text{ hours}$ settling window).
   - After grace period, transition Key $N$ to `REVOKED_SHREDDED`.
5. **Audit Report Generation**: Output structured `KeyRotationReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Unrotated Keys for 1+ Years**: Running trading bot API keys for over 90 days without rotation, exposing historical transactions to breach risks.
- **Immediate Hard Revocation Without Grace Period**: Revoking Key $N$ instantly when Key $N+1$ is issued, causing in-flight pending trades signed by Key $N$ to fail settlement.
- **Neglecting Key Memory Shredding**: Leaving old private key material in RAM or disk logs after revocation instead of zeroizing memory bytes.

## Verification

- Instantiate `HotWalletKeyRotationEngine`. Audit Active 30-Day Key $\implies$ verify `KEY_HEALTHY_ACTIVE`. Audit Expired 95-Day Key $\implies$ verify engine flags `ROTATION_REQUIRED_AGE_EXPIRED` and transitions to `DEPRECATED_GRACE_PERIOD`. Audit Compromised Key $\implies$ verify immediate state transition to `REVOKED_SHREDDED`.
- Run `python scripts/test_key_rotation_schedule_for_hot_wallet_keys.py`.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `recovery-plan-for-lost-or-compromised-keys`
---
