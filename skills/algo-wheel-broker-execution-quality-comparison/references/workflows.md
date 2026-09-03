# Workflows for Algo Wheel Broker Comparison

## Systematic Broker Evaluation Loop

1. **Define the evaluation window**: choose a bounded period and freeze the
   execution-ledger snapshot, benchmark definition, fee schedule, FX rates, and
   inclusion/exclusion rules.
2. **Randomise assignment**: route each comparable order to a broker by drawing
   against the published target weights rather than by trader choice, and
   persist the draw. The wheel's whole claim to an unbiased comparison rests on
   this step; orders picked for a broker by a human measure the human.
3. **Capture decision context**: persist decision-price timestamp/source,
   side, instrument, order size, venue, urgency, parent order, broker, fills,
   fees, and any cancellation or rejection outcome.
4. **Validate and normalize**: reject malformed prices or quantities, normalize
   broker IDs and side values, convert fees to the notional currency, and link
   partial fills to their parent order.
5. **Segment observations**: separate materially different order classes or
   record the segmentation fields used for a controlled comparison.
6. **Calculate TCA**: compute signed IS per execution and aggregate by
   decision-notional-weighted broker average without prematurely rounding raw
   scores.
7. **Calculate what IS omits**: fill rate, cancelled residual, reject rate, and
   speed per broker over the same window. The module's score covers executed
   quantity only, so a broker can lower its IS purely by completing less of each
   order. Carry these alongside the score, never instead of it.
8. **Rank deterministically**: `rank_brokers` sorts by average IS, then broker
   ID for exact ties, and returns the score, execution count, decision notional,
   and promotion eligibility for each broker. Publish that record with the
   exclusions.
9. **Apply the data-sufficiency policy**: `min_observations` and `min_notional`
   decide who may lead. A broker below either threshold keeps its canary share
   but cannot be promoted. If nothing qualifies the wheel returns equal weights
   and logs a warning — treat that as "keep gathering data", not as a result.
10. **Generate allocations**: apply the approved canary policy, verify shares
    sum to 1.0 within `ALLOCATION_TOLERANCE`, and reject impossible
    minimum-allocation settings including any that would route the leader less
    flow than the canary floor.
11. **Review and publish**: require trading or best-execution-owner approval,
    version the allocation snapshot together with the ranking evidence, and
    publish it atomically to the router.
12. **Monitor and re-evaluate**: compare realized post-change IS with the prior
    window, monitor sample, notional coverage and fill rate, and pause promotion
    when data quality or execution conditions deteriorate. FINRA members owe a
    regular and rigorous review at least quarterly under Rule 5310.

## Deployment, Rollback, and Recovery

- **Before deployment**: compare proposed allocations with the current snapshot,
  set maximum per-broker and strategy exposure limits, and test publication and
  fallback behavior in a non-production environment.
- **During deployment**: publish a versioned snapshot atomically; if the router
  cannot load it completely, keep the last known-good snapshot rather than
  using partial weights.
- **On anomaly**: pause automatic promotion when reject rates, adverse
  selection, slippage, fee totals, fill rates, or broker connectivity materially
  diverge from the evaluation window.
- **Rollback**: restore the last approved allocation snapshot, record the
  incident and affected orders, and keep the wheel in a safe canary policy until
  the TCA data and broker route are reviewed.
- **After rollback**: reconcile allocations actually used, compare them with the
  published snapshot, and document the corrective action before re-enabling
  automatic rotation.

## Monitoring and Evidence

Monitor by broker, side, instrument or segment, venue, and allocation snapshot:

- IS distribution, mean, median, tail percentiles, and fee contribution;
- fill rate, cancelled residual, and reject rate — the blind spot in the score;
- execution count and decision-notional coverage, against the sufficiency
  thresholds actually configured;
- realized versus target allocations and allocation drift, including whether
  the randomised assignment is tracking the published weights;
- stale/missing benchmark rate, invalid-record rate, and data-latency metrics;
- broker rejects, partial fills, cancels, and connectivity incidents.

Retain the raw ledger reference, calculation configuration, ranking evidence,
approval, allocation snapshot, deployment result, and rollback history.
