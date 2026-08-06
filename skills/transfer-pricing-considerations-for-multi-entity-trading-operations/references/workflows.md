# Institutional Transfer Pricing Workflows

## Workflow 1: Intercompany Service Fee Settlement Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant Provider as Service Provider Entity (e.g. UK Quant Lab)
    participant Engine as Transfer Pricing Engine
    participant Recipient as Recipient Entity (e.g. US IP Holdco)
    participant Accounting as Intercompany Ledger / ERP

    Provider->>Engine: Submit Direct Operating Expenses ($500k Base Cost)
    Engine->>Engine: Select TP Methodology (Cost-Plus 10% Markup)
    Engine->>Engine: calculate_cost_plus_fee($500k, 10%)
    Engine->>Engine: calculate_berry_ratio($550k, $500k) -> 1.10
    
    Engine-->>Accounting: Post Intercompany Invoice ($550,000 Fee)
    Accounting->>Recipient: Charge Intercompany Service Expense ($550k)
    Accounting->>Provider: Credit Intercompany Service Revenue ($550k)
    
    Engine->>Engine: Log OECD Local File Compliance Audit Trail
```

---

## Workflow 2: OECD DEMPE Residual Profit Split Pipeline
```mermaid
flowchart TD
    A[Consolidate Global Trading PnL: $10,000,000] --> B[Assess DEMPE Contributions Across Group Entities]
    
    B --> C[US Entity: Algo IP Development = 1.0, Protection = 1.0 -> Score 0.88]
    B --> D[UK Entity: Regional Execution & Risk Management -> Score 0.30]
    
    C & D --> E[Compute Composite Relative DEMPE Weights: US=74.6%, UK=25.4%]
    
    E --> F[Execute Residual Profit Split]
    F --> G[Allocate $7,457,627 to US IP Entity]
    F --> H[Allocate $2,542,373 to UK Manager Entity]
    
    G & H --> I[Generate OECD Master File & CbC Reporting Audit Logs]
```