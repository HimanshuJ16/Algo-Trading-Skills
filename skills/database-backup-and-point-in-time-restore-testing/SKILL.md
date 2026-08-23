---
name: database-backup-and-point-in-time-restore-testing
description: Drill harness for PostgreSQL/TimescaleDB continuous WAL archiving — replays
  archived backup metadata to a target timestamp, detects WAL gaps and unreachable
  recovery targets, and audits measured RPO/RTO against internal objectives.
domain: Infrastructure & Operations
subdomain: Database Disaster Recovery
tags:
- database-backup
- pitr-restore
- wal-archiving
- rpo-rto-testing
- disaster-recovery
- timescaledb
- postgresql
- data-integrity
brokers_frameworks:
- PostgreSQL WAL
- TimescaleDB
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when operating a trading database (PostgreSQL/TimescaleDB) whose order books, fill records, and account balances must survive a bad deployment, a corrupting migration, or a regional outage — and recovery must land on an exact timestamp $T_{\text{target}}$ just before the damage. It is a **drill harness over backup metadata**: given the base snapshots and the WAL sequence your archive actually holds, it answers whether a recovery to $T_{\text{target}}$ would succeed, how wide the data-loss window would be, and whether that lands inside your RPO/RTO objectives.

Its practical value is catching the failure that real incidents expose too late: an archive that silently stopped advancing weeks ago, so that the "daily backups" everyone trusts cannot reach any recent recovery point.

## When NOT to Use

- **As proof that a backup is restorable.** This module replays an abstract WAL model, not real WAL pages. Only an actual restore into an isolated instance, followed by application-level reconciliation, proves recoverability. Use this between drills, not instead of them.
- **As a recovery tool during a live incident.** It performs no I/O against any database and restores nothing. Follow your runbook (`disaster-recovery-runbook-for-full-region-outage`).
- **For logical/vendor backup schemes without a WAL-style sequence** (e.g. `pg_dump` snapshots alone). PITR requires continuous archiving; with dump-only backups the recovery point is the dump, and this model does not apply.
- **As a compliance determination.** The 60s/15m objectives here are internal engineering targets, not regulatory thresholds — see `references/standards.md`.

## Prerequisites

- Base snapshot records (`snapshot_id`, `timestamp_ms` = backup **completion** time, `table_rows`, `state_checksum`). Supply `last_lsn_included` where the archive catalogue knows it: a hole sitting immediately after the backup is otherwise invisible, because the surviving records are contiguous among themselves.
- Archived WAL records (`lsn_id`, `timestamp_ms`, `table_name`, `operation`, `row_id`, `payload`). `lsn_id` must be a monotonically increasing **archive sequence number**, not a raw `pg_lsn` byte offset — contiguity of that sequence is what exposes a hole in the archive.
- Recovery objectives (`max_rpo_sec`, `max_rto_min`), agreed with the desk rather than copied from this document.
- Optionally, an `ExpectedDatabaseState` derived **independently** of the WAL — reconciled ledgers or broker statements. Deriving it from the same WAL the engine replays makes the integrity check circular.

## Workflow

1. **Ingest snapshots and the WAL sequence.** Validate before trusting: a duplicate LSN makes replay order ambiguous, an LSN whose commit time precedes its predecessor's means the archive is not a coherent stream, and an unrecognised operation string means row counts would be silently wrong. Each raises `PitrRestoreError` rather than producing a report.
2. **Select the base snapshot.** Take the latest snapshot *completing* at or before $T_{\text{target}}$; PostgreSQL requires the stop point to be after the base backup's end. Replay only WAL strictly after that completion time — the snapshot already contains everything at or before it.
3. **Replay with continuity enforced.** Sort by LSN and stop at the first hole. Recovery halts at a missing or corrupt WAL segment; it does not skip past it, so a gap truncates the replay and everything after it is unreachable. The target is **inclusive** — a commit at exactly $T_{\text{target}}$ is applied, matching `recovery_target_inclusive = on`.
4. **Decide reachability before reporting a recovery point.** If the usable archive ends before $T_{\text{target}}$, this is not a partial success: PostgreSQL "will shut down with a fatal error" when archive recovery ends before the configured target. Report `recovery_target_reached = False` and fail the drill.
5. **Measure the data-loss window.** $\text{RPO}_{\text{sec}} = \max\left(0, \frac{T_{\text{target}} - T_{\text{horizon}}}{1000}\right)$, where $T_{\text{horizon}}$ is the furthest point the usable archive proves it reaches. A compliant RPO on an unreachable target is still a failed drill — check both flags.
6. **Measure RTO** as the wall-clock duration of the restore, not the replay arithmetic; feed the measured value in.
7. **Verify integrity against an independent expectation.** Compare restored row counts and the replay digest to `ExpectedDatabaseState`. With no expectation supplied, `integrity_verified` is `None` and the report says so — unverified, not verified.
8. **Emit the `PitrBackupAuditReport`** and archive it; drill-over-drill comparison is what surfaces a slowly degrading archive.

> Full procedure: see `references/workflows.md`.
> Standards and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reading a zero RPO as good news.** Zero data loss and "no WAL newer than the target" look identical if the arithmetic is written the wrong way round. Measure the shortfall $T_{\text{target}} - T_{\text{horizon}}$; an archive that stopped an hour ago must report ~3600s, not 0.
- **Treating an unreachable target as a partial restore.** If the archive ends before $T_{\text{target}}$, PostgreSQL fatal-errors out; there is no half-recovered database to trade from. Reachability is a pass/fail gate, not a quality score.
- **Skipping a WAL gap.** A hole in the sequence is not a missing row or two — everything after the hole is unrecoverable, because recovery stops at the break.
- **Backing up without ever restoring.** Nightly backups that are never replayed hide corrupt WAL, a broken `archive_command`, and expired credentials until an outage makes all three urgent at once.
- **Relying on full backups alone.** Without continuous archiving the recovery point is the last full backup — up to a full day of fills, positions, and balances gone.
- **Declaring success when the engine starts.** A database that starts is not a database that reconciles. Compare restored trade ledgers against broker records before resuming trading.
- **Verifying against expectations derived from the same WAL.** That checks the replay against itself and always passes.
- **Feeding raw `pg_lsn` byte offsets as `lsn_id`.** Every step then looks like a gap; either map to a dense sequence number or set `require_contiguous_lsn=False` and accept that gap detection is off.

## Verification

- Instantiate `PitrBackupTesterEngine`, restore to a target the archive covers, and confirm records at exactly $T_{\text{target}}$ are replayed while later ones are not.
- Regression check: an archive whose last WAL is 50 minutes before $T_{\text{target}}$ must report `rpo_seconds ≈ 3000`, `recovery_target_reached = False`, and `is_restoration_successful = False`.
- Continuity check: remove one `lsn_id` from the middle of the sequence and confirm replay truncates at the hole and the drill fails.
- Integrity check: supply an `ExpectedDatabaseState` that disagrees by one row and confirm `integrity_verified` is `False`.
- Run `python -m unittest discover -s skills/database-backup-and-point-in-time-restore-testing/scripts`.

## Related Skills

- `cross-region-data-replication-lag-monitoring`
- `exchange-gateway-redundancy-and-failover-testing`
- `disaster-recovery-runbook-for-full-region-outage`
- `zero-downtime-database-schema-migrations`
- `data-retention-policy-and-storage-tiering`
