---
name: graceful-degradation-to-polling-fallback
description: >-
  Use when maintaining real-time market data availability to detect WebSocket feed degradation, seamlessly switch to REST polling fallback, and deduplicate ticks during handover transitions
domain: algorithmic-trading
subdomain: real-time-architecture
tags: ["real-time-architecture", "feed-degradation", "polling-fallback", "websocket-failover", "high-availability"]
brokers_frameworks: ["All Market Data Feeds", "WebSockets", "REST APIs"]
version: "1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever an algorithmic trading bot relies on WebSocket market data feeds for live strategy execution. WebSockets can silently freeze, drop packets, or disconnect due to network congestion or broker maintenance. If market data streaming halts while a strategy holds open positions, the bot becomes blind to price movements. Implementing a feed health circuit breaker, automatic failover to REST HTTP polling when stream silent time exceeds thresholds (e.g., $3.0\text{s}$), tick deduplication at handover, and automatic recovery back to WebSocket when streams stabilize is mandatory.

## Prerequisites

- Active WebSocket tick feed and REST quote/ticker endpoint.
- Configured silence timeout threshold (default $3.0\text{s}$).
- Tick deduplication memory tracking last ingested tick timestamp.

## Workflow

1. **Monitor WebSocket Feed Silence**:
   - Record `last_websocket_tick_time` for each incoming tick.
   - Run periodic health check loop. If $T_{\text{now}} - T_{\text{last\_ws}} > 3.0\text{s}$, mark feed mode as `DEGRADED_POLLING`.

2. **Activate REST Polling Worker**:
   - Immediately launch REST polling loop (e.g., polling GET quote endpoint every $500\text{ms}$).
   - Continue feeding strategy pipeline with polled REST ticks.

3. **Deduplicate Handover Ticks**:
   - Filter polled REST ticks against `last_processed_timestamp` to prevent feeding duplicate price ticks to strategy indicators during handover.

4. **Monitor WebSocket Reconnection & Stabilization**:
   - Continue attempting WebSocket background reconnection.
   - When WebSocket resumes and receives $N=5$ consecutive valid ticks, verify stream health.

5. **Graceful Handback to WebSocket Stream**:
   - Terminate REST polling worker gracefully and restore feed mode to `HEALTHY_WEBSOCKET`.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Un-Monitored Silent Freezes**: Failing to detect a frozen TCP WebSocket connection where no disconnect event fired but no ticks arrive.
- **Double-Feeding Handover Ticks**: Ingesting both REST polled ticks and reconnected WebSocket ticks simultaneously without timestamp deduplication.
- **Aggressive Flapping**: Rapidly switching back and forth between WebSocket and REST polling on brief single-tick network stutters.

## Verification

- Simulate active WebSocket feed and verify mode is `HEALTHY_WEBSOCKET`.
- Inject a 3.5s silence gap and verify feed mode transitions to `DEGRADED_POLLING` and triggers REST polling.
- Verify REST ticks with timestamp $\le$ last ingested tick are deduplicated.
- Simulate WebSocket recovery and verify mode returns to `HEALTHY_WEBSOCKET` after 5 consecutive ticks.
- Run unit test suite `python scripts/test_feed_fallback_manager.py` and confirm 100% pass rate.

## Related Skills

- `producer-consumer-tick-pipeline`
- `websocket-reconnect-without-duplicate-subscriptions`
- `tick-buffering-burst-handling`
---
