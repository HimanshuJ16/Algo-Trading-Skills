# Institutional Zero-Downtime Migration Checklist

## Phase 1: Expand DDL & Non-Blocking Indexes
- [ ] **Nullable Column Addition**: Verify new column is added as `DEFAULT NULL` without exclusive table locks.
- [ ] **Concurrent Index Build**: Ensure indexes are built using `CREATE INDEX CONCURRENTLY` (PostgreSQL) or `LOCK=NONE` (MySQL).

## Phase 2 & 3: Dual-Write & Batched Backfill
- [ ] **Dual-Write Application Deployment**: Deploy application code writing to both `old_column` and `new_column`.
- [ ] **Batched Backfill Execution**: Run asynchronous backfill in non-blocking batches of 1,000-5,000 rows.
- [ ] **Replication Lag Monitoring**: Monitor read replica replication lag ($< 1.0\ \text{s}$) during backfill execution.

## Phase 4 & 5: Read Cutover & Contract
- [ ] **Backfill Completion Verification**: Confirm backfill progress reaches 100.0% before initiating reader cutover.
- [ ] **Reader Cutover Deployment**: Switch application read queries exclusively to `new_column`.
- [ ] **Old Column Deprecation & Drop**: Remove dual-write logic and safely execute `ALTER TABLE DROP COLUMN old_column`.