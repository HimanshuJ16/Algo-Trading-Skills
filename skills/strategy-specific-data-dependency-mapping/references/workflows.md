# Workflows — Strategy-Specific Data Dependency Mapping

All procedures below use the reference engine in
`scripts/strategy_specific_data_dependency_mapping.py`. It is pure and dependency-free: it
performs no I/O, polls nothing, and mutates nothing. Vendor probing, persistence, alert
routing, and acting on the verdict are adapter concerns.

## 1. Build the dependency inventory

1. List every input the strategy reads at decision time, not only the price feeds. Include
   reference data, corporate actions, borrow availability, FX rates used for normalisation,
   and every derived feature computed by an upstream job.
2. For each input, create a `DataDependencyNode`:
   - `feed_id` — stable, never reused, matching whatever the health probe reports.
   - `criticality` — `CRITICAL`, `HIGH`, `MEDIUM`, or `LOW`.
   - `vendors` — ordered preference tuple, primary first. Duplicates are rejected.
   - `max_acceptable_lag_seconds` — mandatory, chosen for this feed. There is no default.
   - `schema_contract_version` — set it only if the probe reports an observed schema version
     to compare against; leaving it empty disables the contract check.
   - `upstream_feed_ids` — feeds this one is computed from.
   - `failure_response` — leave `None` to inherit `BLOCK` for CRITICAL and `DEGRADE`
     otherwise, or set it explicitly.
3. Construct `StrategyDataDependencyEngine(strategy_id, nodes, policy=None)`. Construction
   validates the whole graph and raises `DependencyValidationError` on a duplicate `feed_id`,
   an unknown upstream reference, a self-loop, or a cycle. Fix the map; do not delete an edge
   to make the cycle go away.

```python
from strategy_specific_data_dependency_mapping import (
    DataDependencyNode, DependencyCriticality, FailureResponse,
    FeedObservation, StrategyDataDependencyEngine,
)

nodes = [
    DataDependencyNode(
        feed_id="L2_BOOK", feed_name="L2 order book",
        criticality=DependencyCriticality.CRITICAL,
        vendors=("VendorA", "VendorB"),
        max_acceptable_lag_seconds=5.0,
        schema_contract_version="book-v3",
    ),
    DataDependencyNode(
        feed_id="IMBALANCE", feed_name="Queue imbalance feature",
        criticality=DependencyCriticality.HIGH,
        vendors=("Internal",),
        max_acceptable_lag_seconds=10.0,
        upstream_feed_ids={"L2_BOOK"},
        failure_response=FailureResponse.BLOCK,
    ),
]
engine = StrategyDataDependencyEngine("STAT_ARB_01", nodes)
```

## 2. Tune the readiness policy

`ReadinessPolicy` carries the criticality weights, the fallback and degraded credit, the
readiness minimum, and the future-timestamp tolerance. All are operator-chosen — see
`references/standards.md`. Constraints enforced at construction: every criticality must have a
positive weight, `0 <= degraded_credit <= fallback_credit <= 1`, the minimum must sit within
`[0, 100]`, and the tolerance must be non-negative.

Set `future_timestamp_tolerance_seconds` from your actual clock-discipline budget. Too tight
and a normally-skewed vendor is rejected as a clock fault; too loose and a badly skewed vendor
stays permanently "fresh".

## 3. Collect observations

Emit one `FeedObservation` per `(feed_id, vendor_id)` pair you actually probed:

- `last_updated_epoch` — the publication timestamp reported by that vendor's stream, not the
  time your process received it and not `time.time()` at probe time.
- `is_healthy` — the transport/session view: connected, no error state.
- `schema_version` — the observed contract version, when the node declares one.
- `schema_error` — a non-empty description when the payload failed contract validation.

Probe every vendor in the hierarchy, not just the one currently serving. A fallback with no
observation is treated as unavailable, by design: nothing measured it, so nothing can vouch
for it. This is the single most common way a dependency map produces a falsely reassuring
readiness score.

## 4. Evaluate before trading

`evaluate_strategy_readiness(current_time_epoch, observations)` resolves each feed as follows:

1. Walk `vendors` in preference order. A vendor is usable when its observation is not
   future-dated beyond tolerance, is healthy, carries no `schema_error`, matches the declared
   `schema_contract_version`, and lags by no more than `max_acceptable_lag_seconds`. The bound
   is inclusive: a lag exactly equal to the bound is fresh.
2. The first usable vendor serves the feed — `PRIMARY_ACTIVE` at rank 0, `FALLBACK_ACTIVE`
   otherwise. The reported `fault` on a fallback is why the *primary* was rejected.
3. If no vendor is usable, the feed is `UNAVAILABLE`, then clamped to `DEGRADED` when its
   effective response is `DEGRADE`. `UNAVAILABLE` in the resolved report therefore always
   means "the strategy must not trade on this".
4. Upstream states are folded in, in topological order. A feed is never healthier than its
   worst upstream, and the fault becomes `UPSTREAM_IMPAIRED`.
5. Credit per feed is its criticality weight times 1.0 / `fallback_credit` / `degraded_credit`
   / 0.0 by state. `readiness_score_pct` is earned weight over total weight.
6. `is_strategy_ready_to_trade` requires **zero** blocked dependencies **and** a score at or
   above `minimum_readiness_pct`.

Treat any raised exception as not-ready. `ObservationValidationError` means the caller passed
something malformed; failing open there would defeat the gate.

## 5. Read the report

| Field | Use |
|---|---|
| `is_strategy_ready_to_trade` | The gate. Do not re-derive it from the score. |
| `blocked_dependencies` | Hard stop. Page someone. |
| `fallback_dependencies` | Running on a spare — alert, and check the primary's outage clock. |
| `degraded_dependencies` | Trading on cached/imputed values for these feeds by prior decision. |
| `unobserved_dependencies` | No probe reported for any vendor of this feed. Usually a monitoring gap, not a vendor outage. |
| `active_feed_sources` | Feed to serving vendor, for feeds you may rely on — `PRIMARY_ACTIVE` and `FALLBACK_ACTIVE` only. A feed degraded by an impaired upstream is deliberately absent even though a vendor is attached to it; read `assessments[].active_vendor` for that. |
| `warnings` | Configuration drift: unmapped feed, vendor outside the hierarchy, duplicated observation. |
| `assessments` | Per-feed state, serving vendor, fault code, observed lag, and credit — the audit record. |
| `audit_notes` | One-line summary suitable for a log or ticket. |

Duplicate observations for the same `(feed, vendor)` are resolved to the least favourable one
and recorded as a warning. That keeps a caller bug from silently raising readiness, but the
warning still means the caller needs fixing.

## 6. Single-point-of-failure review

- `engine.single_source_feeds()` lists feeds with no configured fallback.
- `engine.assess_vendor_outage(vendor_id)` returns a `VendorExposure`: dependent feeds,
  sole-sourced feeds, feeds whose loss blocks trading, the projected readiness after the
  outage, and the highest criticality touched.

The projection assumes every remaining vendor is healthy, so it is an **upper bound on
resilience**. It will tell you a dual-sourced feed survives losing its primary; it will not
tell you the secondary is down for the same underlying reason. During a live incident, run a
real evaluation.

## 7. Portfolio outage triage

```python
from strategy_specific_data_dependency_mapping import DataDependencyPortfolio

portfolio = DataDependencyPortfolio([stat_arb_engine, trend_engine, vol_engine])
portfolio.strategies_blocked_by("VendorA")   # which strategies stop
portfolio.assess_vendor_outage("VendorA")    # per-strategy detail
portfolio.vendor_ids()                       # every vendor referenced anywhere
```

Run this before the incident, not during it: the answer to "what does losing VendorA cost us"
should be a lookup, not an investigation.

## 8. Reconciliation cadence

1. Re-derive the map from deployed subscriptions, pipeline configuration, and vendor contracts
   on a defined schedule and after any data-platform change.
2. Confirm every vendor in every hierarchy has a live health probe. A fallback nobody probes
   contributes nothing to readiness and will not be there in an incident.
3. Re-check that each `max_acceptable_lag_seconds` still matches the feed's real cadence —
   vendors change publication intervals without announcing them.
4. Replay a recorded outage through the engine and compare the verdict with what the desk
   actually did. A map that would have blocked a session that traded fine, or cleared one that
   should have stopped, is miscalibrated.
