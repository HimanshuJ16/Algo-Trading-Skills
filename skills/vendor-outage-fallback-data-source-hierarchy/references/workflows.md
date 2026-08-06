# Institutional Market Data Vendor Fallback Workflows

## Workflow 1: Real-Time Tick Ingestion & Automated Failover Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Feed as Market Data Ingestion Service
    participant Engine as Vendor Fallback Hierarchy Engine
    participant P1 as Priority 1 (Bloomberg B-PIPE)
    participant P2 as Priority 2 (Refinitiv Elektron)
    participant Cache as In-Memory Synthetic Cache

    Feed->>Engine: Request Market Tick for Symbol (e.g. AAPL)
    Engine->>Engine: Evaluate Node Health (Staleness & Error Thresholds)
    
    alt Priority 1 Healthy
        Engine->>P1: Fetch Tick from Primary Feed
        P1-->>Engine: Return Tick (Price, Volume)
        Engine->>Cache: Update Synthetic Cache with Live Tick
        Engine-->>Feed: Return MarketDataTick (Source: P1)
    else Priority 1 Stale / Error -> Failover to Priority 2
        Engine->>Engine: Log FAILOVER_EVENT (P1 -> P2)
        Engine->>P2: Fetch Tick from Secondary Feed
        P2-->>Engine: Return Tick (Price, Volume)
        Engine->>Cache: Update Synthetic Cache with Live Tick
        Engine-->>Feed: Return MarketDataTick (Source: P2)
    else All Live Feeds Stale / Disconnected
        Engine->>Engine: Log FAILOVER_EVENT (Live -> Synthetic Cache)
        Engine->>Cache: Fetch Last Known Tick
        Cache-->>Engine: Return Cached Price
        Engine-->>Feed: Return MarketDataTick (is_synthetic=True)
    end
```

---

## Workflow 2: Anti-Flapping Recovery Cooling & Primary Restoration
```mermaid
flowchart TD
    A[Failover Active: Operating on Secondary Feed] --> B[Heartbeat Received on Primary Feed]
    
    B --> C[Set Primary Status to HEALTHY]
    C --> D{Elapsed Time since Last Failover >= Recovery Cooling Period?}
    
    D -- No (Cooling Active) --> E[Maintain Secondary Feed as Active Source]
    E --> F[Log Cooling Active Message]
    
    D -- Yes (Cooling Expired) --> G[Switch Active Source Back to Primary]
    G --> H[Update Engine State to PRIMARY_ACTIVE & Log Event]
```

