# Broker & Data Provider Coverage — survivorship-bias-free-universe-construction

| Institutional Data Provider | Point-in-Time Field | Delisting Settlement Handling |
|---|---|---|
| CRSP (Center for Research in Security Prices) | `DLSTCD` (Delisting Code) | Terminal payment / return code (`DLRET`) |
| Sharadar (US Equities) | `isdelisted` / `deletions` | Final price snapshot on delisting date |
| Norgate Data (Equities / Futures) | Point-in-Time Index Constituents | Automatic unadjusted/adjusted delisted data |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with GIPS (Global Investment Performance Standards), institutional backtest auditability, and quantitative factor research integrity.
