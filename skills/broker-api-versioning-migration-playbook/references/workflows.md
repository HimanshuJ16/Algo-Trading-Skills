# Institutional API Migration Workflows

Migrating a live order path is phase-gated for one reason: during the migration two API
versions are simultaneously live against the same account, and ordinary failures —
timeouts, retries, replica restarts — can now resolve onto a *different version* than
the one that saw the original request. Each phase below exists to remove one class of
that ambiguity before the next phase adds risk.

Durations are calibrated defaults, not standards. Set them from your own instrument
liquidity, session structure, and order volume.

---

## Phase 0: Preparation (before any code moves)

- **Read the target schema.** Not the SDK wrapper, not the changelog summary — the
  request/response reference for the version you are moving to. Diff it against the
  current version mechanically (`broker-api-changelog-diffing-tool`) and then read the
  diff by hand for semantic changes a structural diff cannot see: rounding, fee tiers,
  matching behaviour, rate limits, error-code meanings.
- **Enumerate every call site.** Reads, writes, cancels, modifies, streaming
  subscriptions, reconciliation queries. A migration that moves order placement but
  leaves cancel on the old version is the version-mismatch bug, deliberately shipped.
- **Confirm client order ids are stable per intent.** If retries currently mint a new
  id, fix that first — deterministic canary routing depends on it, and so does the
  broker's own de-duplication.
- **Confirm the risk layer sits above the router.** Every pre-trade control must apply
  identically to both branches.
- **Where mandated, complete venue conformance testing** in a non-production
  environment (RTS 6 Articles 6 and 7 for EU-authorised investment firms). Nothing
  below substitutes for it.
- **Agree the abort criteria and who may invoke them,** before the first order moves.

---

## Phase 1: V1 baseline (`V1_ONLY`)

- **Objective**: establish what "normal" is, so later comparisons have a referent.
- **Action**: all reads and writes on V1. No behavioural change. Latency timing is
  active — `execute_read_shadowing` times the V1 call in this phase too.
- **Collect**: read-latency mean/p50/p95/p99, order acceptance and rejection rates,
  error-code distribution, message volume per session.
- **Exit criteria**: enough samples for the percentile gate you intend to use (default
  ≥ 1000 reads per version), spanning at least one full session including open and
  close. Tail latency at the open does not resemble tail latency at midday.

---

## Phase 2: Shadow reads (`SHADOW_MODE`)

- **Objective**: prove the V2 *read* surface is structurally equivalent and not slower,
  with zero exposure on the write path.
- **Action**:
  - Writes remain 100% on V1. This is enforced by the router, not by convention.
  - Reads execute on V1 on the calling thread and return immediately; the V2 replica
    runs on a bounded background pool and is dropped when that pool saturates.
  - Each version's latency is timed around its own call.
  - V2 responses feed the schema differ and are discarded from application logic.
- **Watch for**:
  - `missing_in_v2` / `type_mismatches` by dotted path — including nested paths.
  - `unverified_paths`. A clean run with many unverified paths has proved little; drive
    traffic that populates those fields (place a test order, hold a position) before
    reading the gate as passed.
  - `shadow_errors`. A V2 endpoint that is simply failing produces *no* drift, because
    nothing is being compared. Silence is not equivalence.
  - `shadow_shed`. Persistent shedding means V2 is slow enough to saturate the pool,
    which is itself a finding.
- **Exit criteria**: zero unresolved schema drift on every endpoint on the order path,
  across at least two full trading sessions; V2 latency within tolerance with
  `percentiles_reliable` true on both sides; `shadow_errors` at or near zero.
- **Abort**: any drift on a field the strategy or risk layer actually reads. Fix the
  downstream code, or the mapping, and restart the clock.

---

## Phase 3: Canary cutover (`CANARY_CUTOVER`)

- **Objective**: expose the write path incrementally, with an abort criterion at every
  step.
- **Action**:
  - Start at 1%. Percentages are fractions in [0, 1] and out-of-range values are
    rejected outright — `50` is not 50%.
  - Routing is a stable hash of the client order id, so a retry returns to the same
    version and every replica agrees.
  - **Cancels and modifies route by affinity, not by hash.** Look the order up; if the
    binding is unknown, query both versions. A ramp re-buckets orders, so recomputing
    the hash after a ramp can aim a cancel at a version that never saw the order.
  - Record every order outcome so the error-rate gate has data.
- **Suggested ramp** (calibrated): 1% → 5% → 25% → 50% → 100%, holding each step for at
  least one full session and crossing at least one open and one close before 100%.
  Ramping only through quiet midday hours tests the easy case.
- **Watch for**: V2 rejection rate versus the V1 baseline, fill quality and slippage on
  V2-routed orders, latency divergence, any order whose state cannot be resolved from
  the version it was routed to.
- **Abort immediately on**: rejection-rate breach, a fill that cannot be reconciled to
  the routed version, evidence of a duplicate order, or any ambiguity about which
  version holds a working order.

---

## Phase 4: Full cutover (`V2_ONLY`)

- **Objective**: run exclusively on V2.
- **Action**: 100% of reads and writes on V2, with the V1 code path still deployed and
  the rollback still armed.
- **Watch for**: anything that only appears at full volume — rate limiting, connection
  pool exhaustion, throttling behaviour that 50% of flow never reached.
- **Note**: orders placed on V1 before the cutover may still be working. Their cancels
  and modifies must continue to route to V1 until they are terminal. Do not decommission
  V1 while V1-placed orders are live.

---

## Phase 5: Decommission

- **Not before**: an agreed stability period at 100% (default: two weeks of trading
  sessions), *and* zero working orders placed on V1, *and* zero V1 traffic observed in
  the routing counters.
- **Then**: remove the V1 path, retire its credentials, and delete the affinity map's
  reason for existing. Update the runbook to say the rollback no longer exists — an
  operator reaching for a rollback that was deleted last sprint is a worse outage than
  the one they were trying to fix.

---

## Rollback (`ROLLBACK_V1`) — reachable from any phase

- **Trigger**: automatically on a breached policy threshold, or manually from the desk.
- **Effect**: new order flow returns to V1 on the next routing decision. It is an
  in-process state change; no restart, no redeploy.
- **What it does not do**: it does not pull working orders. Orders already placed on V2
  are still live and must be managed on V2. If the situation calls for withdrawing
  outstanding orders, that is the kill switch — a separate control
  (`kill-switch-and-drawdown-circuit-breakers`; RTS 6 Article 12 for firms in scope).
- **Latched**: the migration cannot resume until an operator explicitly clears it with a
  reason, and it then restarts from the gate sequence rather than the percentage that
  failed.
- **After a rollback**: reconcile every order routed to V2 during the incident window
  before resuming anything. The rollback stops new exposure; it does not tell you what
  the old exposure did.
