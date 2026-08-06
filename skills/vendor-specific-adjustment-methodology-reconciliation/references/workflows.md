# Institutional Vendor Corporate Action Adjustment Workflows

## Workflow 1: Historical Price Adjustment & Factor Calculation Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Data as Historical Price Storage
    participant Engine as Adjustment Reconciliation Engine
    participant CA as Corporate Actions Database
    participant Output as Quant Feature Store

    Data->>Engine: Ingest Raw Price Bars (Open, High, Low, Close, Volume)
    CA->>Engine: Ingest Corporate Actions (Dividends, Splits, Spin-offs)
    
    Engine->>Engine: 1. Match Corporate Actions to Ex-Dates & Cum-Prices
    Engine->>Engine: 2. Calculate Period Adjustment Factors (f_div, f_split)
    Engine->>Engine: 3. Compute Cumulative Backward Product Factors (F_t)
    Engine->>Engine: 4. Multiply Prices (P_raw * F_t) & Divide Volume (V_raw / F_t)
    
    Engine-->>Output: Store Vendor-Conforming Adjusted Price Series
```

---

## Workflow 2: Cross-Vendor Adjustment Reconciliation Pipeline
```mermaid
flowchart TD
    A[Initiate Cross-Vendor Price Reconciliation] --> B[Fetch Adjusted Price Series from Vendor A (e.g. CRSP)]
    A --> C[Fetch Adjusted Price Series from Vendor B (e.g. Bloomberg)]
    
    B --> D[Align Common Calendar Dates & Match Closing Prices]
    C --> D
    
    D --> E[Calculate Percentage Difference: |P_a - P_b| / Mean * 100]
    E --> F{Percentage Difference > Tolerance (0.5%)?}
    
    F -- No --> G[Log Date PASSED]
    F -- Yes --> H[Flag ReconciliationDivergence Anomaly]
    
    G --> I[Compile ReconciliationReport]
    H --> I
    
    I --> J{Report Status PASSED?}
    J -- Yes --> K[Approve Series for Quant Backtesting & Alpha Models]
    J -- No --> L[Quarantine Divergent Symbol & Trigger Data Audit Alert]
```

