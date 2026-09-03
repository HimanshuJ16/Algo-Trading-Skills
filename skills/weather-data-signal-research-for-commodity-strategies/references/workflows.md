# Institutional Weather Data Signal Research Workflows

## Workflow 1: Weather Station Data & Model Forecast Processing Pipeline
```mermaid
sequenceDiagram
    autonumber
    participant NOAA as NOAA / GFS / ECMWF Model Ingestion
    participant Engine as Weather Signal Research Engine
    participant Baseline as Trailing Climate Norm Database
    participant OMS as Commodity Order Management System

    NOAA->>Engine: Ingest Station Temps (T_min, T_max) & Model Forecasts
    Engine->>Engine: 0. Validate per date: one date per batch, unique station IDs,<br/>non-negative weights, T_max >= T_min, finite values
    Engine->>Engine: 1. Calculate Station HDD, CDD, Modified GDD (86F cap / 50F floor)
    Engine->>Engine: 2. Apply Weights (population for HDD/CDD, acreage for GDD)

    Engine->>Baseline: Query seasonal window STRICTLY BEFORE signal date
    Baseline-->>Engine: Return Baseline (Mean, Sample Std, n)

    alt n < min_observations
        Engine->>Engine: Suppress signal for this date (do NOT thin the baseline)
    else n sufficient
        Engine->>Engine: 3. Compute Anomaly Z-Score = (Val - Mean) / Std, unrounded
        Engine->>Engine: 4. Map Z-Score to Directional Signal (LONG / SHORT / NEUTRAL)

        alt Abs(Z-Score) >= 1.5
            Engine->>OMS: Route Commodity Order Signal (NG / ERCOT / ZC / ZS)
        else Abs(Z-Score) < 1.5
            Engine->>Engine: Maintain NEUTRAL Position (confidence = 0.0)
        end
    end
```

### Look-ahead guard (step 3)
The baseline query is the single point where look-ahead bias enters a weather strategy. `compute_climate_baseline` filters on `entry_date < as_of` — strictly, not `<=` — and additionally bounds the sample to the seasonal window ($\pm$`day_window` calendar days of the target day-of-year, wrapping at the year boundary) and to `lookback_years` of history. Day-of-year matching maps every date onto a fixed non-leap reference year so a leap day does not shift the window by one.

The failure mode this prevents is subtle: computing $\mu$ and $\sigma$ once over the entire historical series (the natural pandas one-liner) embeds every future observation into the norm used to score the earliest dates, which inflates in-sample Sharpe and vanishes live.

---

## Workflow 2: GFS vs ECMWF Model Shift Arbitrage Pipeline
```mermaid
flowchart TD
    A[Monitor Model Runs] --> A1{Which model?}

    A1 -- GFS --> A2[All four cycles 00z/06z/12z/18z reach 384h]
    A1 -- ECMWF --> A3[Only 00z and 12z reach the medium range;<br/>06z/18z stop at 90h HRES / 144h ENS]

    A2 --> B[Extract 14-Day Forecast Cumulative Pop-Weighted HDDs]
    A3 --> B

    B --> B1{Both runs cover the full 14-day horizon?}
    B1 -- No --> B2[Reject comparison: truncated run would<br/>manufacture a phantom revision]
    B1 -- Yes --> C[Compute Run Shift: Delta_HDD = HDD_current - HDD_prior]

    C --> D{Abs Delta_HDD >= revision threshold?}

    D -- No --> E[No Significant Model Revision -> HOLD]
    D -- Yes --> F{Delta_HDD > 0, i.e. colder forecast?}

    F -- Yes --> G[LONG Signal for Natural Gas Futures]
    F -- No --> H[SHORT Signal for Natural Gas Futures]

    G --> I{Inside an EIA WNGSR or USDA WASDE freeze window?}
    H --> I
    I -- Yes --> J[Queue signal; do not execute across the print]
    I -- No --> K[Execute]
```

### Comparing like with like
Run-to-run deltas are only meaningful between runs of the **same model** covering the **same horizon**:

- **GFS**: all four daily cycles run to 16 days, so 00z-vs-06z, 06z-vs-12z, etc. are all valid 14-day comparisons.
- **ECMWF**: the 06z and 18z HRES/ENS cycles run only to 90 h and 144 h respectively. A 14-day cumulative HDD does not exist for them. Compare ECMWF **00z vs 12z** (or 12z vs the prior 00z) only.
- Never difference a GFS cumulative against an ECMWF cumulative and call it a revision — that is a model-disagreement measure, not a forecast shift.

### Revision threshold calibration
The threshold on $|\Delta \text{HDD}|$ is a calibrated parameter, not a constant: it depends on the region's weight vector, the horizon, and the season, and must be fitted on trailing data (10 gas-weighted HDDs is a materially larger revision in shoulder season than in January). Fit it with the same strictly-trailing discipline as the climate norm, and re-fit on a documented cadence rather than hard-coding a number.

---

## Workflow 3: Execution Freeze Windows
Weather-driven commodity signals must not execute across scheduled fundamental prints, which re-price the contract on information orthogonal to the weather forecast:

| Report | Publisher | Schedule | Affected contracts |
| :--- | :--- | :--- | :--- |
| Weekly Natural Gas Storage Report (WNGSR) | US EIA | Thursday 10:30 a.m. ET (shifts in holiday weeks; EIA posts exceptions) | `NG` and power |
| WASDE | USDA OCE | Monthly, 12:00 noon ET | `ZC`, `ZS` and grain/oilseed complex |

Read the schedule from the publisher's calendar rather than assuming a fixed weekday — the EIA release moves for federal holidays, and the WASDE date moves within the 9th-12th of the month.
