---
name: broker-api-versioning-migration-playbook
description: >-
  Use when moving live order flow from one broker API version to the next: shadow reads,
  deterministic per-order canary routing that survives retries, latched rollback, and a
  translator that refuses to silently change time-in-force.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, api-migration, canary-deployment, shadow-traffic, schema-drift, rollback, deployment-ops
  brokers_frameworks: "Coinbase Advanced Trade API (CreateOrder); MiFID II RTS 6 (Reg (EU) 2017/589); SEC Rule 15c3-5; Python Trading Engine"
  version: "3.0.0"
  author: algo-trading-skills-contributors
---

# Broker API Versioning & Migration Playbook

## When to Use

Invoke this skill when a live trading system must move from one broker API version to
another — a REST v1→v2 cutover, a new FIX session profile, an SDK major bump that
changes payload shapes, or a forced migration off an endpoint with a published sunset
date. It covers the migration *mechanism*: which version each request goes to, how read
equivalence is proved before writes move, and how the cutover is aborted.

The migration window is the dangerous part, not the destination. During it, two API
versions are simultaneously live against the same account, and every ordinary failure —
a timeout, a retry, a redeployed replica — can now resolve onto a *different version*
than the one that saw the original request. The three rules below exist because each has
a specific way of putting two orders in the market where the strategy intended one:

> **A retry must reach the same version as the original attempt.**
> **A cancel must reach the version that holds the order.**
> **A target-version field you have not read in the spec does not exist.**

That last one is not hypothetical. A translator emitting a Coinbase `order_configuration`
key called `stop_stop_gtc` is inventing one: no such key exists. The published CreateOrder
schema has `stop_limit_stop_limit_gtc`, and it requires `limit_price`, `stop_price` and
`stop_direction` together. Every stop order translated by such code is rejected by the
venue — or worse, silently reshaped by a permissive gateway.

## When NOT to Use

- **As the thing that decides whether V2 is correct.** This proves *structural*
  equivalence of read responses and tracks write-path error rates. It cannot tell you
  that V2's matching semantics, rounding, fee schedule, or rate limits changed. Use
  `broker-api-changelog-diffing-tool` on the published specs and read the release notes.
- **As a pre-trade risk control.** The version router must sit *below* your risk layer,
  never in front of it, so neither canary branch can bypass a limit check. See
  `sec-rule-15c3-5-risk-controls-us` and `kill-switch-and-drawdown-circuit-breakers`.
- **As a substitute for the broker's own test environment.** Shadow mode replicates
  *reads*. It is not conformance testing, and for EU-authorised investment firms it does
  not discharge RTS 6 Article 6 — see Prerequisites.
- **As a durable order ledger.** The version-affinity map is in-memory, per-process and
  bounded. A resting order older than the eviction window will return `None`, meaning
  "reconcile", not "route to V1".
- **For a same-version SDK upgrade with no wire change.** That is an ordinary deploy —
  use `canary-releases-for-strategy-code-changes` or
  `blue-green-deployment-for-live-strategy-updates`.

## Prerequisites

- **The target version's actual request/response schema**, read from the broker's
  reference — not inferred from the old version, an SDK wrapper, or a blog post. Field
  renames (`instrument_id` → `product_id`), relocations (top-level `size` →
  `order_configuration.base_size`) and type changes (numeric price → **string** price)
  are the common shape of a v1→v2 break.
- **A V1 baseline captured before the migration starts.** Mean and p99 read latency,
  order rejection rate, and error-code mix, measured in `V1_ONLY`. Without it, "V2 is 5%
  slower" has no referent. Percentile gates need volume: a p99 estimated from 200
  samples rests on two observations.
- **Stable client order ids**, one per order *intent*, reused across retries of that
  intent. This is both the broker's de-duplication key and the canary routing key. See
  `order-placement-idempotency`.
- **Access to the broker's order-state stream** for reconciliation when a routing
  decision turns out to be ambiguous — see `webhook-based-order-fill-notifications`.
- **Venue conformance testing, where mandated.** EU-authorised investment firms are
  required by MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589) Article 6
  to conformance-test with the trading venue prior to the deployment or material update
  of an algorithmic trading system, and by Article 7 to test in an environment separated
  from production. A canary in production is a *complement* to that, never a
  replacement. US broker-dealers with market access must keep 15c3-5(c)(1)(i) pre-trade
  controls applied to both branches.

## Workflow

1. **Baseline in `V1_ONLY`.** Route everything to V1 and record read latency and order
   outcomes. `execute_read_shadowing` times the V1 call in every phase precisely so this
   baseline exists before any comparison is made.

2. **Translate — and fail loudly on anything you cannot express.** Build the V2 payload
   from the version-neutral order, and raise rather than substitute when the target
   version has no equivalent. The two substitutions that look harmless and are not:
   - **Time-in-force.** Mapping a LIMIT/IOC order onto `limit_limit_gtc` converts an
     immediate-or-cancel instruction into a resting order. The strategy believes the
     unfilled remainder is gone; it is sitting in the book.
   - **A missing price.** `str(price) if price else "0"` sends a limit price of zero.
     Note it also fires when the price is legitimately `0.0` — a falsiness check is not
     a null check.

3. **`SHADOW_MODE`: prove read equivalence.** Writes stay on V1 by definition. Reads run
   on V1 and are replicated to V2 in the background. Three properties are load-bearing:
   - The V1 result returns **as soon as V1 completes**. If the shadow can block the
     production read path, it is not a shadow.
   - Each version's latency is timed around *its own* call. Measuring V2 only after
     awaiting V1 inflates V2 by V1's duration — biasing the exact number the gate reads.
   - The comparison **recurses**. Comparing top-level keys only means a price that
     became a string inside a nested fill record passes the gate cleanly. On a gate, a
     false negative is the dangerous direction.

   Treat `is_equivalent == True` with a non-empty `unverified_paths` as *unproven*, not
   passed: a `null` field or an empty list carries no type information, and a shadow
   phase that passed because half the payload was null has proved nothing.

4. **`CANARY_CUTOVER`: move writes by order, not by call.** Derive the routing decision
   from a stable hash of the client order id, so a retry of a timed-out order returns to
   the same version and every replica agrees. A fresh random draw per call gives one
   order a new coin flip on every attempt — and the broker's de-duplication namespace on
   the *other* version has never seen the first attempt.

   Ramp percentages are fractions in [0, 1]. **Reject out-of-range values; never clamp
   them.** Clamping turns an operator typing `50` for "50%" into an instant 100% cutover
   onto the untested version.

5. **Route follow-ups by affinity, not by hash.** Determinism alone is not enough:
   ramping 5% → 25% re-buckets orders, so a cancel computed from the new percentage can
   be aimed at a version that never saw the order. Look the order up in the affinity map
   and, when it returns `None`, query both versions rather than guessing — "unknown
   order" from the wrong version is not proof the order is gone
   (`broker-api-idempotent-cancel-requests`).

6. **Gate the ramp on evidence, and abstain when there is none.** Compare V2 error rate,
   schema-drift rate, and latency against thresholds calibrated from *your* V1 baseline.
   When there are too few samples to decide, the verdict is "undecided", not "pass" — a
   gate that returns green on three observations promotes on silence.

7. **Roll back to a latch.** `ROLLBACK_V1` must not be an ordinary phase that the next
   scheduled ramp step can overwrite. Leaving it requires an explicit, logged operator
   action, and the migration restarts from the gate sequence rather than resuming at the
   percentage that just failed.

8. **`V2_ONLY`, then decommission.** Only after V2 has carried full flow across several
   sessions. Keep the V1 code path deployed until then — a rollback you have deleted is
   not a rollback.

> Full phase procedure and exit criteria: see `references/workflows.md`.
> Schema citations, thresholds, and the statistics behind the latency gate: see
> `references/standards.md`.
> Printable sign-off sheet: see `assets/checklist.md`.

## Common Pitfalls

- **Inventing a target-version field.** `stop_stop_gtc` is not a Coinbase
  `order_configuration` key; stops are stop-*limit* orders requiring `limit_price`,
  `stop_price` and `stop_direction` together. If you have not read the field in the
  spec, it does not exist.
- **Dropping `time_in_force` during translation.** An IOC or FOK order that arrives as
  GTC rests in the book, and the strategy has no idea it holds exposure.
- **`str(price) if price else "0"`.** Two bugs in one expression: it defaults a missing
  price to zero, and it treats a legitimate `0.0` as missing.
- **Serialising prices with `str(float)`.** `str(1e-05)` is `'1e-05'` and
  `str(0.1 + 0.2)` is `'0.30000000000000004'`. Format through `Decimal` with fixed-point
  notation.
- **Re-randomising the canary on every call.** The same order retried lands on a
  different version, where the broker's client-order-id de-duplication has never seen
  the first attempt. One intent, two live orders.
- **Using Python's built-in `hash()` for the routing bucket.** String hashing is salted
  per process, so replicas and restarts disagree about which orders are in the canary.
- **Clamping an out-of-range canary percentage.** `min(1.0, 50)` is `1.0`: the operator
  asked for half the flow and got all of it, instantly, on the untested version.
- **Cancelling through the hash instead of the affinity map.** After a ramp the hash
  points at the wrong version, and the resulting "unknown order" reads as "already
  cancelled" to code that is not looking for this.
- **Letting the shadow block the production read path.** A `with ThreadPoolExecutor(...)`
  block joins its workers on exit; a hung V2 endpoint then stalls live reads for as long
  as it hangs.
- **Timing the shadow call after awaiting the primary.** V2 is then charged for V1's
  latency, and the migration gate reads a number that describes neither.
- **Comparing top-level keys only.** Nested drift — the common kind — passes silently.
- **Discarding shadow-call exceptions.** A wholly broken V2 endpoint then produces no
  signal at all; the phase looks quiet because nothing is being compared.
- **Letting the audit log grow without bound.** One diff per read across a multi-session
  shadow phase is a slow memory leak in a process that must not restart mid-session.
- **Gating a p99 on a sliding window of the last N samples.** After two trading days
  those samples describe the last few minutes, not the phase you are gating.
- **Treating "not significantly worse" as "equivalent".** Failing to reject a null
  hypothesis is not evidence for it — with few samples you will always fail to reject.
- **Skipping venue conformance testing because the canary is in production.** For firms
  under RTS 6 these are different obligations, and the canary does not discharge either
  Article 6 or Article 7.
- **Deleting the V1 code path at cutover.** Rollback needs somewhere to roll back to.

## Verification

- Run `python -m unittest discover -s skills/broker-api-versioning-migration-playbook/scripts`
  and confirm all tests pass.
- Translate a STOP order and confirm the payload contains `stop_limit_stop_limit_gtc`
  with `base_size`, `limit_price`, `stop_price` and `stop_direction` — and no
  `stop_stop_gtc`. Confirm a stop missing any of those raises instead of translating.
- Translate a LIMIT order with no `limit_price` and confirm it raises rather than
  emitting `"0"`; construct one with `limit_price=0` and confirm the payload itself is
  rejected.
- Translate LIMIT/FOK and confirm it yields `limit_limit_fok`, not `limit_limit_gtc`.
- Format a price of `0.00001` and confirm the payload carries `"0.00001"`, not
  `"1e-05"`.
- Route one client order id 200 times at 50% canary and confirm a single distinct
  decision; route the same ids through a second, independently constructed migrator and
  confirm both agree.
- Call `set_phase(CANARY_CUTOVER, 50)` and confirm it raises; confirm the phase is
  unchanged afterwards.
- Attempt `V1_ONLY → V2_ONLY` and confirm it raises.
- Trigger a rollback, then attempt to set any other phase, and confirm it raises until
  `clear_rollback(operator, reason)` is called with both arguments non-empty.
- Route an order at 0% canary, ramp to 100%, and confirm `route_followup_version` still
  reports `V1`.
- Shadow a V2 call that blocks for 5 s and confirm the V1 result returns in
  milliseconds; confirm a V2 exception is counted in `shadow_errors` and never reaches
  the caller.
- Shadow a *slow V1* against an instant V2 and confirm the recorded V2 mean is not
  inflated toward V1's.
- Feed `{"order": {"fills": [{"price": 1.0}]}}` against the same structure with
  `"price": "1.0"` and confirm `order.fills.[].price` appears in `type_mismatches`.
- Feed a list-returning endpoint and confirm the comparison runs at all.
- Record 1000 V1 samples of 10 ms and 1000 V2 samples of 20 ms and confirm the
  comparison reports `within_tolerance is False`; repeat with 3 samples and confirm it
  reports `None`, not `True`.

## Related Skills

- `broker-api-changelog-diffing-tool`
- `broker-api-deprecation-notice-monitoring`
- `broker-api-idempotent-cancel-requests`
- `broker-agnostic-adapter-interface`
- `order-placement-idempotency`
- `canary-releases-for-strategy-code-changes`
- `blue-green-deployment-for-live-strategy-updates`
- `sandbox-vs-production-endpoint-drift`
- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `webhook-based-order-fill-notifications`
