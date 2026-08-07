---
name: websocket-reconnection-with-state-recovery
description: Use when managing real-time market data or order update WebSocket streams
  to execute automatic reconnection with exponential backoff and randomized jitter,
  re-subscribe active symbol channels, and perform REST sequence gap-filling to prevent
  missed tick data.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- websocket
- reconnection
- exponential-backoff
- state-recovery
- gap-fill
brokers_frameworks:
- WebSocket Manager
- Python Async Engine
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when operating real-time market data streaming components or order execution WebSocket connections. Network disconnects, TCP timeouts, or broker server restarts will inevitably disrupt live WebSocket connections. Reconnecting naively risks losing in-flight ticks, desynchronizing orderbook sequences, or triggering thundering herd reconnect storms. This skill manages connection state transitions, exponential backoff with full jitter, symbol channel re-subscription, and REST sequence gap recovery.

## Prerequisites

- WebSocket feed supporting message sequence numbers (e.g. Coinbase `sequence`, Binance `u` update ID).
- REST endpoint for fetching historical tick/kline sequence gap data.

## Workflow

1. **Monitor Connection State**:
   - Maintain state machine: `DISCONNECTED` $\to$ `CONNECTING` $\to$ `AUTHENTICATED` $\to$ `SUBSCRIBED` $\to$ `RECOVERING_GAP` $\to$ `STREAMING`.

2. **Exponential Backoff with Randomized Jitter**:
   - Compute retry delay $T_{\text{retry}} = \min(T_{\text{max}}, T_{\text{base}} \times 2^k) + \text{uniform}(0, \text{jitter})$.

3. **Re-Subscribe Symbol Channels**:
   - Re-issue subscription payloads for active symbol universe (`["BTCUSDT", "ETHUSDT"]`).

4. **Sequence Gap Fill Recovery**:
   - Upon receiving first reconnected message with sequence ID $S_{\text{new}}$, detect gap if $S_{\text{new}} > S_{\text{last}} + 1$.
   - Fetch missing sequence range $[S_{\text{last}} + 1, S_{\text{new}} - 1]$ via REST API before processing live stream events.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Thundering Herd Reconnects**: Omitting randomized jitter, causing hundreds of trading bots to reconnect simultaneously on the exact same second after a broker restart.
- **Unfilled Sequence Gaps**: Resuming live orderbook processing immediately upon WS reconnect without filling missing intermediate sequence ticks.
- **Infinite Fast Loops**: Attempting rapid reconnect loops without exponential backoff, causing temporary IP bans.

## Verification

- Simulate network disconnect, verify exponential backoff delay calculation with jitter.
- Simulate sequence gap upon reconnection and verify REST gap fill invocation.
- Run `python scripts/test_ws_recovery.py` and confirm 100% pass rate.

## Related Skills

- `historical-order-book-reconstruction-from-message-logs`
- `clock-synchronization-ptp-for-trading-hosts`
- `broker-status-page-monitoring-integration`
---
