# Pre-Flight / Sign-off Checklist — websocket-reconnection-with-state-recovery

Use this before considering the skill's implementation complete.

## Recovery path

- [ ] **Venue capability confirmed:** the recovery path is written down as one of
      id-addressable history endpoint / snapshot-only / neither — checked against the
      venue's current documentation, not assumed.
- [ ] **Fill callback matches that capability:** `rest_gap_fill_fn` is configured *only*
      where the venue can actually return a sequence range (e.g. Binance
      `aggTrades?fromId=`), and is `None` for order book delta streams.
- [ ] **`max_gap_fill_size` is the endpoint's page size** (1000 for Binance `aggTrades`),
      not an arbitrary number.
- [ ] **Fill callback has its own network timeout** — it runs under the manager's lock.

## Backoff

- [ ] **Cap is a cap:** delays sampled across attempts 0–40 all lie in
      `[0, max_backoff_sec]`.
- [ ] **First retry uses the base delay as a ceiling,** not `2 × base`.
- [ ] **Jitter variant chosen deliberately:** `1.0` (Full) unless a floor is required.
- [ ] **Long outage survived:** 1000+ consecutive reconnect attempts raise no
      `OverflowError`.
- [ ] **Venue connection limit respected:** the worst-case retry rate stays inside the
      published limit (Binance: 300 attempts / 5 min / IP).
- [ ] **Scheduled rotations flagged** with `scheduled=True` so an expected 24-hour eviction
      does not escalate the failure counter.

## State machine and subscriptions

- [ ] **Transitions verified** against `state_history`, including that `AUTHENTICATED`
      appears only when `requires_auth=True`.
- [ ] **Re-subscription rebuilt from desired state,** returned sorted and deduplicated.
- [ ] **Backoff resets on a processed message,** not on socket open.

## Sequence integrity

- [ ] **Duplicate/stale frames are withheld and never regress the watermark.**
- [ ] **A complete fill is emitted in order, before the triggering message.**
- [ ] **Every incomplete fill fails closed:** short page, empty list, `None`, reordered
      range, wrong symbol, raised exception — each latches `RECOVERING_GAP` and reports
      the exact missing range.
- [ ] **A gap with no fill callback latches** rather than passing through.
- [ ] **An oversized gap does not call the endpoint at all.**
- [ ] **`resynchronize()` is wired to a real re-snapshot** — local state discarded and
      rebuilt, then the next expected sequence set to `snapshot_last_update_id + 1`.
- [ ] **`is_synchronized()` gates order entry and every state-derived signal.**
- [ ] **Latching is per symbol:** one broken symbol does not stop the others.

## Operations

- [ ] **`processed_messages` is bounded** for a 24/7 process.
- [ ] **Counters exported to monitoring:** `reconnect_attempts`,
      `duplicate_message_count`, `gap_fill_success_count`, `gap_fill_failure_count`,
      `withheld_message_count` — with an alert on a rising withheld count, which means the
      consumer is blind.
- [ ] **Concurrency exercised:** multi-threaded ingestion emits each message exactly once.
- [ ] **Automated testing:** run
      `python -m unittest discover -s skills/websocket-reconnection-with-state-recovery/scripts`
      — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
