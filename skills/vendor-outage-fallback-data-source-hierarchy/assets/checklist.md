# Vendor Fallback Hierarchy — Pre-Flight Checklist

Sign off before this engine routes production prices. Date and retain the completed
sheet: for firms in scope of MiFID II RTS 6, Article 14(4) requires business continuity
arrangements to be reviewed and tested annually, and this is that artefact.

Skill: `vendor-outage-fallback-data-source-hierarchy` · Version 2.0.0

## 1. Hierarchy design

- [ ] **Tiers are genuinely independent.** Each vendor has been asked what their upstream
      source is. Two vendors reselling the same feed are one tier, not two.
- [ ] **Every tier is warm.** Fallback sessions are connected and beating *before* an
      incident, not established on demand.
- [ ] **Same price basis across tiers.** Last-trade against quote-midpoint has been
      normalised, or the divergence at failover is understood and accepted.
- [ ] **Entitlements cover fallback use.** Each vendor agreement permits the intended
      purpose on the tier where it sits, including use as a backup.
- [ ] **Priorities are unique, or ties are deliberate.** Two sources at equal priority
      order by `source_id`; confirm that is what you want.
- [ ] **Tier-3 vendor viability reviewed.** Cloud data vendors are retired with months of
      notice, not years — IEX Cloud gave three. Re-check annually.

## 2. Threshold calibration (none of these are standards)

- [ ] **`max_staleness_seconds` measured, not guessed.** Set per vendor *and per
      instrument class* from recorded normal inter-tick gaps, including the quiet part of
      the session.
- [ ] **`max_error_threshold` set per vendor.** Remember the budget decays one per good
      message, so it counts roughly-consecutive failures.
- [ ] **`recovery_cooling_seconds` set.** Long enough that a flapping vendor cannot
      recapture routing; short enough that you are not paying tier-3 latency for hours
      after a blip.
- [ ] **`max_synthetic_age_seconds` set deliberately.** This is the number that decides
      how long you are willing to trade off a price nobody is refreshing. Justify it.
- [ ] **`allow_non_positive_prices` matches the instruments.** `False` for equities, FX
      and crypto. `True` only where prices can legitimately print at or below zero —
      energy futures, calendar spreads.

## 3. Integration

- [ ] **`record_heartbeat` is called on every message**, not on a timer of its own.
- [ ] **`mark_disconnected` is wired to socket close and session logout.** The transport
      knows before any staleness timer does.
- [ ] **A supervisor calls `evaluate_health_and_failover`** at an interval well below the
      tightest `max_staleness_seconds`. The engine has no timer; without this a total
      stall is invisible.
- [ ] **Vendor timestamps are passed for audit only.** Nothing computes an age by
      subtracting a vendor stamp from local time.
- [ ] **`allow_synthetic=False` on every order, margin and risk-limit path.** A cached
      price into an order router is how an outage becomes a bad fill.
- [ ] **Consumers branch on `is_synthetic` and `age_seconds`**, not on `source_id`
      string matching.
- [ ] **`volume` on a synthetic tick is not consumed.** It is `0.0` meaning "unknown",
      not "nothing traded".
- [ ] **A reconnect path exists separately from failover.** Routing around a dead feed
      does not repair it; without recovery you ratchet down one tier per incident,
      permanently.
- [ ] **Events are persisted externally.** The in-memory ring is telemetry, bounded by
      `max_event_log_entries`, not the system of record.

## 4. Failover drill (run before go-live, then at least annually)

Full procedure in `references/workflows.md`, Workflow 5. Record measured latencies.

- [ ] **Cold start** — no heartbeats: `ALL_SOURCES_DOWN`, fetches raise, no failover
      event written.
- [ ] **Frozen tier 1** — heartbeats stopped, socket left open: staleness triggers the
      switch. Detection latency recorded: ________
- [ ] **Hard disconnect on tier 1** — switch is faster than the staleness path.
- [ ] **Cascading loss of tiers 1 and 2** — tier 3 reached in a single pass, each source
      attempted once.
- [ ] **Total outage** — synthetic tick carries `is_synthetic=True`, non-zero
      `age_seconds` and an observation `timestamp`; an `allow_synthetic=False` caller is
      refused.
- [ ] **Cache expiry** — outage held past `max_synthetic_age_seconds`: the engine raises,
      and the consuming system degrades safely when it does.
- [ ] **Flapping recovery** — interrupted heartbeats on tier 1 do not recapture routing;
      an incumbent failure during the hold releases it immediately.
- [ ] **Bad ticks** — `NaN`, `inf`, zero price and negative volume are each rejected,
      charged as an error, and absent from the cache.
- [ ] **Clock step** — host wall clock stepped forwards and backwards by minutes
      mid-run: no state change.

## 5. Sign-off

| Item | Value |
|---|---|
| Drill date | |
| Participants | |
| Tier 1 / 2 / 3 vendors and versions | |
| Measured detection latency per tier | |
| Deviations found | |
| Remediation owner and date | |
| Next scheduled drill | |
