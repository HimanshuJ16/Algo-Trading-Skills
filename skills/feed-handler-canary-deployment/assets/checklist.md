# Pre-Flight / Sign-off Checklist — feed-handler-canary-deployment

Use this before promoting a feed handler release past its canary phase.

## Setup

- [ ] **Independent State:** $V_{\text{canary}}$ and $V_{\text{stable}}$ decode the same
      stream but share no mutable order book or cache.
- [ ] **Capacity & Entitlement:** the second full-universe consumer is provisioned and
      permitted by the venue's market data agreement.
- [ ] **Message-Identity Pairing:** audited pairs are matched by sequence number (or
      symbol + exchange timestamp), never by arrival order.
- [ ] **Deterministic Allocation:** routing buckets on a fixed digest, not on Python's
      salted `hash()`. Verified identical across two processes with different
      `PYTHONHASHSEED`.
- [ ] **Representative Whitelist:** `canary_symbols` pins deliberately awkward instruments
      (sub-dollar, suffixed, illiquid, halted, newly listed) — not only mega-caps.
- [ ] **Live Routing:** the publisher calls `route_symbol()` per tick; no routing table is
      cached at startup.

## Audit configuration

- [ ] **Tolerance:** `price_tolerance` is `0.0`, or a non-zero value has a written
      justification for why the two decoders legitimately differ.
- [ ] **Corrupt-Value Handling:** `NaN`, `±Inf`, zero and negative prices on **either**
      side are counted as mismatches.
- [ ] **Sample Gate:** `min_ticks_before_rollback` is set from this feed's tick rate, not
      left at the harness default of 10.
- [ ] **Exception Budget:** `max_allowed_exceptions` is 0 unless a documented decision says
      otherwise.

## Breaker and rollback

- [ ] **Auto-Rollback Verified:** an injected mismatch burst and an injected decoder
      exception each trip the breaker in a staging replay.
- [ ] **Revert Is Total:** after a rollback every symbol routes to `V_stable` with
      `reason == "rolled_back"`.
- [ ] **No Retry Path:** `set_canary_percentage()` raises after a rollback; nobody has
      added a reduced-percentage retry.
- [ ] **Idempotent:** repeated rollbacks retain the first reason and record one event.

## Evidence

- [ ] **Written Criteria:** promotion and rollback criteria, the observation window in
      message counts and session events, and the named authoriser were fixed **before**
      the canary started.
- [ ] **Event Record Exported:** `router.events` — timestamped, attributed `RAMP` /
      `PROMOTION` / `ROLLBACK` records — is retained with the change management evidence.
      For in-scope EU/UK firms see `references/standards.md` §3.
- [ ] **Session Coverage:** the observation window included the open auction, at least one
      halt/resume if the universe saw one, and the close.
- [ ] **Automated Testing:** `python -m unittest discover -s skills/feed-handler-canary-deployment/scripts`
      — 100% pass rate.

## Sign-off

- Release / candidate version: ___________________________
- Reviewed by: ___________________________
- Authorised by: ___________________________
- Date: ___________________________
