# Institutional Zero-Downtime Schema Migration Standards

## 1. 5-Phase Expand-Contract Migration Standard

Lock levels below are the **documented** levels, not marketing ones. "Brief" means the
statement is fast, not that it is lock-free: a fast statement that cannot get its lock
blocks everything queued behind it.

| Migration Phase | DDL / Code Action | Database Lock Level | App Read / Write State | Rollback Safety Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1: Expand** | `ALTER TABLE tbl ADD COLUMN col_new TYPE DEFAULT NULL;` | PG: **ACCESS EXCLUSIVE**, brief (catalogue-only for a non-volatile default). MySQL: exclusive metadata lock, brief with `ALGORITHM=INSTANT`. CRDB: no data locks (async job). | Reads `col_old`, Writes `col_old` | Drop `col_new` immediately |
| **Phase 2: Dual-Write** | Deploy app code performing dual writes | None (no DDL) | Reads `col_old`, Writes `col_old` AND `col_new` | Revert code to single-write |
| **Phase 3: Backfill** | Asynchronous batched backfill script | Row-level locks per batch (1,000-5,000 rows) | Reads `col_old`, Writes Dual | Stop backfill job; backfilled rows remain valid |
| **Phase 4: Read Cutover** | Deploy app code reading from `col_new` | None (no DDL) | Reads `col_new`, Writes Dual | Revert code to read `col_old` |
| **Phase 5: Contract** | `ALTER TABLE tbl DROP COLUMN col_old;` | PG: **ACCESS EXCLUSIVE**. MySQL: exclusive metadata lock; **INPLACE rebuilds the table**, INSTANT does not. CRDB: async job. | Reads `col_new`, Writes `col_new` | **Irreversible.** Restore from backup — the data is gone from the live table. |

**ACCESS EXCLUSIVE is not a footnote.** PostgreSQL: "An `ACCESS EXCLUSIVE` lock is acquired
unless explicitly noted" for `ALTER TABLE`, and that mode "conflicts with locks of all modes
(`ACCESS SHARE`, ...)" — including the `ACCESS SHARE` every `SELECT` takes. A transaction
seeking a conflicting lock "will wait indefinitely for conflicting locks to be released".
Bound it or it is not a zero-downtime migration.

---

## 2. Database Engine Non-Blocking DDL Syntax Standard

### PostgreSQL 11+
- **Bound the lock first**: `SET lock_timeout = '3s';` — then retry on timeout rather than removing the bound.
- **Nullable column addition**: `ALTER TABLE tbl ADD COLUMN col_new TYPE DEFAULT NULL;`
  - A **non-volatile** `DEFAULT` is stored in the table's metadata and "in neither case is a rewrite of the table required". A **volatile** default (`random()`, `gen_random_uuid()`, `clock_timestamp()`) "will require the entire table and its indexes to be rewritten".
- **Lock-free index build**: `CREATE INDEX CONCURRENTLY idx_tbl_col ON tbl (col_new);`
  - **Cannot run inside a transaction block.** Performs two table scans and waits for existing transactions to terminate.
  - On failure it "leave[s] behind an 'invalid' index" which "will be ignored for querying purposes ... however it will still consume update overhead". Recover with `DROP INDEX CONCURRENTLY` and retry, or `REINDEX INDEX CONCURRENTLY`.
- **Column drop**: `ALTER TABLE tbl DROP COLUMN col_old;` — does not physically remove data or reclaim space; "the space will be reclaimed over time as existing rows are updated".

### MySQL 8.0 (InnoDB)
- **Bound the metadata-lock wait first**: `SET SESSION lock_wait_timeout = 3;` — the default is **31536000 seconds (one year)**, range 1-31536000, and it applies to DDL.
- **Column addition**: `ALTER TABLE tbl ADD COLUMN col_new TYPE DEFAULT NULL, ALGORITHM=INSTANT;`
  - `INSTANT` is the default algorithm as of **8.0.12** and only modifies metadata.
  - **Only `LOCK=DEFAULT` is permitted with `ALGORITHM=INSTANT`** — do not append `LOCK=NONE`.
  - Fallback where INSTANT is refused: `..., ALGORITHM=INPLACE, LOCK=NONE;`
- **Column drop**: `ALTER TABLE tbl DROP COLUMN col_old, ALGORITHM=INSTANT;`
  - `INSTANT` is the default as of **8.0.29**. The `INPLACE` form **rebuilds the table** (it permits concurrent DML, but pay for the I/O knowingly).
- **Secondary index build**: `CREATE INDEX idx_tbl_col ON tbl (col_new) ALGORITHM=INPLACE LOCK=NONE;`
  - There is **no `INSTANT` form** for a secondary index: in place, no table rebuild, concurrent DML permitted.
  - **No comma before the options.** The grammar is `ON tbl_name (key_part,...) [index_option] [algorithm_option | lock_option]`. `CREATE INDEX ... (col), ALGORITHM=INPLACE` is a **syntax error**; the comma-separated form belongs to `ALTER TABLE` only.
- **INSTANT is refused** on `ROW_FORMAT=COMPRESSED` tables, tables with FULLTEXT indexes, columns with functional indexes, and once a table reaches **64 row versions** (`ERROR 4092`; check `INFORMATION_SCHEMA.INNODB_TABLES.TOTAL_ROW_VERSIONS`).

### CockroachDB
- Schema changes are **asynchronous background jobs** run "without holding locks on the underlying table data". `CREATE INDEX` is already online — there is no `CONCURRENTLY` keyword to add.
- The statement returns a job ID **before the change is applied**. Poll the job; do not treat statement success as schema success.
- "Most schema changes should not be performed within an explicit transaction with multiple statements, as they do not have the same atomicity guarantees as other SQL statements" — DDL "can fail while other statements succeed".
- **Phase 5 hazard**: "Some schema changes that drop columns cannot be rolled back properly. In some cases, the rollback will succeed, but the column data might be partially or totally missing." Take a verified backup before contracting.

---

## 3. Safety Verification Thresholds
- **Batch size**: 1,000-5,000 rows per backfill transaction. Larger batches hold row locks and generate replication volume for longer; the engine warns above `max_batch_size` rather than failing, because the operator may have justified it.
- **Replica lag budget**: default **1.0 s**. Above budget the backfill withholds the batch entirely (`BackfillDirective.THROTTLE`) rather than applying it and pausing afterwards. A missing or NaN lag reading is an error, never an implicit zero.
- **Cutover prerequisite**: `SELECT count(*) FROM tbl WHERE col_new IS NULL` **on the primary** must return **0**. The backfill percentage is a pacing signal, not evidence — `total_records` is a plan-time snapshot that excludes rows written before dual-write finished rolling out.
- **Identifier limits**: 63 bytes. PostgreSQL "uses no more than NAMEDATALEN-1 bytes of an identifier; longer names ... will be truncated" **silently**, so a generated `idx_<table>_<column>` over the limit can collide with another. Identifiers are validated, not quoted: quoting in PostgreSQL "makes it case-sensitive, whereas unquoted names are always folded to lower case", so quoting a mixed-case plan value would address a different object than the application does.

---

## 4. Sources

| Claim | Source |
| :--- | :--- |
| ALTER TABLE lock level; non-volatile vs volatile DEFAULT; DROP COLUMN space reclamation | PostgreSQL 17 docs, [ALTER TABLE](https://www.postgresql.org/docs/17/sql-altertable.html) (Description, Notes) |
| ACCESS EXCLUSIVE conflicts with all modes; indefinite waiting | PostgreSQL 17 docs, [Explicit Locking §13.3.1](https://www.postgresql.org/docs/17/explicit-locking.html) |
| CONCURRENTLY: no transaction block, two scans, invalid index, recovery | PostgreSQL 17 docs, [CREATE INDEX — Building Indexes Concurrently](https://www.postgresql.org/docs/17/sql-createindex.html) |
| Identifier length truncation; unquoted identifiers folded to lower case | PostgreSQL 17 docs, [Lexical Structure §4.1.1](https://www.postgresql.org/docs/17/sql-syntax-lexical.html) |
| CREATE INDEX grammar (no comma before ALGORITHM/LOCK) | MySQL 8.0 Reference Manual, [CREATE INDEX](https://dev.mysql.com/doc/refman/8.0/en/create-index.html) |
| INSTANT defaults (8.0.12 add / 8.0.29 drop), INPLACE table rebuild on drop, 64 row versions / ERROR 4092, secondary index has no INSTANT form | MySQL 8.0 Reference Manual, [Online DDL Operations §17.12.1](https://dev.mysql.com/doc/refman/8.0/en/innodb-online-ddl-operations.html) |
| Only LOCK=DEFAULT permitted with ALGORITHM=INSTANT | MySQL 8.0 Reference Manual, [ALTER TABLE](https://dev.mysql.com/doc/refman/8.0/en/alter-table.html) |
| DDL requires an exclusive metadata lock and blocks behind open transactions | MySQL 8.0 Reference Manual, [Metadata Locking §10.11.4](https://dev.mysql.com/doc/refman/8.0/en/metadata-locking.html) |
| lock_wait_timeout default 31536000 s, range 1-31536000, applies to DDL | MySQL 8.0 Reference Manual, Server System Variables — `lock_wait_timeout` |
| Async background jobs, no data locks, transaction caveats, unrecoverable column drops | Cockroach Labs docs, [Online Schema Changes](https://docs.cockroachlabs.com/docs/stable/online-schema-changes) |
