# Institutional Weather Derivatives & Niche Instrument Workflows

## Workflow 1: Monthly CME Weather Contract Accumulation & Cash Settlement
```mermaid
sequenceDiagram
    autonumber
    participant Feed as NOAA / Station Weather Data Feed
    participant Engine as Weather Derivatives Engine
    participant Risk as Derivatives Risk Ledger
    participant Clearing as CME Clearing / OTC Counterparty

    Feed->>Engine: Ingest Daily Station (T_min, T_max) Logs for Month
    Engine->>Engine: 1. Accumulate Daily Degree Days (HDD, CDD, or CAT)
    Engine->>Engine: 2. Calculate Final Accumulated Monthly Index Value
    
    Engine->>Engine: 3. Compute Payoff = max(0, Index - Strike) * $20/point
    
    alt Contract specifies Max Payout Cap Limit
        Engine->>Engine: 4. Enforce Cap Limit: min(Payoff, MaxPayoutUSD)
    end
    
    Engine-->>Risk: Return SettlementPayoff (Payoff USD, Capped Flag)
    Risk->>Clearing: Submit Final Cash Settlement Instructions
```

---

## Workflow 2: Burn Analysis Historical Simulation Valuation Pipeline
```mermaid
flowchart TD
    A[Structure OTC Weather Derivative / Swap] --> B[Fetch 20-30 Years Historical Meteorological Station Logs]
    
    B --> C[Calculate Historical Monthly Accumulated Indexes (HDD/CDD)]
    C --> D[Apply Climate Trend Adjustment (Warming Trend detrending)]
    
    D --> E[Compute Contract Payoff for Each Historical Season]
    E --> F[Calculate Mean Expected Payoff & Standard Deviation]
    
    F --> G[Derive Fair Option Premium / Swap Spread]
    G --> H[Export Burn Analysis Audit Report to Risk Committee]
```