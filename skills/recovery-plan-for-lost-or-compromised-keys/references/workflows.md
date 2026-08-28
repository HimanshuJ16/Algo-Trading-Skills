# Workflows for Recovery Plan for Lost or Compromised Keys

## A. Peacetime readiness audit (what this engine runs)

1. **Assemble the plan from artefacts**
   - One `RecoveryPlanSpec` per wallet whose keys you hold. Every field comes from
     a document — the last drill report, the shard custody log, the signed test
     transaction — not from memory.
   - Counting fields left at `0` mean *unrecorded* and are reported as findings.
   - Pass an explicit `as_of_date` so the report is reproducible.

2. **Validate before scoring**
   - `KeyRecoveryPlanError` is raised for internally impossible plans: threshold
     above total shards, verified shards above total, a drill dated after the
     audit date, an unknown `wallet_type`, a blank or duplicated `plan_id`, a
     negative count, or a `SHAMIR_SSS` plan with no shard counts declared.
   - An *unrecognised* `backup_method` is not an error — it is a CRITICAL finding,
     because having no recognised backup scheme is a real audit outcome.

3. **Backup integrity**
   - Shamir: `verified < threshold` ⟹ `SHARDS_BELOW_THRESHOLD` (CRITICAL, key is
     unrecoverable now). `threshold <= verified < threshold + surplus` ⟹
     `NO_SHARD_SURPLUS` (HIGH, one shard from unrecoverable).
   - `threshold < min_shamir_threshold` ⟹ `WEAK_SHAMIR_THRESHOLD` (HIGH).
   - `max_shards_at_single_location >= threshold` ⟹ `SHARD_QUORUM_CO_LOCATED`
     (CRITICAL). Unrecorded ⟹ `SHARD_DISTRIBUTION_UNRECORDED` (MEDIUM).
   - Any method: `distinct_backup_locations < min_distinct_backup_locations` ⟹
     `BACKUP_NOT_GEOGRAPHICALLY_SEPARATED` (HIGH).

4. **Sweep readiness**
   - No destination ⟹ `NO_SWEEP_WALLET` (CRITICAL), and the sub-checks stop there.
   - Destination not independently keyed ⟹ `SWEEP_WALLET_NOT_INDEPENDENTLY_KEYED`
     (CRITICAL).
   - No confirmed test transaction ⟹ `SWEEP_WALLET_UNTESTED` (HIGH).

5. **Incident response substance**
   - No key inventory ⟹ `KEY_INVENTORY_MISSING` (HIGH).
   - Too few contacts ⟹ `INSUFFICIENT_IR_CONTACTS` (HIGH).
   - `last_drill_date is None` ⟹ `DRILL_NEVER_CONDUCTED` (CRITICAL); older than
     `max_days_since_drill` ⟹ `DRILL_OVERDUE` (HIGH).

6. **Report**
   - `RECOVERY_PLAN_READY` requires at least one plan and zero findings at any
     severity. An empty plan set returns `RECOVERY_PLAN_NOT_READY`.
   - Work findings in severity order: CRITICAL first, then HIGH, then MEDIUM.

## B. The drill this audit assumes you ran

`verified_shards_available` and `last_drill_date` are outputs of a real exercise.
A drill that satisfies them:

1. Retrieve exactly `threshold` shards — never more — from their custody
   locations, recording tamper-evidence state on each.
2. Reconstruct the key **on an air-gapped machine** and derive the first address.
   Compare it to the recorded address. A mismatch means the backup is wrong, and
   that is precisely what the drill exists to discover.
3. Confirm each retrieved shard was individually readable. Shards not retrieved
   this cycle are not "verified" — count only what you read.
4. Destroy the reconstructed material and any working files; return shards under
   fresh tamper-evident seals.
5. Record the date, the participants, the shard IDs read, and the address match.

## C. Live incident: which branch to execute

**Determine the incident type first — the two responses conflict.**

- **Suspected or confirmed compromise** (adversary may hold the key):
  1. **Sweep first.** Broadcast the evacuation to the pre-configured,
     independently keyed destination. Do not pause to reconstruct backups — the
     adversary holds the same key and can front-run you.
  2. Notify the contacts named in the plan over an out-of-band channel.
  3. Only then investigate; see `post-incident-forensics-for-suspected-key-compromise`.
  4. Treat every key derived from the same seed as compromised, and walk the key
     inventory to re-key all of them.

- **Loss with no compromise indication** (key material gone, no adversary):
  1. Do **not** move funds under time pressure. There is no adversary racing you.
  2. Reconstruct from backups per the drill procedure above.
  3. If reconstruction fails, stop and preserve every remaining shard before
     further attempts.

- **Uncertain**: treat it as a compromise. Sweeping when you did not need to costs
  a transaction fee; failing to sweep when you needed to costs the balance.
