# Institutional Zero-Downtime Schema Migration Standards

## 1. 5-Phase Expand-Contract Migration Standard
| Migration Phase | DDL / Code Action | Database Lock Level | App Read / Write State | Rollback Safety Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Phase 1: Expand** | `ALTER TABLE tbl ADD col_new TYPE DEFAULT NULL;` | None (Instant DDL) | Reads `col_old`, Writes `col_old` | Drop `col_new` immediately |
| **Phase 2: Dual-Write** | Deploy app code performing dual writes | None | Reads `col_old`, Writes `col_old` AND `col_new` | Revert code to single-write |
| **Phase 3: Backfill** | Asynchronous batched backfill script | Row-level locks (Batches of 1,000) | Reads `col_old`, Writes Dual | Stop backfill job |
| **Phase 4: Read Cutover** | Deploy app code reading from `col_new` | None | Reads `col_new`, Writes Dual | Revert code to read `col_old` |
| **Phase 5: Contract** | `ALTER TABLE tbl DROP col_old;` | Short metadata lock | Reads `col_new`, Writes `col_new` | Restore column from backup |

---

## 2. Database Engine Non-Blocking DDL Syntax Standard

### PostgreSQL:
- **Nullable Column Addition**: `ALTER TABLE tbl ADD COLUMN col_new TYPE DEFAULT NULL;`
- **Lock-Free Index Build**: `CREATE INDEX CONCURRENTLY idx_tbl_col ON tbl (col_new);`

### MySQL (InnoDB):
- **Lock-Free Column Addition**: `ALTER TABLE tbl ADD COLUMN col_new TYPE DEFAULT NULL, ALGORITHM=INPLACE, LOCK=NONE;`
- **Lock-Free Index Build**: `CREATE INDEX idx_tbl_col ON tbl (col_new), ALGORITHM=INPLACE, LOCK=NONE;`

---

## 3. Safety Verification Thresholds
- **Max Batch Size**: $1,000$ to $5,000$ rows per backfill transaction.
- **Max Backfill Replication Lag**: $< 1.0\ \text{seconds}$ on read replicas during backfill execution.
- **Cutover Prerequisite**: Backfill completion percentage **MUST equal 100.0%**.