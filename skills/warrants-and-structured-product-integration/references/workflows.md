# Institutional Warrants & Structured Product Workflows

## Workflow 1: Warrant Valuation & Greeks Calculation Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Feed as Market Data Feed (Spot & Vol Surface)
    participant Engine as Warrants Integration Engine
    participant TermSheet as Warrant Master Register
    participant Risk as Derivatives Risk Ledger

    Feed->>Engine: Ingest Spot Price S, Volatility Sigma, Risk-Free Rate r
    TermSheet->>Engine: Fetch Contract Specs (Type, Strike K, Barrier B, R_ent, Expiry)
    
    alt Warrant Type == TURBO_BULL / BEAR AND Spot Breaches Barrier
        Engine->>Engine: Mark Status = KNOCKED_OUT, Fair Price = $0.00, Delta = 0.0
        Engine-->>Risk: Trigger Mandatory Call Event (MCE) Termination
    else Active Warrant Contract
        Engine->>Engine: 1. Calculate Black-Scholes d1, d2
        Engine->>Engine: 2. Scale Fair Price & Greeks by Entitlement Ratio R_ent
        Engine->>Engine: 3. Calculate Simple Gearing & Effective Gearing
        Engine-->>Risk: Return WarrantValuation (Price, Delta, Gamma, Effective Gearing)
    end
```

---

## Workflow 2: Market Maker Delta-Neutral Rebalancing Pipeline
```mermaid
flowchart TD
    A[Monitor Active Warrant Position] --> B[Fetch Latest Spot Price & Compute Warrant Delta]
    
    B --> C[Calculate Required Underlying Shares: N_warrants * Delta]
    C --> D[Calculate Net Rebalance: Required Shares - Currently Hedged Shares]
    
    D --> E{Abs(Net Rebalance) >= Threshold (1.0 Share)?}
    
    E -- No --> F[Maintain Current Hedge (Action: HOLD)]
    E -- Yes --> G{Net Rebalance > 0?}
    
    G -- Yes --> H[Submit BUY Order for Underlying Equity]
    G -- No --> I[Submit SELL Order for Underlying Equity]
    
    H --> J[Update Hedged Position Registers]
    I --> J
```