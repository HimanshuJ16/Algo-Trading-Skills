# Institutional Variance Swap & Volatility Derivatives Workflows

## Workflow 1: Fair Variance Strike ($K_{\text{var}}$) Static Replication Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Feed as Market Data Feed (Spot & Option Chain)
    participant Engine as Variance Swap Pricing Engine
    participant Replicator as Log-Contract Replicator
    participant Risk as Portfolio Risk Store

    Feed->>Engine: Ingest Spot Price S_0, Risk-Free Rate r, Time T, Option Quotes Q(K_i)
    Engine->>Engine: Compute Forward Price F_0 = S_0 * e^(r*T)
    
    Engine->>Replicator: Partition Option Strip into OTM Puts (K < F_0) & OTM Calls (K >= F_0)
    Replicator->>Replicator: Discretize Strike Spacing Delta_K_i = (K_{i+1} - K_{i-1}) / 2
    Replicator->>Replicator: Integrate (Delta_K_i / K_i^2) * Q(K_i) Across Option Strip
    
    Replicator-->>Engine: Return Raw Integrated Variance & Log Adjustment
    Engine->>Engine: Compute Fair Variance Strike K_var & Volatility Strike K_vol
    
    Engine-->>Risk: Store Fair Strikes K_var, K_vol, and Convexity Adjustment
```

---

## Workflow 2: Seasoned Variance Swap Mark-to-Market (MTM) Valuation
```mermaid
flowchart TD
    A[Initiate Seasoned Contract MTM Valuation] --> B[Fetch Contract Specs: N_vega, K_vol_initial, T]
    
    B --> C[Compute Variance Notional N_var = N_vega / 2*K_vol_initial]
    B --> D[Calculate Realized Variance so far: (252/N) * SUM( ln(S_i / S_{i-1})^2 )]
    B --> E[Calculate Fair Remaining Variance Strike K_var_remaining via Option Strip]
    
    C --> F[Blend Expected Total Variance: V_exp = (t/T)*Var_realized + ((T-t)/T)*K_var_rem]
    D --> F
    E --> F
    
    F --> G[Compute PV Difference: MTM = e^(-r*(T-t)) * N_var * (V_exp - K_var_initial)]
    G --> H[Update Risk Ledger & Margining Systems]
```