# Deep Workflow Reference — websocket-reconnect-without-duplicate-subscriptions

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Decoupled Subscription State Storage:**
   - Maintain an authoritative `set` of desired symbol subscriptions (`desired_symbols`) independent of WebSocket connection objects.

2. **Fresh Resubscription on Reconnect:**
   - On WebSocket reconnect, issue fresh resubscriptions derived directly from `desired_symbols`.
   - Never replay incremental append-only subscription event logs to prevent duplicate subscriptions.

3. **Jittered Exponential Backoff:**
   - Calculate reconnect backoff delays using `calculate_backoff()` with exponential growth ($2^k$) and $\pm 20\%$ randomized jitter to avoid thundering-herd reconnect spikes during broker outages.

4. **Gap Window REST Backfill Integration:**
   - Track `gap_duration_sec` between `on_disconnect()` and `on_reconnect()`.
   - Invoke `backfill_fn()` to issue REST API historical quote requests filling data gaps across the disconnect window.

5. **Consumer-Level Tick Deduplication:**
   - Pass incoming ticks through `TickDeduplicator` using sliding window `(symbol, timestamp, seq_num)` signatures to drop duplicate deliveries around reconnection boundaries.

## Failure Modes Observed in Production

- **Append-Only Subscription Logs:** Replaying subscribe logs on reconnect, causing subscription counts and duplicate tick volume to grow exponentially over time.
- **Silent Connectivity Data Loss:** Assuming reconnect implies no missing ticks, proceeding without accounting for the gap window between disconnect and resubscription.
- **Thundering Herd Reconnections:** Reconnecting immediately without backoff/jitter during broker outages, triggering IP blocks or rate limits.
- **Zombie Socket State:** Reusing dirty WebSocket SDK connection objects without tearing down prior subscription state before resubscribing.

## Production Implementation Reference

- Reference code: `scripts/reconnect_manager.py` (`WebSocketReconnectEngine`, `TickDeduplicator`, `ReconnectEvent`).
- Automated unit tests: `scripts/test_reconnect_manager.py`.
