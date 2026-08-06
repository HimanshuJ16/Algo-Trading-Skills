# Institutional SEC Regulation NMS Rule 611 Workflows

## Workflow 1: Real-Time Trade-Through Compliance Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Exec as Execution Gateway / SOR
    participant Engine as Reg NMS Rule 611 Engine
    participant Feed as SIP / Direct NBBO Feed
    participant Audit as FINRA CAT Compliance Store

    Exec->>Engine: Submit Execution Record (Price, Qty, Side, FIX Tags)
    Engine->>Feed: Fetch Automated Protected NBBO (Excluding Self-Help Venues)
    Feed-->>Engine: Return Protected NBB & NBO Prices

    alt Tagged as Intermarket Sweep Order (ISO)
        Engine->>Audit: Log EXEMPT_ISO (Rule 611(b)(5/6))
    else Tagged as Benchmark / VWAP
        Engine->>Audit: Log EXEMPT_BENCHMARK (Rule 611(b)(7))
    else Price > NBO (Buy) OR Price < NBB (Sell)
        alt Quote Updated Within 1.0 Sec
            Engine->>Audit: Log EXEMPT_FLICKERING_QUOTE (Rule 611(b)(8))
        else No Exemption Applies
            Engine->>Audit: Log TRADE_THROUGH_VIOLATION (Report to Compliance Officer)
        end
    else Price <= NBO (Buy) AND Price >= NBB (Sell)
        Engine->>Audit: Log COMPLIANT_NO_TRADE_THROUGH
    end
```

---

## Workflow 2: Venue Self-Help Exemption Lifecycle (Rule 611(b)(1))
```mermaid
flowchart TD
    A[Monitor Venue Response Latency] --> B{Venue Response Time > 1.0 sec OR Outage?}
    
    B -- Yes --> C[Invoke declare_self_help(venue_id, reason)]
    B -- No --> D[Maintain Standard Protected NBBO Ingestion]
    
    C --> E[Exclude Venue Quotes from Protected NBBO Calculations]
    E --> F[Broadcast Self-Help Declaration to Smart Order Routers]
    
    F --> G[Monitor Venue Recovery & Latency Restoration]
    G --> H{Venue Performance Normal (< 1.0 sec)?}
    
    H -- Yes --> I[Invoke revoke_self_help(venue_id)]
    I --> J[Re-include Venue Quotes in Protected NBBO]
    H -- No --> G
```

