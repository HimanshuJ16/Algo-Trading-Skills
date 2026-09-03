# Pre-Flight / Sign-off Checklist — tradestation-websocket-order-updates

Use this before considering the skill's implementation complete.

## Environment

- [ ] **Host selects the environment:** live traffic goes to
      `https://api.tradestation.com`, paper to `https://sim-api.tradestation.com`.
      Confirm the environment is *not* inferred from the account id, and that a
      mismatched account/host pairing fails closed at startup.
- [ ] **Scope and token:** OAuth token carries `ReadAccount`; expiry/refresh is
      handled outside the read loop.

## Stream handling

- [ ] **Frame reassembly:** the transport buffers bytes and emits complete JSON
      values. Confirm HTTP chunk boundaries are not treated as message
      boundaries, and that `FRAME_MALFORMED` counts are alerted on.
- [ ] **Control frames branch correctly:** `GoAway` and `Error` frames terminate
      the request and trigger reconnect + catch-up; `EndSnapshot` marks the end of
      the initial replay; heartbeats are liveness only.
- [ ] **Stall detection:** silence beyond the threshold (default 15 s, against a
      documented 5 s idle heartbeat) closes the connection rather than waiting.
      Confirm the timer uses a **monotonic** clock.

## Fill accounting

- [ ] **Correct fields:** executed quantity is read from `Legs[].ExecQuantity` and
      average price from `FilledPrice`. Confirm no code path reads
      `FilledQuantity` or `AveragePrice` — neither exists on a v3 order.
- [ ] **Snapshot semantics:** the ledger applies order state by **assignment**,
      never `+=`. Verified by replaying a reconnect snapshot and confirming the
      position is unchanged.
- [ ] **Partial fills survive:** three successive `FPR` frames on one order
      (2 → 5 → 9 of 10) produce three distinct applied events.
- [ ] **`FPR` is not treated as terminal**, and `FLP` is.
- [ ] **Money is `Decimal`,** not binary float, anywhere quantities or prices are
      persisted or summed.

## Delivery semantics

- [ ] **Apply-then-commit:** `mark_processed()` is called only after the ledger
      has durably applied the update. Confirm `is_duplicate()` has no side effect.
- [ ] **Crash recovery rehearsed:** kill the consumer between receipt and commit,
      restart, and confirm catch-up re-delivers the event.
- [ ] **Dedupe state is bounded:** `max_tracked_signatures` is sized for expected
      session volume and eviction has been exercised.

## Gap reconciliation

- [ ] **Both endpoints queried:** `/orders` (today's + open, no `since`) **and**
      `/historicalorders?since={date}` (closed only). Confirm an order that opened
      and closed inside the outage window is recovered.
- [ ] **`since` is a date string,** derived from broker event timestamps rather
      than the local clock, and clamped to 90 days.
- [ ] **Pagination is complete:** `nextToken` is followed until exhausted.
      Confirm recovery is not truncated at 600 orders.
- [ ] **Quota respected:** catch-up polling stays within 320 requests / rolling
      5 minutes, and no more than 40 concurrent order streams are opened.

## Testing

- [ ] **Automated tests:** run
      `python -m unittest discover -s skills/tradestation-websocket-order-updates/scripts`
      and confirm a 100% pass rate.
- [ ] **Repo suites:** `python tools/validate_skills.py` and
      `python tools/run_all_tests.py` pass.
- [ ] **Paper rehearsal:** disconnect mid-session against `sim-api` with a working
      order open, reconnect, and reconcile the ledger against the broker's own
      order history.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
