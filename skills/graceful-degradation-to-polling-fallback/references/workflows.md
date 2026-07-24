# Deep Workflow Reference — graceful-degradation-to-polling-fallback

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **WebSocket Feed Health Monitoring:**
   - Record `last_ws_tick_time` on every incoming WebSocket tick.
   - Run periodic health check loop `check_feed_health()`.
   - If $T_{\text{now}} - T_{\text{last\_ws}} > 3.0\text{s}$, transition state to `DEGRADED_POLLING`.

2. **Activate REST Polling Fallback Worker:**
   - Initiate background REST polling loop fetching quote/ticker updates every $500\text{ms}$.
   - Pass polled ticks to strategy pipeline via `poll_rest_fallback()`.

3. **Handover Tick Deduplication:**
   - Verify `tick.timestamp > last_processed_timestamp` before passing tick to strategy indicators.

4. **Stream Reconnection & Stabilization Monitoring:**
   - Attempt background WebSocket reconnection.
   - Increment `consecutive_ws_ticks` counter on stream re-establishment.

5. **Graceful Handback to WebSocket:**
   - When `consecutive_ws_ticks` reaches $N=5$, terminate REST polling worker and restore state to `HEALTHY_WEBSOCKET`.

## Failure Modes Observed in Production

- **Silent Stream Freezes:** Allowing a dead TCP connection without an explicit `on_close` event to freeze strategy indicators.
- **Double-Feeding Handover Ticks:** Ingesting both REST and WebSocket ticks during reconnection without timestamp deduplication.

## Production Implementation Reference

- Reference code: `scripts/feed_fallback_manager.py` (`FeedFallbackManager`, `FeedMode`, `TickPayload`).
- Automated unit tests: `scripts/test_feed_fallback_manager.py`.
