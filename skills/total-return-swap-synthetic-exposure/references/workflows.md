# Institutional Total Return Swap (TRS) Workflows

## Workflow 1: Periodic Swap Cash Flow Reset Lifecycle
```mermaid
sequenceDiagram
    autonumber
    participant Engine as TRS Engine
    participant Data as Reference Asset & Benchmark Feed
    participant Custody as Prime Broker / Custodian
    participant Margin as Collateral / CSA Manager

    Engine->>Data: Fetch Reset Period Start/End Prices & SOFR Rate
    Data-->>Engine: Prices (P_start, P_end), SOFR Fixings, Dividend Events
    
    Engine->>Engine: calculate_total_return_leg(Config, Reset)
    Engine->>Engine: calculate_funding_leg(Config, Reset)
    Engine->>Engine: process_reset_period(Config, Reset, Side)
    
    Engine-->>Custody: Generate TRSSettlement (Net Cashflow USD)
    
    alt Net Cashflow > 0 (Inflow to Receiver)
        Custody-->>Engine: Wire Settlement Cash Payment
    else Net Cashflow < 0 (Outflow from Receiver)
        Engine->>Custody: Transfer Settlement Cash Payment
    end

    Engine->>Margin: Evaluate ISDA Variation Margin (VM)
    Margin-->>Custody: Update Collateral Account Postings
```

---

## Workflow 2: Synthetic Exposure & Delta Risk Pipeline
```mermaid
flowchart TD
    A[Strategy Asset Allocation Signal] --> B{Execution Path}
    B -->|Physical Equity| C[Buy Shares on Stock Exchange]
    B -->|Synthetic TRS| D[Execute TRS Contract with Prime Broker]
    
    D --> E[Record TRS Notional, Reference Price & Quantity]
    E --> F[Calculate Daily MtM: Capital Return + Divs - Funding Interest]
    
    F --> G[Track Synthetic Delta = +Shares / -Shares]
    G --> H[Monitor Daily Benchmark Rate (SOFR/ESTR) Drift]
    H --> I[Execute Periodic Net Cash Flow Resets & Rebalance Collateral]
```