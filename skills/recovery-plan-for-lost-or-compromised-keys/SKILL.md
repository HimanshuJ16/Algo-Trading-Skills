---
name: recovery-plan-for-lost-or-compromised-keys
description: >-
  Crypto custody key recovery plan engine auditing backup availability, Shamir shard integrity, emergency fund sweep readiness, and incident response checklist compliance.
domain: Crypto Custody & Security
subdomain: Key Recovery & Incident Response
tags: ["key-recovery", "compromised-keys", "shamir-secret-sharing", "cold-storage", "emergency-sweep", "incident-response"]
brokers_frameworks: ["NIST Cybersecurity Framework", "Shamir Secret Sharing (SSS)", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when establishing and auditing disaster recovery plans for cryptographic key loss or compromise in crypto custody operations. Key compromise events require immediate emergency fund sweeps to new wallets, while key loss events require verified backup restoration from Shamir shards or HSM-backed seeds. This engine audits recovery plan readiness across backup availability, shard integrity, sweep wallet configuration, and incident response checklist completion.

## Prerequisites

- Recovery plan components (`plan_id`, `wallet_type`, `backup_method`, `shamir_threshold`, `shamir_total_shards`, `verified_shards_available`, `sweep_wallet_configured`, `last_drill_date`, `incident_response_contacts`).
- Config options (`max_days_since_drill`: default 90, `min_shamir_surplus_shards`: default 1).

## Workflow

1. **Backup Method Validation**:
   - Verify backup method is present and recognized (`SHAMIR_SSS`, `HSM_SEED`, `MNEMONIC_PHRASE`).
2. **Shamir Shard Integrity Check**:
   - Verify $\text{VerifiedShards} \ge \text{Threshold} + \text{MinSurplus}$.
   - Flag if insufficient shards available to reconstruct key.
3. **Emergency Sweep Wallet Readiness**:
   - Verify sweep wallet is pre-configured for immediate fund evacuation.
4. **Recovery Drill Recency**:
   - Verify last drill date is within $\text{MaxDaysSinceDrill}$ window.
5. **Audit Report Generation**: Output structured `KeyRecoveryReadinessReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Untested Backups**: Maintaining Shamir shards that have never been verified through a full reconstruction drill.
- **Missing Sweep Wallet**: Failing to pre-configure emergency sweep destination wallets, delaying fund evacuation during active compromise.
- **Co-located Shards**: Storing all Shamir shards in the same geographic location, creating single-point-of-failure risk.

## Verification

- Instantiate `RecoveryPlanForLostOrCompromisedKeysEngine`. Audit plan with 3-of-5 Shamir shards, 4 verified available, sweep configured, drilled 30 days ago $\implies$ verify `RECOVERY_PLAN_READY`. Audit plan with only 2 verified shards (below 3 threshold) $\implies$ verify `RECOVERY_PLAN_NOT_READY`.
- Run `python scripts/test_recovery_plan_for_lost_or_compromised_keys.py`.

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `post-incident-forensics-for-suspected-key-compromise`
---
