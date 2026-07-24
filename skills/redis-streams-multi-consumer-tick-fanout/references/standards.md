# Broker & Framework Coverage — redis-streams-multi-consumer-tick-fanout

| Technology | Messaging Paradigm | Delivery Guarantee | Crash Recovery |
|---|---|---|---|
| Redis Streams | Consumer Groups (`XREADGROUP`) | At-least-once | `XCLAIM` / `XPENDING` |
| Redis Pub/Sub | Broadcast channel | At-most-once (Fire & Forget) | None (Lost if offline) |
| Apache Kafka | Consumer Groups (`KafkaConsumer`) | At-least-once | Offset commit / rebalance |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with high-frequency market data distribution, asynchronous microservices architecture, and fault-tolerant streaming standards.
