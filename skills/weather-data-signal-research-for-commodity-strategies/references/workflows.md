# Institutional Weather Data Signal Research Workflows

## Workflow 1: Weather Station Data & Model Forecast Processing Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant NOAA as NOAA / GFS / ECMWF Model Ingestion
    participant Engine as Weather Signal Research Engine
    participant Baseline as 10-Yr Climate Norm Database
    participant OMS as Commodity Order Management System

    NOAA->>Engine: Ingest Station Temps (T_min, T_max) & Model Forecasts
    Engine->>Engine: 1. Calculate Station HDD, CDD, GDD Metrics
    Engine->>Engine: 2. Apply Regional Population / Consumption Weights
    
    Engine->>Baseline: Query 10-Year Baseline Mean & Std Dev
    Baseline-->>Engine: Return Baseline (Mean, Std)
    
    Engine->>Engine: 3. Compute Climate Anomaly Z-Score = (Val - Mean) / Std
    Engine->>Engine: 4. Map Z-Score to Directional Signal (LONG / SHORT / NEUTRAL)
    
    alt Abs(Z-Score) >= 1.5
        Engine->>OMS: Route Commodity Order Signal (NG / ERCOT / Corn / Soybeans)
    else Abs(Z-Score) < 1.5
        Engine->>Engine: Maintain NEUTRAL Position
    end
```

---

## Workflow 2: GFS vs ECMWF Model Shift Arbitrage Pipeline
```mermaid
flowchart TD
    A[Monitor Model Runs: 00z, 06z, 12z, 18z] --> B[Extract 14-Day Forecast Cumulative GW-HDDs]
    
    B --> C[Compute Model Run Shift: Delta_HDD = HDD_12z - HDD_00z]
    C --> D{Abs(Delta_HDD) >= 10 GW-HDDs?}
    
    D -- No --> E[No Significant Model Revision -> HOLD]
    D -- Yes --> F{Delta_HDD > 0 (Colder Forecast)?}
    
    F -- Yes --> G[Submit Immediate LONG Signal for Natural Gas Futures]
    F -- No --> H[Submit Immediate SHORT Signal for Natural Gas Futures]
    
    G --> I[Execute Order Before Mainstream Media Report Release]
    H --> I
```