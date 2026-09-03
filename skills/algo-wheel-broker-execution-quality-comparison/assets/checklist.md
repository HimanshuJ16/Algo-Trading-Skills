# Pre-Flight Checklist

## Prerequisites

- [ ] Define the decision-price timestamp, source, review window, and order universe.
- [ ] Confirm price, quantity, fee, and FX units are compatible.
- [ ] Confirm order assignment to brokers is randomised against the published weights, not chosen by a trader.
- [ ] Capture broker, side, instrument, venue, urgency, parent order, fills, and partial-fill links.
- [ ] Define segmentation, exclusion, minimum sample, and invalid-record policies.
- [ ] Agree the data-sufficiency thresholds (`min_observations`, `min_notional`) with the best-execution owner.
- [ ] Obtain approval for the canary allocation policy and rollback owner.

## Validation

- [ ] Reject missing broker IDs, unsupported sides, non-finite values, non-positive prices, and non-positive quantities.
- [ ] Reject a decision notional that is non-finite or non-positive, including overflow.
- [ ] Verify buy and sell IS use the decision-price denominator.
- [ ] Include normalized commissions, taxes, exchange fees, and documented rebates.
- [ ] Use notional-weighted broker averages and retain raw scores before rounding.
- [ ] Record fill rate, cancelled residual, and reject rate per broker — the score covers executed quantity only.
- [ ] Confirm no broker is promoted below the configured sufficiency thresholds, and that an all-ineligible window returns equal weights rather than a promotion.
- [ ] Confirm deterministic tie handling and allocations summing to 1.0 within `ALLOCATION_TOLERANCE`.
- [ ] Confirm the canary floor leaves the leading broker at least the floor itself.
- [ ] Run `python -m unittest discover -s skills/algo-wheel-broker-execution-quality-comparison/scripts`.

## Deployment and Rollback

- [ ] Freeze and version the TCA input snapshot, calculation configuration, ranking evidence, and proposed allocations.
- [ ] Compare new allocations with the current live snapshot and apply exposure limits.
- [ ] Test atomic publication and last-known-good fallback in non-production.
- [ ] Define anomaly thresholds and the route or strategy pause action.
- [ ] Verify rollback restores the prior allocation snapshot and records affected orders.

## Monitoring and Post-Deployment Verification

- [ ] Monitor IS, fee contribution, tails, fill rate, sample count, notional coverage, and allocation drift.
- [ ] Alert on stale benchmarks, invalid records, broker rejects, connectivity incidents, and data-latency spikes.
- [ ] Compare realized flow with target weights after deployment.
- [ ] Retain ranking, approval, deployment, rollback, and post-deployment review evidence.

## Regulatory

- [ ] Confirm the jurisdiction's live obligations before citing this output as best-execution evidence.
- [ ] FINRA members: schedule the Rule 5310 regular and rigorous review at least quarterly and cover likelihood of execution, speed, and size of execution in addition to this score.
- [ ] Do not generate MiFID II RTS 27 or RTS 28 reports — both obligations were deleted by Directive (EU) 2024/790.

## Sign-off

- Head of Trading: ___________________________
- Best-Execution Owner: ______________________
- Date: ___________________________
