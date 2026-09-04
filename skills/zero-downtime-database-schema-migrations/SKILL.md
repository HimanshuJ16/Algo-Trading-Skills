---
name: zero-downtime-database-schema-migrations
description: >-
  Use when altering the schema of a database a 24/7 trading system reads or writes,
  without a trading-hours outage; a five-phase expand-contract sequence with
  lock-bounded non-blocking DDL for Postgres and MySQL.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: zero-downtime, database-migration, expand-contract, concurrent-index, postgresql, mysql-innodb, cockroachdb, dual-write
  brokers_frameworks: "alembic; flyway; liquibase; gh-ost; pt-online-schema-change"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when altering the schema of a database that a 24/7 trading system reads or writes — the order ledger, the fills table, the position store, the tick archive — without a trading-hours outage. It covers adding columns, renaming fields by migration, changing types, and adding indexes.

It provides:
- **Lock-bounded, engine-correct DDL** for PostgreSQL, MySQL 8.0/InnoDB and CockroachDB, including the `lock_timeout` / `lock_wait_timeout` guard that is the actual difference between "fast DDL" and "zero downtime".
- **The 5-Phase Expand-Contract sequence**: (1) Expand — add the new column as nullable; (2) Dual Write — the application writes both columns; (3) Backfill — batched, replica-lag-paced updates of historical rows; (4) Read Cutover — the application reads only the new column; (5) Contract — drop the dual write and the old column.
- **A cutover gate that requires evidence**, not a progress counter: Phase 4 is refused until a `SELECT count(*) ... WHERE new_column IS NULL` against the primary returns zero.

## When NOT to Use

- **For a schema change that is not backwards compatible in one step.** Expand-contract exists because old and new application code run simultaneously during a rollout. A change that no version of the code can tolerate — dropping a column still read by the running release, or narrowing a type in place — is not made safe by this workflow. Split it into more phases.
- **To version an internal wire payload.** Tick and message envelopes with a `schema_version` header, and adapters that migrate between versions, are `tick-data-schema-versioning`. This skill governs the database you own on both sides.
- **To validate an inbound vendor feed's fields.** That is `data-pipeline-schema-contract-testing`.
- **As the backup.** Phase 5 is irreversible: once `old_column` is dropped its data is gone from the live table. The restore path is `database-backup-and-point-in-time-restore-testing`, and it must be tested *before* the contract step, not discovered during it.
- **For very large MySQL table rebuilds under load.** When `ALGORITHM=INSTANT` is refused and the INPLACE fallback would rebuild a multi-hundred-GB table, an external copy tool (`gh-ost`, `pt-online-schema-change`) gives you throttling and a cutover you control. This engine emits the statement; it does not pace a table rebuild.
- **During a deployment freeze window.** The migration is a deploy. `deployment-freeze-windows-around-market-events` decides whether today is the day.

## Prerequisites

- Python 3.10+, standard library only (`re`, `dataclasses`, `enum`, `logging`, `typing`).
- PostgreSQL 11+, MySQL 8.0.12+ (8.0.29+ for instant `DROP COLUMN`), or CockroachDB.
- **A tested restore** of the target table — Phase 5 has no rollback.
- **Replica lag monitoring** you can read programmatically. The backfill throttles on it; without a reading you are pacing blind.
- **Confirmed dual-write coverage**: a way to verify every application instance is running dual-write code, not just that the deploy was triggered. The rows written by the last non-dual-write instance are exactly the rows the cutover gate exists to catch.
- A migration tool that can apply a statement **outside** a transaction (`CREATE INDEX CONCURRENTLY` requires it; Alembic and Flyway wrap migrations in a transaction by default).

## Workflow

1. **Define the plan.** Construct `MigrationPlan` with the table, old/new column, type, engine and a row count. Identifiers are validated on construction — anything that is not a plain unquoted identifier is rejected rather than escaped, because this text is interpolated into DDL.
2. **Generate the DDL.** `generate_expand_contract_ddl(plan)` returns `expand_sql`, `concurrent_index_sql`, `contract_sql`, the `expand_fallback_sql` / `contract_fallback_sql` for when MySQL refuses `ALGORITHM=INSTANT`, `lock_timeout_sql`, `index_cleanup_sql`, `verification_sql`, and `notes`. **Read `notes` before applying anything** — they carry the engine-specific caveats that decide whether the statement is safe on your server version.
3. **Apply Phase 1 behind a lock timeout.** Run `lock_timeout_sql`, then `expand_sql`. If it times out, that is the control working: something long-running holds a conflicting lock. Retry later — do not remove the timeout. On MySQL, if `ALGORITHM=INSTANT` is refused (`ERROR 4092`, compressed row format, a FULLTEXT index), decide deliberately whether to accept the INPLACE fallback's table rebuild or switch to `gh-ost`.
4. **Build the index outside transaction control.** `CREATE INDEX CONCURRENTLY` cannot run in a transaction block. If it fails, PostgreSQL leaves an INVALID index that the planner ignores but writes still maintain — check `pg_index.indisvalid`, apply `index_cleanup_sql`, then rebuild. Do not simply re-run the create.
5. **Deploy dual write (Phase 2), then confirm it is everywhere.** Advance with `advance_migration_phase(plan, PHASE_2_DUAL_WRITE)` only after every instance is confirmed on the new code. A partial rollout writes NULLs that the backfill's plan-time row count will not know about.
6. **Backfill in paced batches (Phase 3).** Call `execute_batched_backfill_step(plan, batch_size, replica_lag_seconds)` per batch and **branch on the returned directive**: `CONTINUE` applies the next batch, `THROTTLE` means lag is over budget and no batch was counted — pause, do not apply the update, `COMPLETE` means the counter is exhausted. Ignoring `THROTTLE` and pushing the batch anyway is how a backfill starves every read replica the trading system reads from.
7. **Verify, then cut over (Phase 4).** Run `verification_sql` on the **primary** and pass the result: `advance_migration_phase(plan, PHASE_4_READ_CUTOVER, residual_null_rows=n)`. A non-zero `n` blocks the cutover. The engine refuses to advance on the counter alone, because `total_records` was a snapshot and the rows written during the dual-write rollout are not in it.
8. **Soak, then contract (Phase 5).** Leave the old column populated and readable for at least one full rollback window before advancing to `PHASE_5_CONTRACT` and applying `contract_sql`. Once dropped, `rollback_migration_phase` refuses and the only path back is a restore.
9. **Roll back by stepping back.** For a failure in Phases 2-4, `rollback_migration_phase(plan, reason)` steps one phase back; redeploy the previous application code. The backfill counter is deliberately preserved — backfilled rows stay correct across a code rollback.

## Common Pitfalls

- **Believing "fast DDL" means "no lock".** PostgreSQL `ADD COLUMN` and `DROP COLUMN` take an **ACCESS EXCLUSIVE** lock, which conflicts with every lock mode including the ACCESS SHARE that every `SELECT` takes, and a statement waiting for a conflicting lock "will wait indefinitely". The statement is milliseconds; the wait behind one long analytics query is not, and while it waits your order queries are behind it. Always set `lock_timeout` and retry on timeout.
- **Leaving MySQL's `lock_wait_timeout` at its default.** It is 31536000 seconds — one year. MySQL DDL needs an exclusive metadata lock and blocks behind any open transaction on the table, so the default is "wait forever" with extra steps.
- **Writing `CREATE INDEX ... (col), ALGORITHM=INPLACE` on MySQL.** The comma-separated option list is an `ALTER TABLE` construct. `CREATE INDEX`'s grammar is `ON tbl (key_part,...) [index_option] [algorithm_option | lock_option]` — no comma. The comma form is a syntax error, and it fails at 3am in the middle of a migration, not in review.
- **Reaching for `ALGORITHM=INPLACE` because it sounds safer than `INSTANT`.** For `DROP COLUMN`, in-place **rebuilds the entire table**; instant only edits metadata. On a large fills table that is the difference between milliseconds and hours of I/O and replication volume. `INSTANT` is the default for `ADD COLUMN` since 8.0.12 and for `DROP COLUMN` since 8.0.29 — and note that only `LOCK=DEFAULT` is permitted with it, so an `INSTANT` statement must carry no `LOCK` clause.
- **Adding the column with a volatile default.** PostgreSQL stores a *non-volatile* `DEFAULT` in the catalogue with no rewrite, but `DEFAULT gen_random_uuid()` or `DEFAULT clock_timestamp()` rewrites the whole table and every index while holding ACCESS EXCLUSIVE. Add the column as NULL and backfill.
- **Running `CREATE INDEX CONCURRENTLY` inside a transaction.** It cannot run in a transaction block, and most migration frameworks open one for you by default. It also leaves an INVALID index behind on failure — invisible to the planner, still maintained by every write.
- **Treating the backfill counter as proof.** `backfilled_records / total_records == 100%` says the batches you planned have run. It does not say no NULLs remain: `total_records` was a snapshot, and rows written between the expand and the last instance picking up dual-write code are NULL and uncounted. Cutting reads over then returns NULL for real historical orders. Gate on `SELECT count(*) WHERE new_column IS NULL` against the primary.
- **Reading the verification count from a replica.** The replica is behind by exactly the lag your backfill has been generating. Count on the primary.
- **Running the backfill flat out.** Batched updates that outrun replication push replica lag up, and every component reading from a replica — risk checks, position views, dashboards — silently reads stale state. Throttle on measured lag and treat a missing lag reading as a stop, not as zero.
- **Skipping the dual-write soak before contracting.** Dropping `old_column` immediately after cutover means the first bug found in the new read path has no rollback. Once the column is dropped, only a restore brings it back.
- **Assuming a CockroachDB statement that returned has been applied.** Schema changes are asynchronous background jobs that return before completion, must not be mixed into multi-statement explicit transactions, and some column drops cannot be rolled back properly — the rollback can succeed with the column data partially or totally missing.

## Verification

Run the unit test suite. It covers DDL generation per engine (including the MySQL `CREATE INDEX` option-separator and the `INSTANT`/`LOCK` incompatibility), identifier rejection, phase-transition legality, the replica-lag throttle, the residual-NULL cutover gate, and rollback:

```bash
python -m unittest discover -s skills/zero-downtime-database-schema-migrations/scripts
```

Then sign off against `assets/checklist.md`.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `database-backup-and-point-in-time-restore-testing`
- `deployment-freeze-windows-around-market-events`
- `tick-data-schema-versioning`
- `data-pipeline-schema-contract-testing`
- `canary-releases-for-strategy-code-changes`
- `backtest-database-schema-for-point-in-time-queries`
- `cross-region-data-replication-lag-monitoring`
