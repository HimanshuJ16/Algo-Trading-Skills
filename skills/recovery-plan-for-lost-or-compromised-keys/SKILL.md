---
name: recovery-plan-for-lost-or-compromised-keys
description: >-
  Readiness auditor for crypto custody key-loss and key-compromise recovery plans, checking backup integrity, Shamir shard sufficiency and quorum distribution, emergency sweep readiness, key inventory, and drill recency against NIST SP 800-57 and CCSS.
domain: Crypto Custody & Security
subdomain: Key Recovery & Incident Response
tags: ["key-recovery", "compromised-keys", "shamir-secret-sharing", "cold-storage", "emergency-sweep", "incident-response"]
brokers_frameworks: ["NIST SP 800-57 Part 1 Rev. 5", "NIST CSF 2.0 (Recover)", "CCSS v9", "Shamir Secret Sharing (SSS)", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when building or periodically auditing the recovery plan that stands behind a trading operation's signing keys — hot wallet keys used by execution bots, warm operational keys, and cold-storage treasury keys. It converts a documented plan into an auditable list of severity-ranked findings, so "we have backups" becomes a claim with evidence behind it.

It covers the two incidents that are routinely conflated:

- **Key loss** — the key material is gone and no adversary holds it. Only *backups* help: enough verified Shamir shards, an HSM seed, a mnemonic.
- **Key compromise** — an adversary holds, or may hold, the key material. Backups are worthless because the adversary can spend from the same key. Only a *pre-configured sweep* to an independently keyed destination helps. NIST SP 800-57 Part 1 Rev. 5 §5.5.2 states that a compromise-recovery plan "shall be documented and easily accessible".

The engine audits both for every plan, because an incident does not announce which kind it is at the moment you need the plan.

## When NOT to Use

- **As the incident response itself.** This is a readiness audit run in peacetime. During a live compromise, execute the plan; see `post-incident-forensics-for-suspected-key-compromise`.
- **As verification that a backup works.** The engine scores what it is told. `verified_shards_available` is your drill's finding, not the engine's — nothing here reads a shard or signs a transaction.
- **For exchange-held or third-party-custodied assets.** You do not hold those keys; the relevant controls are withdrawal whitelists, API key scoping, and vendor diligence. See `custody-solution-vendor-due-diligence-checklist` and `exchange-withdrawal-whitelist-enforcement`.
- **For MPC/threshold-signature schemes.** MPC shares are not Shamir backup shards and the refresh/reshare model differs; see `multi-party-computation-mpc-custody-solutions`.

## Prerequisites

- A documented plan per wallet, populated from artefacts rather than memory: `plan_id`, `wallet_type` (`HOT`/`WARM`/`COLD`), `backup_method` (`SHAMIR_SSS`/`HSM_SEED`/`MNEMONIC_PHRASE`), `shamir_threshold`, `shamir_total_shards`, `verified_shards_available`, `max_shards_at_single_location`, `distinct_backup_locations`, `sweep_wallet_configured`, `sweep_wallet_independently_keyed`, `sweep_wallet_test_transaction_verified`, `key_inventory_documented`, `incident_response_contacts`, `last_drill_date` (a `datetime.date`, or `None` for never drilled).
- A calibrated threshold policy. The config defaults (`max_days_since_drill=90`, `min_shamir_surplus_shards=1`, `min_shamir_threshold=2`, `min_incident_response_contacts=2`, `min_distinct_backup_locations=2`) are **engineering defaults**. No regulator mandates a drill cadence; CCSS Level III (2.04.2.1) requires the Key Compromise Policy be tested *at least annually*, and NIST SP 800-53 Rev. 5 CP-4 leaves test frequency organisation-defined.
- An explicit `as_of_date` for reproducible output.

## Workflow

1. **Populate from artefacts, and treat zero as "unrecorded"**: Counting fields default to `0`, which the audit reports as a finding rather than a pass. An unrecorded control is not a passing control — record the real number or accept the finding.
2. **Backup Integrity**: An unrecognised `backup_method` is reported as a CRITICAL finding, not raised — "we have no recognised backup scheme" is a real audit result. For Shamir, distinguish the two shard failures rather than lumping them: `verified_shards_available < threshold` means the key **cannot be reconstructed today** (CRITICAL); meeting the threshold but not the surplus means it is recoverable **until one shard is lost** (HIGH).
3. **Shard Quorum Distribution**: Check `max_shards_at_single_location` against the threshold, not just the total shard count. A 3-of-5 split with three shards in one vault is not a 3-of-5 split — that vault can reconstruct the key alone, and losing that one site destroys the quorum. Both directions of the failure are the same CRITICAL finding.
4. **Sweep Readiness**: Confirm a destination exists, that its key material is **independent** of the key being protected, and that a real test transaction has confirmed to it. If no sweep wallet is configured at all, the sub-checks are suppressed — reporting "untested sweep wallet" about a wallet that does not exist is noise.
5. **Incident Response Substance**: Check the key inventory (NIST SP 800-57 §5.5.2(d), the prerequisite for monitoring re-keying across all affected keys per (h)) and contact depth (§5.5.2 separates personnel to notify (a), to perform recovery (b), and to support it (f)).
6. **Drill Recency**: `last_drill_date=None` is `DRILL_NEVER_CONDUCTED` (CRITICAL), reported distinctly from `DRILL_OVERDUE` (HIGH). A plan that has never been rehearsed has unknown recoverability; a plan rehearsed 100 days ago has known recoverability and a stale attestation. Do not let a sentinel "days since drill" number blur the two.
7. **Report**: `RECOVERY_PLAN_READY` requires at least one plan and zero findings at any severity. An empty plan set returns `RECOVERY_PLAN_NOT_READY` and says so — it is not evidence of readiness.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Sweeping Into the Same Seed**: The most expensive mistake in this skill's domain. A sweep wallet generated from the same mnemonic, the same hardware device, or the same keyholder set as the compromised key hands the funds straight back to the adversary. `sweep_wallet_independently_keyed` is a CRITICAL check for exactly this reason.
- **Counting Shards That Exist Rather Than Shards That Are Readable**: A shard in a deposit box that no one has opened in two years is not verified. `verified_shards_available` means confirmed readable within the current drill cycle; anything else is an untested backup wearing a number.
- **Co-locating a Quorum**: Splitting 3-of-5 and then storing three shards in the same safe reduces the scheme to 1-of-3 against that custodian and to a single point of loss against fire or seizure. Distribution has to be checked against the *threshold*, not against the shard count.
- **Confusing Loss with Compromise**: Restoring from backup during a *compromise* is the wrong move — the adversary holds the same key and can front-run your recovery transaction. Sweep first, restore later. Conversely, a sweep wallet does nothing for a pure key-loss event.
- **Treating 90 Days as a Regulatory Requirement**: It is not. No regulator prescribes a key recovery drill cadence. CCSS Level III requires *annual* testing of the Key Compromise Policy, and NIST CP-4 leaves the frequency to you. Present the 90-day default as internal policy, or an auditor will ask for the citation you do not have.
- **Auditing a Batch with Duplicate `plan_id`s**: Readiness counts silently become ambiguous, so the engine raises instead. Likewise, a drill dated after the audit date and a shard count exceeding the split size raise `KeyRecoveryPlanError` rather than being scored.

## Verification

- Audit a 3-of-5 plan with 4 verified shards, max 2 shards per location, 3 backup locations, an independently keyed and test-transacted sweep wallet, a documented key inventory, 3 contacts, and a drill 30 days before `as_of_date` ⟹ `RECOVERY_PLAN_READY` with zero issues.
- Drop `verified_shards_available` to 2 ⟹ `SHARDS_BELOW_THRESHOLD` at CRITICAL. Set it to exactly 3 ⟹ `NO_SHARD_SURPLUS` at HIGH and `critical_issue_count == 0` — confirm the two are not merged.
- Set `max_shards_at_single_location=3` on the 3-of-5 plan ⟹ `SHARD_QUORUM_CO_LOCATED` at CRITICAL; set it to 2 ⟹ ready.
- Set `sweep_wallet_independently_keyed=False` ⟹ `SWEEP_WALLET_NOT_INDEPENDENTLY_KEYED` at CRITICAL. Set `sweep_wallet_configured=False` ⟹ `NO_SWEEP_WALLET` alone, with no `SWEEP_WALLET_UNTESTED` noise.
- Set `last_drill_date=None` ⟹ `DRILL_NEVER_CONDUCTED` at CRITICAL and no `DRILL_OVERDUE`. A drill exactly 90 days before `as_of_date` passes; 91 days flags.
- Submit `shamir_threshold=6` on a 5-shard split, a future `last_drill_date`, a duplicated `plan_id`, or `wallet_type="FROZEN"` ⟹ `KeyRecoveryPlanError` rather than a scored report.
- Run `python -m unittest discover -s skills/recovery-plan-for-lost-or-compromised-keys/scripts` and confirm a 100% pass rate.

## Related Skills

- `shamir-secret-sharing-for-key-backup`
- `post-incident-forensics-for-suspected-key-compromise`
- `cold-storage-geographic-distribution-strategy`
- `test-transaction-verification-before-large-transfers`
- `key-rotation-schedule-for-hot-wallet-keys`
