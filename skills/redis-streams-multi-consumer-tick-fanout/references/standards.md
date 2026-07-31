# Standards for Redis Streams Multi-Consumer Tick Fanout

| Metric | Engineering Standard |
|---|---|
| Stream MAXLEN | XADD MUST use MAXLEN to cap stream at $100,000$ entries. |
| XACK Latency | Workers MUST acknowledge ticks within $100\text{ms}$ of processing. |
| XCLAIM Idle Threshold | Stale entries MUST be reclaimed after $30\text{s}$ idle timeout. |
