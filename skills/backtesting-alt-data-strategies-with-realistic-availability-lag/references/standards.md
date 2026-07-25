# Standards

| Constraint | Rule |
|--------|------------|
| Minimum Alt Data Lag | Must be $\ge 1$ day. Intraday alt-data requires microsecond timestamping (not covered by this daily enforcer). |
| Publication Date Fallback | If the exact publication timestamp is unknown, the lag must default to the vendor's maximum contractual SLA delay. |
| Revisions | If a vendor overwrites historical files, the dataset is tainted. You must store daily snapshots of the data to perform true PIT backtesting. |