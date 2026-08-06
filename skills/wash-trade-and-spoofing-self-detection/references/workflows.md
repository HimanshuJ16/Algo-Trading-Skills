# Institutional Market Abuse Surveillance Workflows

## Workflow 1: Real-Time Wash Trade Self-Match Prevention (SMP)
```mermaid
sequenceDiagram
    autonumber
    participant Trader as Algo Trading Strategy
    participant Gateway as Pre-Trade Risk Gateway
    participant Engine as Wash Trade Detection Engine
    participant Book as Central Limit Order Book (CLOB)

    Trader->>Gateway: Submit New Order (TraderId, Symbol, Side, Price, Qty)
    Gateway->>Engine: Evaluate check_wash_trade_self_cross(Order)
    Engine->>Engine: Scan Active Order Book for Same Trader/Account & Opposite Side @ Same Price
    
    alt Self-Cross Match Detected (Wash Trade Risk)
        Engine-->>Gateway: Return CRITICAL Violation (VIOL-WASH)
        Gateway->>Gateway: Trigger Self-Match Prevention (Cancel Incoming / Cancel Resting)
        Gateway-->>Trader: Reject Order & Issue Compliance Alert
    else No Self-Cross Match
        Gateway->>Book: Route Order to Exchange CLOB
    end
```

---

## Workflow 2: Spoofing & Layering Post-Trade Analysis Pipeline
```mermaid
flowchart TD
    A[Ingest Order Fill Event] --> B[Retrieve Trader Event Log History]
    
    B --> C[Scan for Opposite-Side Cancellations within 1,000 ms Window]
    C --> D{Opposite-Side Cancellations Found?}
    
    D -- No --> E[Mark Fill as Compliant & Update Metrics]
    D -- Yes --> F[Calculate Canceled Volume & Average Order Lifespan]
    
    F --> G{Canceled Order Lifespan < 1,000 ms?}
    
    G -- Yes --> H[Generate CRITICAL Spoofing / Layering Violation Alert]
    G -- No --> I[Log Warning for Compliance Review]
    
    H --> J[Notify Compliance Officer & Trigger Algorithmic Execution Pause]
```