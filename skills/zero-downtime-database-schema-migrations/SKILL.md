---
name: zero-downtime-database-schema-migrations
description: "Institutional deployment engineering skill for orchestrating 5-Phase Expand-Contract zero-downtime database schema migrations, generating non-blocking DDL scripts (CREATE INDEX CONCURRENTLY), executing batched asynchronous backfills, and verifying reader cutover safety."
domain: Deployment Operations & Database Infrastructure
subdomain: Zero-Downtime Schema Evolution & Lock-Free DDL
tags:
- zero-downtime
- database-migration
- expand-contract
- concurrent-index
- postgresql
- mysql-innodb
- cockroachdb
- dual-write
brokers_frameworks:
- alembic
- flyway
- liquibase
- gh-ost
- pt-online-schema-change
version: "1.1.0"
author: Quant Engineering
license: MIT
---

## When to Use

Use this skill when executing database schema migrations (adding columns, renaming fields, altering types, creating indexes) on 24/7 high-frequency quantitative trading systems, market data repositories, or exchange execution engines without taking system downtime or acquiring exclusive table write locks.

This skill provides institutional mechanisms to:
- Generate non-blocking DDL SQL scripts for PostgreSQL (`CREATE INDEX CONCURRENTLY`), MySQL (`ALGORITHM=INPLACE, LOCK=NONE`), and CockroachDB.
- Orchestrate the **5-Phase Expand-Contract Migration Pattern**:
  1. **Phase 1 (Expand)**: Add new column/table as nullable without locking.
  2. **Phase 2 (Dual Write)**: Application writes to BOTH old and new columns.
  3. **Phase 3 (Backfill)**: Asynchronously backfill historical records in non-blocking batches.
  4. **Phase 4 (Read Cutover)**: Application reads exclusively from new column.
  5. **Phase 5 (Contract)**: Remove dual writes and safely drop old column.
- Prevent premature read cutover before historical backfill is 100% verified.

## Prerequisites

- Python 3.9+
- Standard Python libraries (`datetime`, `dataclasses`, `math`, `typing`).
- SQL Database Connection (PostgreSQL 11+, MySQL 8.0+, or CockroachDB).

## Workflow

1. **Define Migration Plan**: Construct `MigrationPlan` specifying plan ID, table name, old column name, new column name, column type, database engine, and total historical record count.
2. **Generate Non-Blocking DDL**: Call `generate_expand_contract_ddl(plan)` to generate non-blocking `ALTER TABLE` and `CREATE INDEX CONCURRENTLY` SQL scripts.
3. **Execute Phase 1 Expand & Phase 2 Dual Write**: Apply DDL and deploy application code that writes incoming records to both old and new columns.
4. **Run Batched Asynchronous Backfill**: Invoke `execute_batched_backfill_step(plan, batch_size)` iteratively to update historical rows without table locks.
5. **Verify Cutover & Contract**: Call `advance_migration_phase(plan, target_phase)` to validate 100% backfill completion, switch application reader logic to `new_column`, and drop `old_column`.

## Common Pitfalls

- **Blocking `ALTER TABLE ADD COLUMN NOT NULL`**: Adding a column with a non-null constraint and default value on large tables acquires an exclusive Access Exclusive table lock, causing query timeouts and order execution halts. Always add columns as **NULLable** first.
- **Blocking Index Builds**: Executing standard `CREATE INDEX` locks writes on PostgreSQL/MySQL tables during index construction. Always use **`CREATE INDEX CONCURRENTLY`** (PostgreSQL) or **`LOCK=NONE`** (MySQL).
- **Cutover Before Backfill Completion**: Switching application reads to `new_column` before backfill reaches 100% returns `NULL` values for historical records. The engine blocks phase advancement if backfill is incomplete.
- **Skipping Dual-Write Phase**: Deploying new code that reads from `new_column` before dual-write is live causes missing writes during application deployment rollouts.

## Verification

Run the unit test suite to validate DDL generation across PostgreSQL/MySQL, 5-Phase Expand-Contract state transitions, batched backfill progress tracking, and cutover safety enforcement:

```bash
python -m unittest discover -s skills/zero-downtime-database-schema-migrations/scripts
```

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `withdrawal-velocity-limits-and-anomaly-detection`
- `uk-fca-algorithmic-trading-systems-controls`
- `tick-to-trade-latency-measurement`
