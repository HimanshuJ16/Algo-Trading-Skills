---
name: strategy-specific-data-dependency-mapping
description: >-
  Map each strategy's data dependencies as a feed-level DAG with ordered vendor hierarchies,
  explicit freshness bounds, schema contracts, and per-feed block/degrade responses; audit
  observed vendor health into a readiness score that gates trading, and project the blast
  radius of a vendor outage across a multi-strategy portfolio. Use for dependency inventory,
  pre-trade readiness gating, single-point-of-failure review, and outage triage; do not use it
  as a market-data failover mechanism, a substitute for pre-trade risk controls, or evidence
  that an unobserved fallback vendor is alive.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- data-dependency
- freshness-sla
- vendor-failover
- blast-radius
brokers_frameworks:
- Broker-agnostic
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to record what data each strategy actually needs, how fresh each feed must be,
which vendors may serve it, what happens when none of them can, and which strategies a given
vendor outage would stop. Apply it during strategy onboarding, production-readiness review,
data-vendor change assessment, outage triage, and periodic single-point-of-failure review.

Do not use it as a failover mechanism. It decides whether a strategy may trade on the data it
currently has; it does not reconnect feeds, reroute subscriptions, or reconcile prices across
vendors — see `vendor-outage-fallback-data-source-hierarchy` for the failover path itself. It
is also not a pre-trade risk control: a strategy can be fully data-ready and still be over its
exposure or capital limits.

## Prerequisites

- Python 3.10+ for the dependency-free reference engine (typed with PEP 604 unions).
- An authoritative inventory of each strategy's feeds, including derived features and reference
  data, not only the obvious price feeds.
- Per feed: an ordered vendor preference list, a freshness bound chosen for *that* feed, the
  schema contract version if one is enforced, upstream feeds it is computed from, and whether
  loss of every vendor must block trading or may degrade it.
- A health probe per `(feed, vendor)` pair. The engine credits a fallback vendor only from an
  observation of that vendor; without a probe on the fallback, the fallback does not exist as
  far as readiness is concerned.
- Clock discipline across the hosts producing and consuming feed timestamps — a lag computed
  against a skewed clock is meaningless.

Read before implementation:

- `references/standards.md` for freshness-tier guidance, the regulatory touchpoints, and an
  explicit statement of which numbers are operator-chosen rather than mandated.
- `references/workflows.md` for the registration, evaluation, and triage procedures.
- `assets/checklist.md` for the production sign-off artifact.

## Workflow

1. **Inventory the dependencies**: Register a `DataDependencyNode` per feed with a stable
   `feed_id`, criticality, ordered `vendors`, and an explicit `max_acceptable_lag_seconds`.
   The SLA has no default: choose it per feed rather than inheriting one.
2. **Record derivation edges**: Set `upstream_feed_ids` on every derived feed. The engine
   rejects unknown references, self-loops, and cycles at construction, and never lets a derived
   feed score better than its worst upstream.
3. **Choose the failure response**: Leave `failure_response` unset to get BLOCK for CRITICAL
   feeds and DEGRADE for everything else, or set it explicitly. Setting DEGRADE on a feed means
   you accept trading on cached or imputed values for it — decide that deliberately.
4. **Observe every vendor**: Emit one `FeedObservation` per `(feed_id, vendor_id)` you actually
   probed, carrying the publication timestamp, health flag, schema version, and any schema
   error. Do not synthesise an observation you did not measure.
5. **Evaluate before trading**: Call `evaluate_strategy_readiness(now, observations)`. Each
   feed is served by the highest-preference vendor whose observation is healthy, on-contract,
   not future-dated beyond tolerance, and within the freshness bound. Trade only when
   `is_strategy_ready_to_trade` is true, and treat any raised exception as not-ready.
6. **Act on the report, not the score alone**: `blocked_dependencies` is a hard stop.
   `fallback_dependencies` and `degraded_dependencies` are running-on-a-spare signals that
   belong in an alert, and `warnings` surfaces configuration drift — an unmapped feed, an
   observation from a vendor outside the hierarchy, a duplicated observation.
7. **Review single points**: Run `assess_vendor_outage(vendor_id)` and `single_source_feeds()`
   during design review, and `DataDependencyPortfolio.strategies_blocked_by(vendor_id)` during
   an incident to see which strategies a vendor loss stops.
8. **Reconcile**: Re-check the map against deployed subscriptions and vendor contracts on a
   defined cadence. A dependency map that drifts from production is worse than none, because
   it is trusted.

## Decision Points

- **Block versus degrade**: Ask what the strategy does with a missing value, not how important
  the feed feels. If it imputes and keeps trading, DEGRADE is honest; if it silently reuses the
  last value in a price-sensitive calculation, that is BLOCK.
- **Freshness bound per feed**: Derive it from how fast the underlying quantity moves relative
  to the strategy's holding period. A quarterly fundamentals feed and an L2 book do not share
  a bound, and the tier table in `references/standards.md` is a starting point, not a rule.
- **Is the fallback real?**: Two vendors count as independent only if they have separate
  upstream sources, networks, credentials, and parsers. A "secondary" that redistributes the
  primary's data is one vendor wearing two names, and the engine cannot detect that for you.
- **Unobserved fallback**: A fallback with no health probe scores as unavailable. That is the
  intended conservative reading — fix the probe rather than assuming the vendor is fine.
- **Score versus blocks**: `readiness_score_pct` is a scalar summary for dashboards and trend
  lines. The gate is `is_strategy_ready_to_trade`, which requires zero blocked dependencies
  *and* a score at or above the policy minimum. Never re-derive the decision from the score.
- **Static projection versus live state**: `assess_vendor_outage` assumes every remaining
  vendor is healthy, so it is an upper bound on resilience. During a correlated outage, use a
  live evaluation.

## Common Pitfalls

- **Crediting an unobserved fallback**: Concluding that a secondary vendor is serving because
  the primary failed. Nothing observed the secondary; it may be down for the same reason.
- **Trusting a fresh timestamp on a derived feed**: A feature pipeline computing off a dead
  input keeps publishing on schedule with a current timestamp. Only the upstream edge reveals it.
- **Unbounded staleness arithmetic**: `now - last_updated` goes negative when a vendor's clock
  runs fast, and a negative lag passes any "lag <= bound" test forever. Future-dated timestamps
  beyond a small tolerance are a clock fault, not fresh data.
- **Inheriting a default SLA**: A CRITICAL order-book feed silently picking up a lenient
  default bound is a stale-data incident waiting for a quiet market.
- **Treating schema validity as static configuration**: Schema conformance is a property of
  what the vendor just sent, not of the node definition. Compare the observed schema version
  against the contract on every evaluation.
- **Single-sourcing quietly**: A feed with one vendor is a single point of failure whether or
  not anyone wrote that down. `single_source_feeds()` makes the list explicit.
- **Ignoring warnings**: An observation for an unmapped feed usually means the map is stale,
  which means the readiness verdict is answering the wrong question.
- **Reusing a readiness verdict**: A report is a statement about one timestamp. Re-evaluate
  before acting rather than caching the verdict across a session.

## Expected Outputs and Success Criteria

- A versioned per-strategy node inventory: feed ids, criticality, ordered vendors, freshness
  bounds, schema contracts, upstream edges, and failure responses.
- A `StrategyDataDependencyReport` per evaluation carrying the readiness score, the trading
  verdict, a per-feed `DependencyAssessment` with state, serving vendor, fault code and lag,
  and the blocked/fallback/degraded/unobserved lists plus configuration warnings.
- A `VendorExposure` per vendor identifying dependent feeds, sole-sourced feeds, feeds whose
  loss would block trading, and the projected readiness after that vendor is lost.
- A portfolio-level list of strategies a given vendor outage would stop.
- Evidence that every configured fallback vendor has a live health probe, and a documented
  rationale for the policy weights, credits, and readiness minimum in use.

## Verification

Run:

```bash
python -m unittest discover -s skills/strategy-specific-data-dependency-mapping/scripts
```

The suite covers the healthy path and score arithmetic, fallback promotion only on observed
evidence, a feed already serving from a non-primary vendor, freshness at and just past the SLA
bound, future-dated timestamps inside and outside the clock tolerance, schema errors and
contract mismatches, upstream propagation through derived feeds, block-versus-degrade
responses, conservative duplicate-observation resolution, unmapped and out-of-hierarchy
observations, vendor blast radius, portfolio triage, configuration and policy validation,
determinism, and report immutability.

Before production adoption, replay a recorded outage through the engine and confirm the
verdict matches what the desk did, verify that every fallback vendor in the map has a health
probe feeding real observations, and confirm the calling code fails closed when
`evaluate_strategy_readiness` raises.

## Related Skills

- `vendor-outage-fallback-data-source-hierarchy`
- `reference-data-golden-source-designation`
- `risk-control-dependency-mapping`
- `data-lineage-tracking-for-audit-and-debugging`
- `clock-skew-correction-for-tick-timestamps`
- `data-pipeline-schema-contract-testing`
