# Tel Aviv Stock Exchange (TASE) Institutional Integration Workflows

## Workflow 1: FIX Session Lifecycle & Sequence Recovery
```mermaid
sequenceDiagram
    autonumber
    participant Algo as Quant Trading Engine
    participant Engine as TASE Integration Engine
    participant Gateway as TASE FIX Gateway
    
    Algo->>Engine: connect()
    Engine->>Gateway: TCP Handshake & TLS Establish
    Engine->>Gateway: FIX Logon (MsgType=A, TargetSeqNum=1)
    Gateway-->>Engine: FIX Logon Ack (MsgType=A)
    Engine-->>Algo: Session Active

    loop Heartbeat Interval (30s)
        Engine->>Gateway: Heartbeat (MsgType=0)
        Gateway-->>Engine: Heartbeat (MsgType=0)
    end

    alt Sequence Gap Detected
        Gateway-->>Engine: ResendRequest (MsgType=2, BeginSeqNo, EndSeqNo)
        Engine->>Gateway: SequenceReset / Re-transmit Messages
    end

    Algo->>Engine: disconnect()
    Engine->>Gateway: FIX Logout (MsgType=5)
    Gateway-->>Engine: FIX Logout Ack (MsgType=5)
```

---

## Workflow 2: Order Placement & Execution Processing Pipeline
```mermaid
flowchart TD
    A[Strategy Signal Generated] --> B{Pre-Trade Risk Engine}
    B -- Max Value Breach --> C[Reject & Alert Risk Manager]
    B -- Price Collar Breach --> C
    B -- Valid Order --> D[Price Denomination Scaling]
    
    D -->|Equity| E[Convert ILS Model Price to Agorot]
    D -->|Bond / Makam| F[Format Price as % of Par]
    
    E --> G[Construct NewOrderSingle MsgType=D]
    F --> G
    
    G --> H[Transmit over FIX Session]
    H --> I[Store Order in Local State Machine]
    
    GatewayResponse[Receive ExecutionReport MsgType=8] --> J{ExecType}
    J -- New --> K[Update Status = NEW]
    J -- Partial Fill --> L[Update VWAP & Filled Quantity]
    J -- Fill --> M[Update Status = FILLED]
    J -- Canceled --> N[Update Status = CANCELED]
    J -- Rejected --> O[Trigger Rejection Recovery & Log Text Tag]
```

---

## Workflow 3: Daily Reference Data & Security Master Ingestion
1. **Cron Ingestion (07:00 IST)**: Trigger REST request to TASE Data Hub API (`GET /api/v1/securities`).
2. **Security ID & ISIN Cross-Reference**: Extract TASE Security Number (e.g. `1082511`), ISIN (`IL0001082511`), Instrument Class (`EQUITY`/`BOND`/`MAKAM`), and Base Price Denomination (`AGOROT` vs `ILS`).
3. **Reference Price Calibration**: Extract previous EOD settlement price and calculate dynamic upper/lower price collars (±10%).
4. **Register Security Master**: Hydrate local `TASESecurity` dictionary inside the `TASEIntegrationEngine`.

---

## Workflow 4: Sunday Trading Session Handling & Sunday-Mon Transition
1. **Calendar Validation**: Verify target execution date against Israeli calendar (Sunday = Day 6, Trading Active; Friday = Day 4, Weekend Closed).
2. **Timezone Alignment**: Convert strategy timestamps from UTC to IST (Israel Standard Time: UTC+2 Standard / UTC+3 DST).
3. **Session Phase Tracking**:
   - `08:30 IST`: Transition engine to `PRE_OPEN` phase. Allow order entry, modification, and cancellation without execution.
   - `09:50 IST`: Transition engine to `OPENING_AUCTION`. Hold new orders; await opening uncrossing Execution Reports.
   - `10:00 IST`: Transition engine to `CONTINUOUS_TRADING`. Enable active algo order slicing (TWAP/VWAP).
   - `15:50 IST (Sunday) / 17:15 IST (Mon-Thu)`: Transition engine to `CLOSING_AUCTION`.
   - `16:00 IST (Sunday) / 17:25 IST (Mon-Thu)`: Transition engine to `CLOSED`. Reconcile fills, generate daily PnL, and disconnect session.
