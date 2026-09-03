---
name: key-rotation-schedule-for-hot-wallet-keys
description: >-
  Use when an online signing key or exchange API credential needs a defensible answer to
  whether it should still be in service, from age, signature count and signed volume,
  and when the old one may be destroyed.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: crypto-custody-security
  tags: key-rotation, hot-wallet, crypto-custody, cryptoperiod, key-shredding, grace-period, fund-sweep, api-keys
  brokers_frameworks: "NIST SP 800-57 Part 1 Rev. 5 (cryptoperiods); CCSS v9.0 (CryptoCurrency Security Standard); AWS Secrets Manager / AWS Config ACCESS_KEYS_ROTATED; HashiCorp Vault; ECDSA secp256k1 / Ed25519 signing keys; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading bot holds an online signing credential — a blockchain hot wallet private key, or an exchange/broker API key — and you need a defensible answer to "should this key still be in service, and is it safe to destroy the old one yet?" The engine turns key metadata into a lifecycle verdict: healthy, rotation initiated, draining in a grace period, blocked pending a fund sweep, or terminally revoked.

It is a **stateless, point-in-time policy evaluator**. It decides what should happen and advances a state field. It never generates a key, holds key material, signs, calls a KMS, sweeps funds, revokes a credential, or zeroizes memory.

Two misconceptions it exists to break:

- **That the thresholds are mandatory.** No standard requires 90-day hot wallet key rotation, a 100,000-signature ceiling, or a $10M volume ceiling. NIST SP 800-57 recommends **one to three years** for a private signature key and calls its own figures "rough order-of-magnitude guidelines"; CCSS v9.0 sets no interval at all. These are conservative engineering defaults, and all are configurable.
- **That "revoking" a blockchain key is a thing.** It is not. A secp256k1 or Ed25519 private key controls its address permanently and no authority can disable it. Rotation means generating a new address and **moving the balance**. Marking such a key `REVOKED_SHREDDED` while value remains either strands those funds or leaves an attacker in control of an address the audit trail records as dead.

## When NOT to Use

- **As evidence that rotation happened.** Every report is an instruction to a runbook. Nothing here verifies a sweep confirmed, a venue revoked a credential, or memory was zeroized. `residual_balance_usd` and `current_state` are asserted by the caller.
- **For cold storage or deep vault keys.** An offline key has a different threat model and a much longer defensible cryptoperiod; frequent rotation of an air-gapped key adds handling risk without reducing exposure. See `air-gapped-signing-workflow-for-cold-storage` and `cold-storage-geographic-distribution-strategy`.
- **For multi-signature or MPC key shares.** Rotating one share of an *m-of-n* quorum is a resharing protocol, not a key replacement, and this engine does not model quorum invariants. See `multi-party-computation-mpc-custody-solutions` and `shamir-secret-sharing-for-key-backup`.
- **As an incident response plan.** Setting `is_compromised` produces a containment instruction, not a forensic process, an exchange freeze, or a disclosure decision. See `post-incident-forensics-for-suspected-key-compromise` and `recovery-plan-for-lost-or-compromised-keys`.
- **As a substitute for scope limits.** Rotating a withdrawal-enabled API key on a schedule does far less good than not granting withdrawal permission. See `api-key-least-privilege-audit-tool`.

## Prerequisites

- A `HotWalletKeyMetadata`: `key_id`, `created_timestamp_epoch`, `last_used_timestamp_epoch`, `total_signatures_count`, `total_volume_usd_signed`, `is_compromised`. Optional: `current_state`, `key_class`, `residual_balance_usd`, `grace_period_started_epoch`.
- **Timestamps in POSIX epoch _seconds_, not milliseconds.** The engine rejects millisecond values explicitly because that mix-up is silent and fail-open — a millisecond timestamp reads as a future creation date, which an age check that clamps negatives to zero reports as a brand-new key forever.
- The `key_class` for each key: `ONCHAIN_SIGNING` (default, stricter — irrevocable, so a sweep gates destruction) or `EXCHANGE_API` (revocable server-side). Defaulting an on-chain key to the API path is the error that loses funds.
- For an on-chain key, a balance source for `residual_balance_usd`. Without it every key looks empty and the sweep gate never engages. Know what your balance source misses — tokens, NFTs, staked or locked positions, pending rewards, outstanding contract allowances — because an address can read as empty and still carry authority worth stealing.
- A threshold policy. The defaults (90 days, 100,000 signatures, $10M, 24-hour grace) have **no regulatory basis** — set them from your own risk appetite and settlement horizons.

## Workflow

1. **Set the Thresholds Deliberately, Not by Inheritance**: Start from what SP 800-57 §5.3.1 says actually shortens a cryptoperiod — the embodiment (factor 2), the operating environment (factor 3), and "the volume of data flow or the number of transactions" (factor 5). A key held in process memory on an internet-facing host justifies far less than NIST's 1–3 year baseline; a key in an HSM does not. Record *why* you chose each number. Note that §5.3.2 warns short cryptoperiods "may be counter-productive ... where there is a significant potential for error in the re-keying" — for a trading bot a botched rotation means an unhedged book, and every on-chain rotation is a fee-bearing, publicly visible transaction.
2. **Classify the Key Before Auditing It**: `ONCHAIN_SIGNING` or `EXCHANGE_API` determines whether destruction is gated on a fund sweep. Get this wrong in the safe direction (treat an API key as on-chain) and you get a harmless extra gate; wrong in the other direction and the engine will clear an irrevocable key for shredding while it still controls money.
3. **Audit and Read the Trigger, Not Just the Verdict**: Triggers are evaluated in the order age → signatures → volume, on the **unrounded** age. The reported `key_age_days` is rounded to 2dp for display only; classification never runs on a rounded value, so a key 1 second short of 90 days stays healthy even though it displays as `90.0`.
4. **Treat Compromise as a Separate Path**: `is_compromised` overrides every threshold and skips the grace period entirely — a leaked key gets no drain window. For an on-chain key still holding value the verdict is `EMERGENCY_SWEEP_REQUIRED` and the state is `PENDING_FUND_SWEEP`, **not** shredded: the attacker retains authority over that address until the balance moves, so sweep first and destroy the material afterwards.
5. **Let the Grace Period Actually Elapse**: On a trigger the engine records `grace_period_started_epoch` and moves the key to `DEPRECATED_GRACE_PERIOD`. Re-auditing inside the window is idempotent — same replacement label, no fresh rotation event, and the clock does not restart. Size `grace_period_hours` above the slowest settlement path in use: Ethereum finalises across two consecutive 32-slot epochs (~13 minutes) and a transaction can sit in the mempool far longer; an exchange may reconcile against an API key well after the last call. The window drains work already authorised — if `last_used_timestamp_epoch` advances past the grace start, the cutover never happened and the report says so in `warnings`.
6. **Do Not Shred Until the Address Is Empty**: When the window closes, a key with a residual balance moves to `PENDING_FUND_SWEEP` and stays there; only a zero balance yields `ROTATION_COMPLETE_KEY_SHREDDED`. On-chain dust would otherwise stall this forever, so `dust_threshold_usd` exists — it defaults to `0.0` (strictly fail-closed), and raising it should be a recorded decision, not a reflex. Raising it is still better than writing a false zero balance, which corrupts the audit trail rather than the policy. `REVOKED_SHREDDED` is terminal — a revoked key re-audited later returns `KEY_ALREADY_REVOKED`, never `ACTIVE`, and a compromise flag on such a key surfaces a forensics warning rather than being silently dropped.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Citing 90 Days as a Requirement**: It is not one. The figure most likely entered circulation from AWS Config's `ACCESS_KEYS_ROTATED` rule, whose `maxAccessKeyAge` parameter *defaults* to 90 — a configurable default for IAM access keys, not a rule about wallet signing keys. NIST's actual guidance for a private signature key is 1–3 years, and PCI DSS v4.0 Req 3.7.4 leaves the cryptoperiod "as defined by the associated application vendor or key owner". Choosing 90 days is defensible; presenting it as a mandate in an audit response is not.
- **Believing a Signature Ceiling Protects the Key**: It bounds blast radius, nothing more. Where ECDSA nonces are biased or reused, private key recovery needs on the order of 2 to a few hundred signatures — the key has already leaked long before any 100,000 trigger fires. Where nonces are deterministic (RFC 6979 ECDSA, Ed25519 per RFC 8032) there is no count bound to respect at all. Fix the RNG; do not rotate around it.
- **Shredding an On-Chain Key Before Sweeping Its Address**: The key is the only thing that can move those funds. Destroy it with a balance still there and the balance is gone permanently — there is no recovery path and no issuer to appeal to. This is why `PENDING_FUND_SWEEP` exists and why it is not skippable.
- **Granting a Compromised Key a Grace Period**: A drain window assumes the old key is only finishing authorised work. A leaked key is being actively used against you, and every hour of "graceful" overlap is an hour the attacker signs freely.
- **Passing Millisecond Timestamps**: `time.time() * 1000` reads as a creation date years in the future. An engine that clamps negative ages to zero then reports a key of any real age as `KEY_HEALTHY_ACTIVE` indefinitely — a security control failing open and silently. This engine rejects the timestamp instead.
- **Re-Auditing a Key and Restarting Its Clock**: A rotation audit that re-triggers on every call never advances a key out of its grace period, floods the audit log with duplicate "rotation initiated" events, and means the documented post-grace transition never happens. Audits must be idempotent within a state.
- **Rotating a Key While the Bot Still Holds It**: The grace period covers settlement, not deployment. If the process has the old key cached in memory or an environment variable, rotation at the KMS changes nothing until the bot reloads. See `secrets-rotation-without-bot-downtime`.
- **Rotating on Schedule Instead of Reducing Scope**: A withdrawal-enabled API key rotated every 90 days is exposed for up to 90 days. The same key without withdrawal permission is not worth stealing. Rotation cadence is the weaker control of the two.

## Verification

- Audit a 30-day-old key with 5,000 signatures and confirm `KEY_HEALTHY_ACTIVE`, `is_rotation_required=False`, and no replacement label.
- Audit a 95-day-old key and confirm `ROTATION_INITIATED_AGE_EXPIRED`, state `DEPRECATED_GRACE_PERIOD`, replacement `<key_id>_V2`, and that `grace_period_started_epoch` was actually recorded — without it the post-grace transition can never be evaluated.
- Confirm the signature trigger at exactly 100,000 and the volume trigger at exactly $10,000,000 both fire (`>=` is inclusive), and that age takes precedence when several trigger together.
- Submit a key 1 second short of 90 days and confirm `KEY_HEALTHY_ACTIVE` even though `key_age_days` displays as `90.0` — classification must not run on the rounded value.
- Re-audit a key inside its grace window and confirm `GRACE_PERIOD_ACTIVE` with an unchanged `grace_period_started_epoch`; advance past the window and confirm `ROTATION_COMPLETE_KEY_SHREDDED` for an empty key, `GRACE_PERIOD_ELAPSED_PENDING_SWEEP` for one still holding a balance.
- Mark a compromised `ONCHAIN_SIGNING` key holding $250,000 and confirm `EMERGENCY_SWEEP_REQUIRED` with state `PENDING_FUND_SWEEP` — **not** `REVOKED_SHREDDED`. Confirm the same key with a zero balance yields `EMERGENCY_REVOKED_COMPROMISED`, and that an `EXCHANGE_API` key ignores the balance entirely.
- Re-audit a key already in `REVOKED_SHREDDED` and confirm `KEY_ALREADY_REVOKED` with state `REVOKED_SHREDDED` — a young revoked key must never report back as `ACTIVE`.
- Submit a millisecond timestamp, a creation date 30 days in the future, a NaN volume, a negative or fractional signature count, an empty `key_id`, an unknown state, or a `DEPRECATED_GRACE_PERIOD` key with no `grace_period_started_epoch`, and confirm `KeyRotationError` rather than a `KEY_HEALTHY_ACTIVE` verdict.
- Run `python -m unittest discover -s skills/key-rotation-schedule-for-hot-wallet-keys/scripts` and confirm a 100% pass rate.

## Related Skills

- `hot-cold-wallet-split-for-trading-bots`
- `recovery-plan-for-lost-or-compromised-keys`
- `secrets-rotation-without-bot-downtime`
- `post-incident-forensics-for-suspected-key-compromise`
- `hardware-security-module-hsm-for-signing-keys`
- `multi-party-computation-mpc-custody-solutions`
- `api-key-least-privilege-audit-tool`
