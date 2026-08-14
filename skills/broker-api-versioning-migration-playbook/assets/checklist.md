# API Migration Sign-Off Checklist

One pass per migration. Durations in brackets are calibrated defaults — replace them
with figures derived from your own baseline.

## Pre-migration — schema and code

- [ ] Target-version request/response schema read **from the broker's own reference**,
      not from an SDK wrapper, changelog summary, or prior integration.
- [ ] Every emitted field verified to exist in that schema. No field included because it
      looked consistent with the naming convention.
- [ ] Field relocations checked: identifier renames, values moving between the top level
      and nested objects, numeric values becoming strings.
- [ ] Translator raises — never substitutes — for any order type / time-in-force
      combination the target version cannot express.
- [ ] Translator raises on a missing required price. Confirm a legitimate `0.0` is not
      treated as "missing".
- [ ] Prices and sizes serialised through `Decimal` in fixed-point form. Confirm
      `0.00001` renders as `0.00001`, not `1e-05`.
- [ ] Every call site enumerated: reads, writes, cancels, modifies, streaming
      subscriptions, reconciliation queries.
- [ ] Client order ids confirmed stable per order *intent* and reused across retries.
- [ ] Pre-trade risk controls confirmed to sit **above** the version router, applying
      identically to both branches.
- [ ] Venue conformance testing completed in a non-production environment where mandated
      (RTS 6 Arts. 6 and 7 for EU-authorised investment firms).
- [ ] Rollback thresholds calibrated from the V1 baseline, not adopted from a document.
- [ ] Abort criteria agreed in writing, and the people authorised to invoke them named.
- [ ] Emergency kill switch confirmed **separate** from the migration rollback, and
      confirmed still functional on both API versions.
- [ ] `python -m unittest discover -s skills/broker-api-versioning-migration-playbook/scripts`
      passes.

## Phase 1 — V1 baseline (`V1_ONLY`)

- [ ] Read-latency mean, p50, p95, p99 captured and stored.
- [ ] Order acceptance/rejection rates and error-code distribution captured.
- [ ] Sample count sufficient for the intended percentile gate (default ≥ 1000 per
      version); `percentiles_reliable` is true.
- [ ] Baseline spans at least one full session including the open and the close.

## Phase 2 — Shadow reads (`SHADOW_MODE`)

- [ ] Phase set to `SHADOW_MODE`; confirmed that 100% of **writes** are still routing to
      V1.
- [ ] Confirmed the production read path is not blocked by a slow shadow (measure it:
      stall the V2 endpoint and check the V1 read still returns promptly).
- [ ] Run for at least two full trading sessions [48 h].
- [ ] `audit_totals.drifted` is zero for every endpoint on the order path.
- [ ] `missing_in_v2` and `type_mismatches` reviewed by dotted path, nested paths
      included.
- [ ] `unverified_paths` reviewed. Traffic driven that populates the null/empty fields;
      a pass built on unpopulated payloads is not a pass.
- [ ] `shadow_errors` at or near zero — a failing V2 endpoint produces no drift because
      nothing is compared.
- [ ] `shadow_shed` not persistently rising.
- [ ] `latency_tracker.compare()` returns `within_tolerance is True` — **not `None`**.
      `None` means undecided, which is not a pass.

## Phase 3 — Canary cutover (`CANARY_CUTOVER`)

- [ ] Confirmed the canary percentage is a fraction: `0.01` for 1%. An out-of-range
      value must raise, not clamp.
- [ ] Confirmed routing is deterministic: the same client order id routes identically
      across repeated calls and across independent processes.
- [ ] Confirmed cancels and modifies route by **affinity**, and that an unknown affinity
      triggers a query of both versions rather than a default.
- [ ] Order outcomes being recorded so the error-rate gate has data.
- [ ] 1% — hold ≥ 1 h; reject rate and fill quality compared against the V1 baseline.
- [ ] 5% — hold through one market close.
- [ ] 25% — hold through one market open.
- [ ] 50% — hold ≥ 1 full session.
- [ ] 100% — hold ≥ 1 full session before declaring `V2_ONLY`.
- [ ] At every step: no duplicate orders, no fill unattributable to the routed version,
      no order whose holding version is ambiguous.
- [ ] Rollback rehearsed at least once at a low percentage, and the latch confirmed to
      block a resumed ramp until explicitly cleared.

## Phase 4 — Full cutover (`V2_ONLY`)

- [ ] Phase set to `V2_ONLY`; routing counters confirm zero new V1 traffic.
- [ ] V1 code path **still deployed** and rollback still armed.
- [ ] Behaviour observed at full volume: rate limits, connection pools, throttling.
- [ ] Any orders placed on V1 before cutover identified, and their cancels/modifies
      confirmed still routing to V1 until terminal.

## Phase 5 — Decommission

- [ ] Stability period at 100% elapsed [2 weeks of sessions].
- [ ] Zero working orders remaining that were placed on V1.
- [ ] Zero V1 traffic in the routing counters over the full period.
- [ ] V1 code path removed and its credentials retired.
- [ ] Runbook updated to record that the rollback no longer exists.

## If a rollback fires

- [ ] Rollback reason recorded, with the metric that breached.
- [ ] Confirmed the latch is holding — no automated ramp has resumed.
- [ ] **Every order routed to V2 during the incident window reconciled against the
      broker's order state.** Rollback stops new exposure; it says nothing about what
      the existing exposure did.
- [ ] Assessed separately whether working orders need to be pulled — that is the kill
      switch, not the rollback.
- [ ] Root cause identified before `clear_rollback` is called.
- [ ] Migration restarted from the gate sequence, not resumed at the percentage that
      failed.
