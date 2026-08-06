# Institutional UK FCA RTS 6 & FG18/9 Control Workflows

## Workflow 1: Pre-Trade Control Execution Pipeline (RTS 6 Article 13)
```mermaid
sequenceDiagram
    autonumber
    participant Algo as Trading Strategy / Algorithm
    participant Engine as FCA RTS 6 Controls Engine
    participant Venue as Trading Venue (LSE / Cboe)
    participant Log as Compliance Audit Log

    Algo->>Engine: Submit Order Intent (Order ID, Symbol, Qty, Price)
    
    Engine->>Engine: 1. Check Active Kill Switch (Art 12)
    Engine->>Engine: 2. Check System Capacity (Art 14)
    Engine->>Engine: 3. Check Price Collar vs NBBO (Art 13(1))
    Engine->>Engine: 4. Check Max Notional (£500k) & Volume (Art 13(2))
    Engine->>Engine: 5. Check Order-to-Trade Ratio OTR (Art 13(3))
    Engine->>Engine: 6. Check Counterparty Credit Limits (Art 13(4))

    alt All Controls Passed
        Engine->>Venue: Transmit FIX Order to Exchange
        Engine->>Log: Log Status = PASSED
    else Any Control Failed
        Engine-->>Algo: Reject Order (Return Violation Reason)
        Engine->>Log: Log Status = REJECTED (Log Violation Type & Timestamp)
    end
```

---

## Workflow 2: Automated Emergency Kill Switch Lifecycle (RTS 6 Article 12)
```mermaid
flowchart TD
    A[Monitor Market Risk & Algorithm Health] --> B{Emergency Event Triggered?}
    
    B -- Runaway Algo / Market Volatility --> C[Invoke trigger_kill_switch(algo_id, reason)]
    B -- System Capacity > 95% --> C
    
    C --> D[Set Active Kill Switch Flag in Memory]
    D --> E[Block All Subsequent Pre-Trade Orders for Target Algo]
    
    E --> F[Send FIX Mass Cancel Order to Exchange Gateways]
    F --> G[Cancel All Open Orders Across All Venues]
    
    G --> H[Notify SMF24 / SMF16 Compliance Officer]
    H --> I[Conduct Compliance Investigation]
    I --> J[Execute reset_kill_switch() Following Sign-Off]
```