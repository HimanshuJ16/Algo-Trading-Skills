# Standards for Universe Change Tracking

| Metric | Engineering Standard |
|---|---|
| Primary Key | Universe change tracking MUST be keyed by permanent identifiers (FIGI / ISIN). |
| De-listing Action | De-listings MUST trigger immediate position liquidation alerts. |
| Ticker Rename Action | Ticker renames MUST update symbol mapper tables without breaking historical database joins. |
