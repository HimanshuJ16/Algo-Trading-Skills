# Pre-Flight Checklist

## Prerequisites

- [ ] Define the decision-price timestamp, source, review window, and order universe.
- [ ] Confirm price, quantity, fee, and FX units are compatible.
- [ ] Capture broker, side, instrument, venue, urgency, parent order, fills, and partial-fill links.
- [ ] Define segmentation, exclusion, minimum sample, and invalid-record policies.
- [ ] Obtain approval for the canary allocation policy and rollback owner.

## Validation

- [ ] Reject missing broker IDs, unsupported sides, non-finite values, non-positive prices, and non-positive quantities.
- [ ] Verify buy and sell IS use the decision-price denominator.
- [ ] Include normalized commissions, taxes, exchange fees, and documented rebates.
- [ ] Use notional-weighted broker averages and retain raw scores before rounding.
- [ ] Confirm deterministic tie handling and valid allocations summing to 1.0.
- [ ] Confirm the canary floor leaves residual flow for the leading broker.
- [ ] Run `python scripts/test_algo_wheel_broker_execution_quality_comparison.py`.

## Deployment and Rollback

- [ ] Freeze and version the TCA input snapshot, calculation configuration, and proposed allocations.
- [ ] Compare new allocations with the current live snapshot and apply exposure limits.
- [ ] Test atomic publication and last-known-good fallback in non-production.
- [ ] Define anomaly thresholds and the route or strategy pause action.
- [ ] Verify rollback restores the prior allocation snapshot and records affected orders.

## Monitoring and Post-Deployment Verification

- [ ] Monitor IS, fee contribution, tails, sample count, notional coverage, and allocation drift.
- [ ] Alert on stale benchmarks, invalid records, broker rejects, connectivity incidents, and data-latency spikes.
- [ ] Compare realized flow with target weights after deployment.
- [ ] Retain ranking, approval, deployment, rollback, and post-deployment review evidence.

## Sign-off

- Head of Trading: ___________________________
- Best-Execution Owner: ______________________
- Date: ___________________________
