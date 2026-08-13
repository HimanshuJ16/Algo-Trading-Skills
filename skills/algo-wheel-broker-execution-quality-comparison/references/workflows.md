# Workflows for Algo Wheel Broker Comparison

## Systematic Broker Evaluation Loop

1. **Define the evaluation window**: choose a bounded period and freeze the
   execution-ledger snapshot, benchmark definition, fee schedule, FX rates, and
   inclusion/exclusion rules.
2. **Capture decision context**: persist decision-price timestamp/source,
   side, instrument, order size, venue, urgency, parent order, broker, fills,
   fees, and any cancellation or rejection outcome.
3. **Validate and normalize**: reject malformed prices or quantities, normalize
   broker IDs and side values, convert fees to the notional currency, and link
   partial fills to their parent order.
4. **Segment observations**: separate materially different order classes or
   record the segmentation fields used for a controlled comparison.
5. **Calculate TCA**: compute signed IS per execution and aggregate by
   decision-notional-weighted broker average without prematurely rounding raw
   scores.
6. **Rank deterministically**: sort by average IS, then broker ID for exact
   ties. Produce a report containing score, execution count, notional coverage,
   and exclusions even if the reference function returns only allocations.
7. **Generate allocations**: apply the approved canary policy, verify shares
   sum to 1.0, and reject impossible minimum-allocation settings.
8. **Review and publish**: require trading or best-execution-owner approval,
   version the allocation snapshot, and publish it atomically to the router.
9. **Monitor and re-evaluate**: compare realized post-change IS with the prior
   window, monitor sample and notional coverage, and pause promotion when data
   quality or execution conditions deteriorate.

## Deployment, Rollback, and Recovery

- **Before deployment**: compare proposed allocations with the current snapshot,
  set maximum per-broker and strategy exposure limits, and test publication and
  fallback behavior in a non-production environment.
- **During deployment**: publish a versioned snapshot atomically; if the router
  cannot load it completely, keep the last known-good snapshot rather than
  using partial weights.
- **On anomaly**: pause automatic promotion when reject rates, adverse
  selection, slippage, fee totals, or broker connectivity materially diverge
  from the evaluation window.
- **Rollback**: restore the last approved allocation snapshot, record the
  incident and affected orders, and keep the wheel in a safe canary policy until
  the TCA data and broker route are reviewed.
- **After rollback**: reconcile allocations actually used, compare them with the
  published snapshot, and document the corrective action before re-enabling
  automatic rotation.

## Monitoring and Evidence

Monitor by broker, side, instrument or segment, venue, and allocation snapshot:

- IS distribution, mean, median, tail percentiles, and fee contribution;
- execution count and decision-notional coverage;
- realized versus target allocations and allocation drift;
- stale/missing benchmark rate, invalid-record rate, and data-latency metrics;
- broker rejects, partial fills, cancels, and connectivity incidents.

Retain the raw ledger reference, calculation configuration, ranking, approval,
allocation snapshot, deployment result, and rollback history.
