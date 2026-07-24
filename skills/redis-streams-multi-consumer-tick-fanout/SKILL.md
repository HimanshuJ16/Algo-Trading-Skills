---
name: redis-streams-multi-consumer-tick-fanout
description: >-
  Use when distributing real-time market data ticks to multiple independent services using Redis Streams consumer groups, message acknowledgments (XACK), and pending message claims (XCLAIM)
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "redis-streams", "tick-fanout", "consumer-groups", "at-least-once"]
brokers_frameworks: ["Redis 6.0+", "redis-py"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a real-time market data ingestion pipeline must fan out incoming tick streams (from WebSockets or broker feeds) to multiple downstream consumer microservices — such as strategy execution engines, risk monitors, feature stores, and historical database recorders. Using Pub/Sub causes data loss if a consumer temporarily disconnects; using Redis Streams with consumer groups (`XREADGROUP`), explicit acknowledgments (`XACK`), and stale pending claim protocol (`XCLAIM`) guarantees high-throughput, non-blocking fanout with at-least-once delivery guarantees.

## Prerequisites

- Redis 6.0+ instance or cluster.
- Python `redis-py` library (or mock Redis stream engine for local testing).
- Defined consumer group names (e.g. `grp_strategy_engine`, `grp_risk_monitor`, `grp_db_recorder`).

## Workflow

1. **Ingest & Publish Ticks (`XADD`)**:
   - Ingest live tick data payload (`symbol`, `last_price`, `volume`, `timestamp`).
   - Publish to stream `market_data_stream` via `XADD` with capped stream length (`MAXLEN ~ 100000`).

2. **Register Consumer Groups**:
   - Create independent consumer groups for each downstream service using `XGROUP CREATE market_data_stream {group_name} $ MKSTREAM`.

3. **Consume Stream Messages (`XREADGROUP`)**:
   - Downstream worker processes execute `XREADGROUP GROUP {group_name} {consumer_id} COUNT 100 BLOCK 1000 STREAMS market_data_stream >`.
   - Process ticks independently across strategy, risk, and storage services.

4. **Acknowledge Message Delivery (`XACK`)**:
   - Submit `XACK market_data_stream {group_name} {message_id}` immediately after successfully processing each tick batch.

5. **Stale Pending Claim Recovery (`XCLAIM`)**:
   - Periodically inspect pending message list via `XPENDING`. Reassign pending messages un-ACKed for $> 5000\text{ms}$ using `XCLAIM` to handle crashed consumer workers.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Pub/Sub for Critical Market Data**: Using basic Redis Pub/Sub instead of Redis Streams, losing ticks when a consumer service reboots or experiences brief network latency.
- **Uncapped Stream Growth**: Failing to specify `MAXLEN ~` on `XADD`, causing Redis memory exhaustion over high-frequency tick bursts.
- **Missing Pending Claims**: Omitting `XCLAIM` recovery logic, leaving un-ACKed ticks stuck in the Pending Entries List (PEL) after a consumer crashes.

## Verification

- Publish mock tick payload to `RedisTickFanoutManager` and verify all consumer groups receive the message.
- Verify message acknowledgment (`XACK`) removes entries from the Pending Entries List.
- Simulate consumer worker crash and verify `claim_stale_ticks()` reassigns un-ACKed ticks.
- Run unit test suite `python scripts/test_redis_tick_fanout.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `tick-buffering-burst-handling`
- `websocket-reconnect-without-duplicate-subscriptions`
---
