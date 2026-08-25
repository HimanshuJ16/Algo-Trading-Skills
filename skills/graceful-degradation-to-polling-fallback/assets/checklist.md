# Pre-Flight / Sign-off Checklist — graceful-degradation-to-polling-fallback

Use this before running a feed fallback path against live capital.

## Venue facts gathered

- [ ] **Liveness Signal Identified:** the venue's Ping/Pong or heartbeat cadence is
      recorded, or it is written down that the venue has none and this is trade-silence
      detection with all that implies for illiquid instruments.
- [ ] **Rate Limit Recorded:** the published limit for the fallback endpoint is written
      down, from the venue's own documentation, not inferred.
- [ ] **Response Schema Verified:** it is confirmed whether the fallback response actually
      carries a timestamp and a trade identity. (Binance `GET /api/v3/ticker/price`
      carries neither — see `references/standards.md` §3.)
- [ ] **Backfill Path Decided:** either a historical-trades endpoint is wired for the
      outage window, or the indicators that go unusable during a handover are listed.

## Configuration

- [ ] **Silence Window Sized From The Heartbeat:** `silence_timeout_seconds` is at least
      twice the venue's heartbeat cadence, and `heartbeat_interval_seconds` is passed so
      the constructor enforces it.
- [ ] **Poll Interval Inside The Limit:** `min_poll_interval_seconds` satisfies the
      venue's published limit with headroom, and the whole symbol universe's aggregate
      request rate has been computed — not just one symbol's.
- [ ] **Batching Where Available:** multi-symbol fallback uses the venue's batch endpoint
      rather than one request per instrument.
- [ ] **Monotonic Clock:** elapsed time is measured on `time.monotonic`; no wall-clock
      subtraction anywhere in the health path.
- [ ] **Exchange Timestamps Normalised:** one documented timezone and unit, with the
      offset handled explicitly for venues that omit it.
- [ ] **Identity Populated:** `TickPayload.identity` carries the venue's trade id or
      sequence number wherever one exists.

## Behaviour verified in staging

- [ ] **Silence Detection:** silence past the threshold degrades; silence exactly at the
      threshold does not.
- [ ] **Heartbeat Keeps A Quiet Symbol Healthy:** heartbeats with no ticks hold
      `HEALTHY_WEBSOCKET`; stopping the heartbeats degrades.
- [ ] **Same-Instant Trades Survive:** two ticks sharing a timestamp with distinct
      identities are both delivered; a repeated identity is deduplicated.
- [ ] **Data Loss Is Visible:** ticks arriving behind the watermark raise
      `stale_tick_count` rather than disappearing.
- [ ] **Throttle Holds:** a tight polling loop produces exactly one request per
      `min_poll_interval_seconds`, and throttled calls do not count as failures.
- [ ] **Blind Escalation:** `max_consecutive_poll_failures` failures set `BLIND_NO_DATA`,
      and a subsequent good quote returns it to `DEGRADED_POLLING`.
- [ ] **No False Stabilisation:** stabilisation ticks spaced wider than the silence window
      never restore `HEALTHY_WEBSOCKET`, however many arrive.
- [ ] **Concurrency:** simultaneous ingestion from multiple threads accepts each distinct
      tick exactly once.
- [ ] **Automated Testing:** `python -m unittest discover -s skills/graceful-degradation-to-polling-fallback/scripts`
      — 100% pass rate.

## Escalation wired

- [ ] **`is_blind()` Gates Order Entry:** blindness reaches the capital-preservation gate
      or kill switch; nothing infers data health from the absence of ticks.
- [ ] **Degradation Is Alerted:** `degradation_count` and `last_degradation_gap_seconds`
      reach the on-call channel, not just the log file.
- [ ] **Blind Policy Written Down:** what the strategy does with open positions while
      blind — flat, hedge or hold — was decided before go-live, not during the outage.

## Sign-off

- Venue / feed: ___________________________
- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
