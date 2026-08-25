# Deep Workflow Reference — graceful-degradation-to-polling-fallback

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## 0. Before writing any code

Collect three facts about the venue. Every design decision below follows from them, and
guessing any of the three produces a control that fails in production rather than in test.

1. **The transport liveness signal and its cadence.** A WebSocket Ping/Pong pair, or a
   venue heartbeat frame. Binance spot pings every 20 s; Kite Connect sends a 1-byte
   heartbeat every couple of seconds when idle. If there is none, record that you are
   detecting *trade* silence and accept the consequences for illiquid instruments.
2. **The REST fallback endpoint, its published rate limit, and whether its response
   carries a timestamp and a trade identity.** See `references/standards.md` §2–§4. Note
   in particular that Binance `GET /api/v3/ticker/price` returns no timestamp at all.
3. **Whether a historical-trades endpoint exists** to backfill the outage window. If not,
   you already know which of your indicators become unusable during a handover.

## Full Procedure

1. **Instantiate with venue-derived configuration.**
   ```python
   mgr = FeedFallbackManager(
       symbol="BTCUSDT",
       silence_timeout_seconds=45.0,      # >= 2x the 20s Binance ping cadence
       heartbeat_interval_seconds=20.0,   # constructor enforces the relationship
       min_poll_interval_seconds=1.0,     # from the venue's published limit
       required_stabilization_ticks=5,
       max_consecutive_poll_failures=3,
   )
   ```
   Passing `heartbeat_interval_seconds` turns a silent misconfiguration into a
   `ValueError` at construction. A 3-second window against a 20-second ping would degrade
   a healthy socket on every quiet interval and pin the symbol into permanent polling.

2. **Wire liveness from both the keepalive handler and the data handler.**
   - `on_websocket_heartbeat()` from the pong / heartbeat callback.
   - `ingest_websocket_tick(tick)` from the data callback. It refreshes liveness even for
     ticks that are subsequently deduplicated — a repeated tick still proves the socket is
     alive.
   - Normalise the exchange timestamp to epoch seconds in one documented timezone before
     constructing the `TickPayload`. Kite's `"YYYY-MM-DD HH:MM:SS"` carries no offset;
     parsing IST as UTC puts the watermark 5½ hours ahead and discards the session.
   - Populate `identity` wherever the venue provides one — Alpaca's `i`, an ITCH sequence
     number, an exchange trade id.

3. **Run the health loop on its own cadence.**
   - Call `check_feed_health()` on a timer independent of tick arrival; a loop driven by
     incoming ticks cannot fire when ticks stop, which is the only case that matters.
   - The transition fires once. `degradation_count` and `last_degradation_gap_seconds`
     from `get_status()` are the record of it.
   - On the transition, treat the gap as a data-integrity event: backfill it from a
     historical-trades endpoint, or mark volume-, VWAP- and trade-count-derived indicators
     unusable for the duration of their lookback.

4. **Poll REST inside the limit.**
   ```python
   tick = mgr.poll_rest_fallback(fetch_quote)   # returns None when healthy or throttled
   ```
   - The throttle is a per-instrument floor. It knows nothing about other symbols on the
     same credentials — batch upstream (Kite `/quote` 500 instruments, Alpaca `symbols=`
     list, Binance all-symbols `/ticker/price` at weight 4) and dispatch into per-symbol
     managers, or hand the budget to `multi-broker-rate-limit-handling`.
   - Classify REST failures before reacting: 429 needs backoff and possibly a longer poll
     interval, 401/403 needs a human, 5xx needs a retry. The manager counts them
     uniformly towards blindness; the classification and backoff belong in your fetcher.
   - Never shorten the poll interval because the stream is down. That is precisely when a
     ban is most expensive.

5. **Deduplicate across both paths with one watermark.**
   - Both sources share the manager's watermark. That is what keeps the overlap window
     safe while the stream is back but the poller has not yet been stopped.
   - Accepted: timestamp ahead of the watermark, or equal with an unseen `identity`.
   - Dropped and counted as `duplicate_tick_count`: equal timestamp with a seen identity,
     or with no identity at all.
   - Dropped and counted as `stale_tick_count`: timestamp behind the watermark. This is
     real data loss — typically buffered WebSocket ticks surfacing after a REST snapshot
     jumped ahead of them. Alert on a rising count; do not treat it as noise.

6. **Hand back only on a recent consecutive run.**
   - `required_stabilization_ticks` consecutive ticks whose inter-arrival gaps are all
     inside the silence window. Any longer gap restarts the run at 1.
   - Stop the polling worker after the mode reads `HEALTHY_WEBSOCKET`, not when the first
     tick reappears.

7. **Escalate on `BLIND_NO_DATA`.**
   - `is_blind()` is the signal that the strategy has no price source. Gate new order
     entry on it and hand it to `capital-preservation-mode-for-degraded-conditions` or
     `kill-switch-and-drawdown-circuit-breakers`.
   - A successful poll drops back to `DEGRADED_POLLING`; a stabilised stream returns
     `HEALTHY_WEBSOCKET` directly.
   - Decide in advance, and write down, whether blindness with open positions means flat,
     hedge, or hold. Making that decision during the outage is making it badly.

## Failure Modes Observed in Production

- **Silent stream freezes.** A dead TCP connection with no `on_close` event. RFC 1122
  §4.2.3.6 requires TCP keep-alives to default off and, when on, to a ≥ 2-hour interval,
  so nothing below the application layer will notice within a trading session.
- **Failover on a quiet market.** A trade-silence timeout applied to an illiquid
  instrument fails over because nobody traded, then polls indefinitely.
- **The fallback causing the blackout.** Unthrottled polling during an outage draws a 429
  and then an IP ban — Binance bans "scale in duration for repeat offenders, from 2
  minutes to 3 days" — leaving the bot with neither feed.
- **Coarse-timestamp data loss.** A strict `timestamp >` test against Kite's
  one-second `last_trade_time` keeps one tick per second and silently discards the rest.
- **Clock-domain mixing.** A tick stamped with local receipt time pushes the watermark
  ahead of exchange time by the network latency; every genuine tick behind it is dropped
  until exchange time catches up.
- **Wall-clock elapsed measurement.** An NTP step backwards makes measured silence
  negative and suppresses degradation entirely.
- **False stabilisation.** Stragglers arriving 10 s apart from a still-broken feed
  satisfying a bare consecutive-tick counter.
- **Double-feeding the handover.** Both paths pushing into the pipeline during the overlap
  with per-source deduplication state instead of a shared watermark.
- **Silent blindness.** A fallback returning `None` indefinitely while the strategy holds
  positions, indistinguishable from a quiet market.
- **Lost updates under concurrency.** The read thread, the poller and the health loop
  mutating the watermark without a lock, letting a duplicate tick through.

## Production Implementation Reference

- Reference code: `scripts/feed_fallback_manager.py`
  (`FeedFallbackManager`, `FeedMode`, `TickPayload`, `FeedStatus`).
- Automated unit tests: `scripts/test_feed_fallback_manager.py`.
- Run with:
  `python -m unittest discover -s skills/graceful-degradation-to-polling-fallback/scripts`
