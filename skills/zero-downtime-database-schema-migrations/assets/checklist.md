# Institutional Zero-Downtime Migration Checklist

## Before Phase 1
- [ ] **Restore tested, not just configured**: a point-in-time restore of the target table has been exercised. Phase 5 has no rollback (`database-backup-and-point-in-time-restore-testing`).
- [ ] **Deploy window cleared**: not inside a freeze window around a market event (`deployment-freeze-windows-around-market-events`).
- [ ] **Replica lag monitoring readable programmatically**, and the backfill runner reads it. A missing reading stops the backfill; it is never treated as zero lag.
- [ ] **Migration tool can apply a statement outside a transaction** (`CREATE INDEX CONCURRENTLY` requires it; Alembic/Flyway wrap migrations in a transaction by default).
- [ ] **Backwards compatibility confirmed**: the currently deployed application release tolerates the new column existing.

## Phase 1: Expand DDL & Non-Blocking Indexes
- [ ] **Lock wait bounded before the DDL**: `SET lock_timeout` (PostgreSQL) or `SET SESSION lock_wait_timeout` (MySQL, whose default is one year). A timeout is the control working — retry, do not remove the bound.
- [ ] **Column added as `DEFAULT NULL`** or with a non-volatile default only. A volatile default (`gen_random_uuid()`, `clock_timestamp()`) rewrites the whole table under ACCESS EXCLUSIVE.
- [ ] **MySQL algorithm chosen deliberately**: `ALGORITHM=INSTANT` where eligible (8.0.12+ add / 8.0.29+ drop, not `ROW_FORMAT=COMPRESSED`, no FULLTEXT index, `TOTAL_ROW_VERSIONS` < 64) with **no `LOCK` clause**; INPLACE fallback accepted knowingly, including its table rebuild on `DROP COLUMN`.
- [ ] **Index built non-blocking and outside transaction control**: `CREATE INDEX CONCURRENTLY` (PostgreSQL, no transaction block), `... (col) ALGORITHM=INPLACE LOCK=NONE` with **no comma before the options** (MySQL), plain `CREATE INDEX` (CockroachDB — already online).
- [ ] **Index build verified valid**: `pg_index.indisvalid` is true. A failed CONCURRENTLY build leaves an INVALID index the planner ignores but writes still maintain — drop and rebuild it.
- [ ] **CockroachDB only**: the background schema-change job polled to completion, not assumed from statement success.

## Phase 2 & 3: Dual-Write & Batched Backfill
- [ ] **Dual-write deployed AND confirmed on every instance** — not just triggered. Rows written by the last non-dual-write instance are exactly what the cutover gate catches.
- [ ] **Backfill batched at 1,000-5,000 rows** per transaction.
- [ ] **Lag checked before each batch is applied**, not after. Over budget (default 1.0 s) the batch is withheld, not sent-then-paused.
- [ ] **Backfill runner branches on the returned directive** (`CONTINUE` / `THROTTLE` / `COMPLETE`) rather than ignoring it and looping.

## Phase 4: Read Cutover
- [ ] **Residual-NULL count run on the PRIMARY**: `SELECT count(*) FROM tbl WHERE new_col IS NULL` returns **0**. The replica is behind by exactly the lag the backfill generated.
- [ ] **Result passed to the engine** as `residual_null_rows`; the backfill percentage alone did not unlock the cutover.
- [ ] **`require_residual_null_check` left enabled.** If it was disabled, the reason is recorded and accepted by whoever owns the table.
- [ ] **Reader cutover deployed** and the new read path exercised against real historical rows, not only rows written since the migration began.

## Phase 5: Contract
- [ ] **Soak completed**: at least one full rollback window elapsed with `old_col` still populated and the new read path live.
- [ ] **Backup verified within the soak window** — this is the last point at which `old_col` is recoverable without one.
- [ ] **Dual-write code removed** before or with the drop, so nothing writes to a column that no longer exists.
- [ ] **`DROP COLUMN` applied behind the same lock bound** as Phase 1, with the algorithm chosen deliberately (MySQL INPLACE drop rebuilds the table; PostgreSQL does not reclaim the space until rows are rewritten).
- [ ] **CockroachDB only**: accepted that some column drops cannot be rolled back properly and the data may be partially or totally missing if one is attempted.
