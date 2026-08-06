# Institutional SEC Regulation SHO Compliance Workflows

## Workflow 1: Pre-Trade Short Sale Locate & Order Marking Gate
```mermaid
sequenceDiagram
    autonumber
    participant Trader as Trading System / Algo
    participant Engine as Reg SHO Compliance Engine
    participant LocateDB as Prime Broker Locate Store
    participant Exchange as US Trading Venue (Exchange/ATS)

    Trader->>Engine: Submit Order Intent (Symbol, Qty, Price, Marking, Locate ID)
    
    alt Marking == LONG
        Engine-->>Exchange: Route Order Marked LONG (No Locate Required)
    else Marking == SHORT OR SHORT_EXEMPT
        Engine->>LocateDB: Validate Locate ID (Symbol Match, Expiration, Rem Qty)
        alt Locate Invalid / Expired / Insufficient Qty
            LocateDB-->>Engine: Locate Validation Failed
            Engine-->>Trader: REJECT ORDER (Rule 203 Violation)
        else Locate Valid
            alt Rule 201 SSR Active AND Marking == SHORT
                alt Order Price <= NBB
                    Engine-->>Trader: REJECT ORDER (Rule 201 SSR Uptick Violation: Price <= NBB)
                else Order Price > NBB
                    Engine->>LocateDB: Deduct Order Qty from Locate Pool
                    Engine-->>Exchange: Route Order Marked SHORT
                end
            else Marking == SHORT_EXEMPT OR SSR Inactive
                Engine->>LocateDB: Deduct Order Qty from Locate Pool
                Engine-->>Exchange: Route Order Marked SHORT / SHORT_EXEMPT
            end
        end
    end
```

---

## Workflow 2: Rule 201 SSR Circuit Breaker Lifecycle Workflow
```mermaid
flowchart TD
    A[Monitor Real-Time Intraday Price Change] --> B{Intraday Drop >= 10% vs Prior Close?}
    
    B -- Yes --> C[Invoke trigger_rule_201_ssr(symbol)]
    B -- No --> D[Maintain Normal Short Sale Processing]
    
    C --> E[Enforce Rule 201 Alternative Uptick Test: Short Price > NBB]
    E --> F{Order Marked SHORT_EXEMPT?}
    
    F -- Yes --> G[Bypass Uptick Test: Route Short Sale at or below NBB]
    F -- No --> H{Order Price > NBB?}
    
    H -- Yes --> I[Approve & Route Order Marked SHORT]
    H -- No --> J[REJECT ORDER: Rule 201 SSR Price Test Failure]
```

