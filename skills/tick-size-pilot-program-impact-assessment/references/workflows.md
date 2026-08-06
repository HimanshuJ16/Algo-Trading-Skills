# Institutional Market Microstructure & Tick Size Impact Workflows

## Workflow 1: Tick Size Pilot Microstructure Evaluation Lifecycle
```mermaid
sequenceDiagram
    autonumber
    participant Data as Tick Data Store (kdb+/Parquet)
    participant Engine as Tick Size Impact Engine
    participant Quant as Quant Trader / Execution Desk
    participant Algo as Execution Algorithm Config

    Data->>Engine: Load L1/L2 Snapshots & Trades (Baseline $0.01)
    Engine->>Engine: evaluate_microstructure_metrics(Baseline)
    
    Data->>Engine: Load L1/L2 Snapshots & Trades (Test $0.05 / Sub-penny)
    Engine->>Engine: evaluate_microstructure_metrics(Test)

    Engine->>Engine: compare_regimes(Baseline, Test)
    Engine-->>Quant: RegimeComparisonResult (Spread Δ%, Depth Δ%, Fill Rate Δ%)

    Quant->>Engine: recommend_strategy_tuning(AlgoType, Comparison)
    Engine-->>Quant: Quantitative Parameter Tuning Recs

    Quant->>Algo: Update Order Type Mix, Slicing Aggression & Queue Offsets
```

---

## Workflow 2: Spread Decomposition & Adverse Selection Analysis Pipeline
```mermaid
flowchart TD
    A[Raw Tick & Trade Stream Ingestion] --> B[Calculate Midpoint Price P_mid]
    B --> C[Compute Quoted Spread: Ask - Bid]
    
    B --> D[Identify Trade Aggressor Side D]
    D --> E[Compute Effective Spread: 2 * D * P_trade - P_mid]
    
    D --> F[Fetch 5-Minute Future Midpoint P_mid_5m]
    F --> G[Compute Realized Spread 5m: 2 * D * P_trade - P_mid_5m]
    
    E & G --> H[Compute Adverse Selection bps: Eff_Spread - Real_Spread / P_mid * 10000]
    
    H --> I[Evaluate Algo Queue Toxicity & Market Maker Revenue]
```