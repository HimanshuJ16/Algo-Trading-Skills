# Institutional Vendor Corporate Action Adjustment Workflows

## Workflow 1: Historical Price Adjustment & Factor Calculation Pipeline

Note the two independent cumulative products. `F_price` accumulates every applicable action;
`F_share` accumulates only actions that change shares outstanding, and it alone drives volume.

```mermaid
sequenceDiagram
    autonumber
    participant Data as Raw Price Storage
    participant CA as Corporate Actions Database
    participant Engine as Adjustment Reconciliation Engine
    participant Output as Quant Feature Store

    Data->>Engine: Ingest raw unadjusted OHLCV bars, one symbol
    CA->>Engine: Ingest corporate actions with ex-dates

    Engine->>Engine: 1. Validate bars and actions, reject bad records
    Engine->>Engine: 2. Select axes for the declared methodology
    Engine->>Engine: 3. Aggregate actions sharing an ex-date
    Engine->>Engine: 4. Accumulate F_price and F_share over ex_date > bar_date
    Engine->>Engine: 5. P_adj = P_raw * F_price, V_adj = V_raw / F_share

    Engine-->>Output: Store vendor-conforming adjusted series
```

---

## Workflow 2: Cross-Vendor Adjustment Reconciliation Pipeline

```mermaid
flowchart TD
    A["Initiate cross-vendor reconciliation"] --> B["Fetch adjusted series from Vendor A"]
    A --> C["Fetch adjusted series from Vendor B"]

    B --> D["Align on common calendar dates"]
    C --> D

    D --> E["Compute coverage: compared / union of dates"]
    E --> F{"Both closes finite and mid price positive?"}

    F -- No --> G["Flag NON_FINITE_PRICE or NON_POSITIVE_MID_PRICE"]
    F -- Yes --> H["Percentage difference = |P_a - P_b| / mid * 100"]

    H --> I{"Difference > tolerance_pct?"}
    I -- No --> J["Log date passed"]
    I -- Yes --> K["Flag TOLERANCE_BREACH divergence"]

    G --> L["Compile ReconciliationReport"]
    J --> L
    K --> L

    L --> M{"Status passed AND coverage acceptable?"}
    M -- Yes --> N["Approve series for backtesting and alpha models"]
    M -- No --> O["Quarantine symbol and trigger data audit"]
```

---

## Workflow 3: Triaging a Divergence

```mermaid
flowchart TD
    A["Divergence flagged"] --> B{"reason field?"}
    B -- "NON_FINITE_PRICE / NON_POSITIVE_MID_PRICE" --> C["Data corruption: re-pull the bar from the vendor"]
    B -- "TOLERANCE_BREACH" --> D{"Divergence starts on one date and persists backwards?"}

    D -- Yes --> E["Unreconciled corporate action at that ex-date"]
    E --> F{"Ratio of the two series?"}
    F -- "Equals a split ratio" --> G["One vendor missed the split, or ex-date differs"]
    F -- "Equals 1 - D/P" --> H["Methodology mismatch: total return vs price return"]

    D -- No --> I{"Scattered and below one tick?"}
    I -- Yes --> J["Rounding or cum-price convention difference: widen tolerance deliberately"]
    I -- No --> K["Escalate: compare vendor-published factors directly"]
```
