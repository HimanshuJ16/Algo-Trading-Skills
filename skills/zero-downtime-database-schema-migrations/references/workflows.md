# Institutional Zero-Downtime Migration Workflows

## Workflow 1: 5-Phase Expand-Contract Migration Sequence
```mermaid
sequenceDiagram
    autonumber
    participant DBA as Migration Engine / DDL
    participant DB as Production SQL Database
    participant App as Algo Trading Application Engine

    DBA->>DB: Phase 1: ADD COLUMN new_col TYPE DEFAULT NULL (Lock-Free)
    DB-->>DBA: Column Added
    
    DBA->>App: Phase 2: Deploy Dual-Write Code (Write to old_col & new_col)
    App->>DB: Application Writes to BOTH old_col and new_col
    
    DBA->>DB: Phase 3: Execute Batched Asynchronous Backfill (UPDATE new_col = old_col)
    DB-->>DBA: Backfill Reaches 100% Complete
    
    DBA->>App: Phase 4: Deploy Read Cutover Code (Read exclusively from new_col)
    App->>DB: Application Reads from new_col
    
    DBA->>DB: Phase 5: DROP COLUMN old_col & Remove Dual-Write Code
    DB-->>DBA: Zero-Downtime Migration Complete
```

---

## Workflow 2: Asynchronous Batched Backfill Execution Pipeline
```mermaid
flowchart TD
    A[Initiate Phase 3 Backfill Job] --> B[Fetch Unpopulated Batch (WHERE new_col IS NULL LIMIT 1000)]
    
    B --> C{Rows Remaining?}
    
    C -- No --> D[Backfill 100% Complete -> Signal Ready for Phase 4 Cutover]
    C -- Yes --> E[Execute UPDATE table SET new_col = old_col WHERE id IN batch]
    
    E --> F[Check Read Replica Replication Lag]
    F --> G{Replication Lag > 1.0s?}
    
    G -- Yes --> H[Throttle / Pause Backfill Engine for 5 Seconds]
    G -- No --> I[Increment Backfilled Counter & Sleep 100ms]
    
    H --> B
    I --> B
```