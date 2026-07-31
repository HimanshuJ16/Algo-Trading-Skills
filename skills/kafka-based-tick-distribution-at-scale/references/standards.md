# Standards for Kafka Tick Streaming

| Metric | Engineering Standard |
|---|---|
| Partition Key | Ticker symbol MUST be used as partition key to guarantee per-symbol ordering. |
| Producer Batching | `batch.size` MUST be $\ge 128\text{ KB}$ and `linger.ms` set to $5\text{ ms}$. |
| Consumer Lag Limit | Consumer lag exceeding $10,000$ messages MUST trigger automated alerts. |
