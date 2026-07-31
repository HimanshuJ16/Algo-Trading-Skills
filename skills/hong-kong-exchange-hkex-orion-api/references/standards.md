# Standards for HKEX Orion API Integration

| Metric | Engineering Standard |
|---|---|
| Stock Code Format | Stock codes MUST be zero-padded to 5 digits (e.g., `00700`). |
| Tick Size Rules | Order prices MUST conform to the HKEX Second Schedule Spread Table. |
| Board Lot Sizing | Orders MUST be multiples of the security's official Board Lot size. |
