# Institutional VIX & Volatility Derivative Workflows

## Workflow 1: VIX Term Structure Analysis & Signal Generation
```mermaid
sequenceDiagram
    autonumber
    participant Feed as Market Data Feed (Spot VIX & Futures)
    participant Engine as VIX Strategy Engine
    participant Portfolio as Risk & Margin Manager
    participant OMS as Order Management System

    Feed->>Engine: Ingest Spot VIX, F1 Futures (Price, Expiry), F2 Futures (Price, Expiry)
    Engine->>Engine: 1. Calculate Term Structure Slope = F2 - F1
    Engine->>Engine: 2. Calculate Slope % = (F2 - F1) / F1 * 100
    Engine->>Engine: 3. Classify State (CONTANGO vs BACKWARDATION)
    Engine->>Engine: 4. Compute Annualized Roll Yield % & Daily Dollar Decay
    
    alt State == CONTANGO
        Engine->>Portfolio: Calculate Short F1 Futures Notional (5% Equity Allocation)
        Engine->>OMS: Submit SHORT_F1_VIX_FUTURE Order (Harvest Contango Decay)
    else State == BACKWARDATION
        Engine->>Portfolio: Calculate Tail Risk Protection Allocation (2% Equity)
        Engine->>OMS: Submit LONG_VIX_CALL_SPREAD Order (Crash Protection)
    else State == FLAT
        Engine->>Engine: Maintain NEUTRAL / CASH Position
    end
```

---

## Workflow 2: Monthly VIX Futures Roll Protocol
```mermaid
flowchart TD
    A[Monitor Active Front-Month VIX Future F1] --> B{Days to Expiration D_expiry <= 3 Days?}
    
    B -- No --> C[Maintain Active Position & Track Daily Roll Decay]
    B -- Yes --> D[Initiate Futures Calendar Roll Protocol]
    
    D --> E[Buy Back Short F1 Contract / Sell Long F1 Contract]
    E --> F[Establish New Position in F2 Contract (New Front-Month)]
    
    F --> G[Update Portfolio Position Registers & Reset Expiry Timers]
```