# Standards for Historical Tick Data Storage

| Metric | Engineering Standard |
|---|---|
| Target Compression Ratio | Historical tick archives MUST achieve $\ge 5.0\times$ compression ratio. |
| Encoding Standard | Nanosecond timestamps MUST be delta-encoded prior to compression. |
| Partition Layout | Files MUST be partitioned by `symbol/year=YYYY/month=MM/date=YYYY-MM-DD`. |
