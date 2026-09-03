# Institutional Zero-Downtime Migration Workflows

## Workflow 1: 5-Phase Expand-Contract Migration Sequence

```mermaid
sequenceDiagram
    autonumber
    participant DBA as Migration Engine / DDL
    participant DB as Production SQL Primary
    participant App as Algo Trading Application Engine

    DBA->>DB: SET lock_timeout / lock_wait_timeout (bound the wait)
    DBA->>DB: Phase 1: ADD COLUMN new_col TYPE DEFAULT NULL
    Note over DBA,DB: Brief ACCESS EXCLUSIVE (PG) / exclusive MDL (MySQL).<br/>On timeout: retry later. Never remove the bound.
    DB-->>DBA: Column added

    DBA->>DB: CREATE INDEX CONCURRENTLY (outside any transaction block)
    Note over DBA,DB: On failure an INVALID index remains.<br/>DROP INDEX CONCURRENTLY, then rebuild.

    DBA->>App: Phase 2: Deploy dual-write code
    App->>DB: Writes to BOTH old_col and new_col
    Note over DBA,App: Confirm EVERY instance is on dual-write before Phase 3.<br/>Rows from the last stale instance are the ones the gate catches.

    DBA->>DB: Phase 3: Batched backfill, paced on replica lag
    DB-->>DBA: Backfill counter exhausted

    DBA->>DB: SELECT count(*) WHERE new_col IS NULL  (on the PRIMARY)
    DB-->>DBA: residual_null_rows
    Note over DBA,DB: Must be 0. A 100% counter is not evidence:<br/>total_records was a plan-time snapshot.

    DBA->>App: Phase 4: Deploy read-cutover code
    App->>DB: Reads from new_col, still writes both

    Note over DBA,App: Soak one full rollback window with old_col still populated.

    DBA->>DB: Phase 5: DROP COLUMN old_col + remove dual-write code
    Note over DBA,DB: Irreversible. Verified backup required beforehand.
    DB-->>DBA: Migration complete
```

---

## Workflow 2: Asynchronous Batched Backfill Execution Pipeline

The order matters: lag is checked **before** the batch is applied, not after. Applying the
batch and then pausing means the batch that pushed lag over budget has already been sent.

```mermaid
flowchart TD
    A[Initiate Phase 3 backfill job] --> F[Read replica lag]
    F --> G{Lag reading available?}
    G -- No / NaN --> Z[STOP: pacing blind. Fix monitoring.<br/>A missing reading is not zero lag.]
    G -- Yes --> H{Lag > budget 1.0s?}

    H -- Yes --> T[THROTTLE: withhold batch, sleep 5s]
    T --> F

    H -- No --> B["Fetch unpopulated batch<br/>(WHERE new_col IS NULL LIMIT 1000-5000)"]
    B --> C{Rows returned?}
    C -- Yes --> E["UPDATE tbl SET new_col = old_col<br/>WHERE id IN batch"]
    E --> I[Increment counter, sleep 100ms]
    I --> F

    C -- No --> D["Counter exhausted -> BackfillDirective.COMPLETE"]
    D --> V["Run verification on the PRIMARY:<br/>SELECT count(*) WHERE new_col IS NULL"]
    V --> W{residual_null_rows = 0?}
    W -- No --> X[Cutover BLOCKED.<br/>Confirm dual-write is live on every instance,<br/>then backfill the remainder.]
    X --> B
    W -- Yes --> Y[Advance to PHASE_4_READ_CUTOVER]
```

---

## Workflow 3: Failure and Rollback Decision Tree

```mermaid
flowchart TD
    P{Which phase failed?} --> P1[Phase 1 Expand]
    P --> P24[Phase 2-4]
    P --> P5[Phase 5 Contract]

    P1 --> P1a{DDL timed out on its lock?}
    P1a -- Yes --> P1b[Working as designed.<br/>Find the blocking transaction, retry in a quieter window.]
    P1a -- No --> P1c[Drop new_col. Nothing depends on it yet.]

    P24 --> P24a["rollback_migration_phase(plan, reason)"]
    P24a --> P24b[Redeploy the previous phase's application code.<br/>new_col keeps receiving writes; nothing is destroyed.<br/>Backfill counter is preserved deliberately.]

    P5 --> P5a[NO ROLLBACK PATH.<br/>old_col data is gone from the live table.]
    P5a --> P5b[Restore from backup:<br/>database-backup-and-point-in-time-restore-testing]
```

**Why Phases 2-4 are cheap to reverse and Phase 5 is not.** Through Phase 4 both columns are
populated and both are readable, so reverting is a code deploy. Phase 5 removes that
redundancy. This is why the soak in step 8 of the workflow exists: it is the last window in
which a bug in the new read path costs a deploy rather than a restore.

---

## Workflow 4: MySQL Algorithm Selection

```mermaid
flowchart TD
    S[MySQL 8.0 schema change] --> A{Operation?}

    A -- ADD COLUMN --> B{Server >= 8.0.12?}
    B -- No --> F[ALGORITHM=INPLACE, LOCK=NONE]
    B -- Yes --> C{"INSTANT eligible?<br/>(not ROW_FORMAT=COMPRESSED,<br/>no FULLTEXT index,<br/>TOTAL_ROW_VERSIONS < 64)"}
    C -- Yes --> D["ALGORITHM=INSTANT (no LOCK clause:<br/>only LOCK=DEFAULT is permitted)"]
    C -- No --> F

    A -- DROP COLUMN --> G{Server >= 8.0.29?}
    G -- No --> H[ALGORITHM=INPLACE, LOCK=NONE<br/>-> REBUILDS THE TABLE]
    G -- Yes --> C

    A -- CREATE INDEX --> I["ALGORITHM=INPLACE LOCK=NONE<br/>(no INSTANT form exists;<br/>NO COMMA before the options)"]

    F --> J{Table large enough that a<br/>rebuild is unacceptable?}
    H --> J
    J -- Yes --> K[Use gh-ost or pt-online-schema-change<br/>for throttling and a controlled cutover]
    J -- No --> L[Apply behind lock_wait_timeout]
```
