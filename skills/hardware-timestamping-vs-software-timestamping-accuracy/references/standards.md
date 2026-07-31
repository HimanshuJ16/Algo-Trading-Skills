# Standards for Hardware vs Software Timestamping

| Metric | Engineering Standard |
|---|---|
| MiFID II RTS 25 UTC Limit | Max UTC divergence MUST be $\le 100\mu\text{s}$ ($100,000\text{ ns}$) for HFT systems. |
| Timestamp Precision | Hardware MAC layer timestamping MUST achieve nanosecond-level precision. |
| Clock Synchronization | Hardware NICs MUST be synchronized via PTP IEEE 1588 grandmaster. |
