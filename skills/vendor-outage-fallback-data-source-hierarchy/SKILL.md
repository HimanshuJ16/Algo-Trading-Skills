---
name: vendor-outage-fallback-data-source-hierarchy
description: >-
  Use when a strategy prices off market data from more than one vendor and must
  keep deciding, continuously, which source it is entitled to trade on — covering
  priority-ranked failover, staleness and disconnect detection, promotion that
  requires demonstrated stability rather than elapsed time, and a bounded
  last-known-price cache that never presents an obsolete quote as a current one.
domain: algorithmic-trading
subdomain: real-time-architecture
tags:
- real-time-architecture
- vendor-outage
- fallback-hierarchy
- failover
- staleness-detection
- anti-flapping
- synthetic-cache
brokers_frameworks:
- Bloomberg B-PIPE
- LSEG Real-Time (formerly Refinitiv Elektron)
- Polygon.io
- Nasdaq TotalView-ITCH (direct exchange feed)
- MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589), Articles 4 and 14
- SEC Rule 2a-5 (17 CFR 270.2a-5) pricing-service oversight
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when the same instrument is available from **more than one vendor at
different tiers of quality and cost** — a direct exchange feed backed by an enterprise
aggregator backed by a cloud REST feed — and a live process must answer one question on
every tick:

> Which source am I entitled to price off right now, and if it is not a live one, how
> old is what I am about to hand the strategy?

Those are two answers, and a fallback hierarchy that only produces the first is the
dangerous kind. The engine ranks sources by priority, measures each one's liveness,
switches away from a failed source immediately, requires a recovering source to prove
itself before it takes routing back, and serves a bounded last-known-price cache only
while that price is young enough to be worth serving.

## When NOT to Use

- **To decide whether two vendors agree.** This engine picks *a* source; it never checks
  that the sources are consistent with each other. A vendor can be perfectly live and
  perfectly wrong. Cross-vendor divergence is `market-data-feed-arbitration-across-vendors`
  (two sources) and `multi-source-price-reconciliation-tie-breaking` (three or more).
- **For A/B line arbitration on a single exchange feed.** Two UDP lines carrying one
  sequence space are arbitrated losslessly by sequence number, not by priority ranking.
  See `sequence-number-gap-detection-for-feeds`.
- **As a risk control.** It reports `is_synthetic` and `age_seconds`; it does not size,
  block or flatten anything. Wire those into
  `graduated-response-to-data-quality-degradation` or
  `kill-switch-and-drawdown-circuit-breakers` to make them act.
- **As a transport-layer reconnect.** Failing over to Priority 2 does not repair
  Priority 1. Something else must reconnect the socket, resubscribe and replay the gap:
  `websocket-reconnection-with-state-recovery`, `graceful-degradation-to-polling-fallback`.
- **To choose a valuation source of record.** Routing preference is an operational
  decision made in milliseconds; a golden source for books and records is a governance
  decision. See `reference-data-golden-source-designation`.
- **Across processes.** State is in-memory and lock-guarded within one process. Two
  processes keep two independent hierarchies, two caches and two promotion timers.

## Prerequisites

- Two or more vendor feeds for the same instrument, on the **same price basis** (last
  trade against quote midpoint diverges permanently by roughly half a spread; normalise
  first with `multi-exchange-feed-normalization`), and a per-vendor entitlement covering
  the intended use.
- A **local monotonic clock** as the measurement basis. Staleness and the promotion
  window are durations, and a duration measured against vendor timestamps measures clock
  skew between two machines instead (`clock-skew-correction-for-tick-timestamps`).
- A feed handler that calls `record_heartbeat` on **every** message, and
  `mark_disconnected` on socket close or session logout.
- A supervisor that calls `evaluate_health_and_failover` on a cadence **well below the
  tightest `max_staleness_seconds`**. The engine has no timer; between calls it cannot
  notice that every feed has stopped.
- A `fetch_func` that reads an **already-received** tick from the feed handler's own
  memory. It runs while the engine lock is held, so a synchronous network call inside it
  stalls every other thread's heartbeat recording.
- Per-vendor staleness limits measured from your own recorded inter-tick gaps, per
  instrument and per session. Every threshold shipped here is an engineering default.

## Workflow

1. **Rank the sources and register them.** Priority 1 is the source you would use if
   everything worked; higher numbers are progressively cheaper, slower or less direct.
   - **Decision point — a registered source is not a working source.** A node starts
     `DISCONNECTED` and is ineligible for routing until its first heartbeat. A hierarchy
     that treats registration as evidence of health will route the whole strategy to a
     vendor whose session was never established, and discover it on the first fetch.
   - **Decision point — a duplicate `source_id` is refused, not merged.** Silently
     replacing a registered vendor is how a hierarchy that reads three deep turns out to
     be one deep on the day it matters.

2. **Feed liveness in continuously.** `record_heartbeat(source_id)` on every tick or
   heartbeat; `record_error(source_id, msg)` on a timeout or protocol fault;
   `mark_disconnected(source_id, reason)` on a socket close.
   - **Decision point — pass the vendor's timestamp for the audit trail, never as the
     liveness measurement.** `record_heartbeat(timestamp=...)` records the vendor stamp
     and measures age on the local monotonic clock. Subtracting a vendor stamp from
     local time makes a correctly working feed look stale by exactly the clock offset
     between the two machines.
   - **Decision point — a socket close is faster evidence than a staleness timer.** If
     the transport already knows the session is gone, say so; waiting out
     `max_staleness_seconds` first spends that whole window routing to a dead feed.

3. **Evaluate: measure every node, then take the highest-priority healthy one.** A node
   is unusable if it is inactive, explicitly disconnected, has never beaten, has spent
   its error budget, or has not beaten within `max_staleness_seconds`.
   - **Decision point — staleness is the check that catches a frozen feed.** A feed can
     hold a price for minutes with the TCP connection perfectly open. Socket-error
     handling alone will never notice.
   - **Decision point — refresh every node, not just those above the first healthy
     one.** Stopping at the first match leaves `status` on the tiers below holding a
     value measured at some unknown earlier time, and that field is what the dashboard,
     the alert and the on-call engineer read.

4. **Fail over immediately; promote only on demonstrated stability.** Losing the active
   source switches on the spot. Taking routing *back* from a still-working source
   requires the challenger to have been continuously healthy for
   `recovery_cooling_seconds`, with any interruption restarting the window.
   - **Decision point — "time since the last failover" is not an anti-flap rule.** After
     a ten-minute outage that clock has long since expired, so a single heartbeat from a
     vendor that is still coming back up instantly recaptures routing — precisely when a
     recovering feed is least trustworthy. Require the run of health, not the elapsed
     time.
   - **Decision point — the hold lapses the moment the incumbent stops being healthy.**
     Holding a source you have just measured as stale, in order to avoid a switch, hands
     the strategy a dead feed while a working one sits idle. Anti-flap protects a
     *working* incumbent and nothing else.

5. **Fetch, validating the quote before trusting it.** `fetch_market_data_tick` walks the
   hierarchy, attempting each source **at most once per call**, and charges a raising or
   invalid vendor an error before moving down.
   - **Decision point — reject a `NaN` price rather than pass it on.** `NaN` compares
     `False` against every threshold downstream and turns spread, size and P&L into
     `NaN` without raising anything. Caching it poisons the fallback for the rest of the
     outage.
   - **Decision point — a hard `price > 0` filter is wrong for some instruments.** WTI
     crude for May 2020 settled at **-$37.63/bbl** on 20 April 2020. Leave
     `allow_non_positive_prices=False` for equities, FX and crypto; set it `True` for
     energy futures and spreads, and only there.

6. **Fall back to the cache only within its age bound.** With no live source, the last
   valid quote is served with `is_synthetic=True`, `age_seconds` set, and `timestamp`
   holding the moment the price was **observed**, not the moment the object was built.
   Past `max_synthetic_age_seconds` the engine raises instead.
   - **Decision point — pass `allow_synthetic=False` wherever an obsolete price is worse
     than no price.** Order placement, mark-to-market and risk-limit checks are all in
     that category. Serving a cached price into an order router is how an outage becomes
     a fill at a price that stopped existing several minutes ago.

7. **Test the hierarchy rather than assume it.** MiFID II RTS 6 Article 14(4) requires
   in-scope firms to review and test business continuity arrangements **annually**. A
   fallback tier that has never been exercised is a hypothesis.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Stamping a cached tick with the current time.** It defeats every downstream
  staleness check at once: the consumer asks how old the price is, the tick answers
  "zero", and the strategy trades a quote from the start of the outage. `timestamp` must
  be the observation time and `age_seconds` must be populated.
- **An unbounded cache of last resort.** Without a maximum age the fallback quietly
  degrades from "covers a 30-second blip" to "prices the book off a number from an hour
  ago". The bound is what makes the tier safe, not the cache itself.
- **Measuring durations on the wall clock.** An NTP step backwards makes a frozen feed
  read as fresh — the arithmetic goes negative and every comparison passes. A step
  forwards makes every vendor breach its staleness limit in the same instant and dumps
  the process onto the synthetic cache with nothing actually wrong. Interval measurement
  belongs on a monotonic clock; wall-clock stamps are for the audit trail.
- **Promoting on elapsed time instead of stability.** See workflow step 4. The symptom
  is a recovering vendor that recaptures routing, drops again within seconds, and drags
  the strategy across a cross-vendor price discontinuity on every cycle.
- **Letting the anti-flap hold pin routing to a dead source.** The bug is subtle because
  the hold is correct in isolation: it only becomes dangerous when the incumbent fails
  *during* the hold. Check the incumbent's measured health before deciding to stay.
- **Treating registration as connection.** A node that has never produced a heartbeat is
  not healthy; it is unmeasured. Optimistic defaults put unmeasured vendors at the top
  of the routing order.
- **Retrying a failing source by recursion.** The depth is the sum of the remaining
  error budgets, so a generous `max_error_threshold` turns a vendor outage into a
  `RecursionError`. Attempt each source once per call and let the hierarchy do the rest.
- **Assuming failover repairs anything.** The engine routes around a broken feed; it
  does not reconnect it, resubscribe it or replay its gap. Without a separate recovery
  path you permanently ratchet down the hierarchy, one vendor per incident.
- **Reading `volume` off a synthetic tick.** It is `0.0`, meaning "no volume
  information", not "nothing traded". Replaying the cached figure would let the same
  flow be counted repeatedly for as long as the outage lasts.
- **Doing network I/O inside `fetch_func`.** The engine lock is held across it, so a
  blocking vendor call serialises every other thread. Worse, a heartbeat delayed behind
  that lock is stamped when it *acquires* the lock, not when the message arrived — which
  makes the feed read fresher than it is, biasing the staleness measurement in the one
  direction that hides a problem.
- **Copying the shipped thresholds into production.** `2.0s` / `5.0s` / `10.0s`
  staleness, a `30.0s` promotion window and a `30.0s` cache bound are engineering
  defaults. A feed whose normal inter-tick gap is three seconds is permanently stale
  under them.

## Verification

- A registered source with no heartbeat is `DISCONNECTED`; the engine reports
  `ALL_SOURCES_DOWN` and `fetch_market_data_tick` raises. One heartbeat makes it
  `PRIMARY_ACTIVE`, logged as `INITIAL_SELECTION`, not as a failover.
- Staleness boundary is exclusive: at exactly `max_staleness_seconds` the node is still
  `HEALTHY`; one millisecond later it is `STALE` and the engine moves to Priority 2.
- Promotion boundary: with a `30.0s` window, a recovered Priority 1 is still held at
  `29.0s` of unbroken health and takes routing at exactly `30.0s`. An interruption at
  `25.0s` restarts the window — 55s of total elapsed health does not promote.
- After a long Priority 1 outage, a single heartbeat does **not** promote.
- Two sources registered at the same priority do not swap while the incumbent is
  healthy and the challenger has not served the window.
- With Priority 1 healthy and Priority 2 stale inside the promotion window, a fetch is
  routed to Priority 1 — the hold never selects a source measured `STALE`.
- A synthetic tick after a 45-second outage reports `age_seconds == 45.0` and a
  `timestamp` equal to the original observation time. At `max_synthetic_age_seconds` it
  is still served; one millisecond past, `FallbackEngineError` is raised.
  `allow_synthetic=False` refuses it at any age.
- `NaN`, `±inf`, non-positive (unless enabled), negative-volume, non-numeric, boolean
  and wrong-arity vendor returns are each rejected, charged to the source, and never
  cached.
- A vendor that always raises is attempted exactly once per source per call — three
  registered sources produce three calls, whatever `max_error_threshold` is set to.
- A timezone-aware heartbeat is accepted; a vendor stamp an hour behind local time does
  not make a live feed read as stale.
- Event ids are unique across transitions landing in the same wall-clock second; the
  event log is bounded by `max_event_log_entries`.
- Run `python -m unittest discover -s skills/vendor-outage-fallback-data-source-hierarchy/scripts`.

## Related Skills

- `market-data-feed-arbitration-across-vendors`
- `multi-source-price-reconciliation-tie-breaking`
- `graduated-response-to-data-quality-degradation`
- `graceful-degradation-to-polling-fallback`
- `sequence-number-gap-detection-for-feeds`
- `clock-skew-correction-for-tick-timestamps`
- `reference-data-golden-source-designation`
- `market-data-latency-monitoring-per-vendor`
- `smart-order-router-failover-on-venue-outage`
- `kill-switch-and-drawdown-circuit-breakers`
