# Pre-Flight Checklist

## Prerequisites

- [ ] Confirm the jurisdiction, venue, broker, account type, and current tagging specification.
- [ ] Confirm the upstream algorithmic-order classification policy and owner.
- [ ] Load a versioned registry with `APPROVED` status, venue scope, and registered version where required.
- [ ] Confirm the compliance gate runs before FIX or broker serialization.

## Validation

- [ ] Reject blank or malformed order identity fields.
- [ ] Require `algo_id` for algorithmic orders.
- [ ] Require `trader_id` and reject contradictory `algo_id` for manual orders.
- [ ] Reject unknown, pending, suspended, deprecated, or otherwise non-approved IDs.
- [ ] Validate venue and algorithm-version scope.
- [ ] Confirm child orders inherit the parent algorithm identifier.
- [ ] Verify the raw outbound payload contains the venue-specific tag.
- [ ] Run `python scripts/test_algo_trading_disclosure_to_exchange_membership.py`.

## Deployment and Rollback

- [ ] Record approval, registry snapshot, strategy version, and adapter mapping version.
- [ ] Test the new strategy and venue adapter in non-production before promotion.
- [ ] Define the route or strategy kill-switch action for a disclosure failure.
- [ ] Verify rollback restores the previous approved registry and strategy together.

## Monitoring and Post-Deployment Verification

- [ ] Monitor approvals and rejects by venue, algorithm ID, and `reason_code`.
- [ ] Alert on unknown IDs, registry-load failures, and sudden reject-rate increases.
- [ ] Recheck child-order tagging and raw wire messages during the first production window.
- [ ] Retain audit evidence for approval, deployment, rollback, and incidents.

## Sign-off

- Chief Compliance Officer (CCO): ___________________________
- Engineering Owner: ______________________________________
- Date: ___________________________
