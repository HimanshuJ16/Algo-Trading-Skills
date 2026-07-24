# Broker & Framework Coverage — backpressure-drop-degrade-policy

| Broker / Framework | Relevance to this skill |
|---|---|
| Python `asyncio.Queue` / `collections.deque` | Standard library queue implementations requiring custom overflow handling to prevent blocking or unhandled `QueueFull` exceptions on WebSocket read loops. |
| ZeroMQ (`PUB/SUB`, `PUSH/PULL`) | Provides `ZMQ_HWM` (High Water Mark) setting; requires explicit dropping or degradation when output queues reach limit. |
| Apache Kafka / Redis Streams | Stream backpressure handling via consumer group lag monitoring and dynamic topic partitioning. |
| RxPY / ReactiveX | Reactive extensions backpressure operators (`on_backpressure_drop`, `on_backpressure_buffer`, `sample`, `throttle_last`). |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

This skill concerns engineering practice, not investment advice. Where applicable
(order placement, risk controls, live-capital promotion), it intersects with
exchange/regulatory requirements for algorithmic trading in the jurisdiction the bot
operates in (e.g., SEBI algo-trading provisions for Indian equity/derivatives markets, EU MiFID II RTS 6 requirements for system resilience and capacity monitoring).
Confirm current regulatory requirements independently — see `mappings/regulatory-coverage.md`.
