# Pre-Flight Checklist

## Prerequisites

- [ ] Confirm the jurisdiction, venue, broker, account type, and current tagging specification.
- [ ] Confirm the upstream algorithmic-order classification policy and owner.
- [ ] Load a versioned registry with `APPROVED` status, venue scope, and registered version where required.
- [ ] Confirm the registry loads without error: no duplicate keys after trimming, no blank status or version, and every venue scope declared as a collection rather than a bare string.
- [ ] Confirm the compliance gate runs before FIX or broker serialization.

## Validation

- [ ] Reject blank or malformed order identity fields.
- [ ] Require `algo_id` for algorithmic orders.
- [ ] Require `trader_id` for manual orders and reject any contradictory `algo_id`, `parent_algo_id`, or `algo_version`.
- [ ] Reject unknown, pending, suspended, deprecated, or otherwise non-approved IDs.
- [ ] Validate venue and algorithm-version scope.
- [ ] Confirm child orders inherit the parent algorithm identifier.
- [ ] Verify the raw outbound payload contains the venue-specific tag.
- [ ] Run `python -m unittest discover -s skills/algo-trading-disclosure-to-exchange-membership/scripts`.

## Deployment and Rollback

- [ ] Record approval, registry snapshot, strategy version, and adapter mapping version.
- [ ] Test the new strategy and venue adapter in non-production before promotion.
- [ ] Define the route or strategy kill-switch action for a disclosure failure.
- [ ] Verify rollback restores the previous approved registry and strategy together.
- [ ] Confirm no runtime path mutates the engine's registry in place instead of redeploying it.

## Monitoring and Post-Deployment Verification

- [ ] Monitor approvals and rejects by venue, algorithm ID, and `reason_code`.
- [ ] Alert on unknown IDs, registry-load failures, and sudden reject-rate increases.
- [ ] Recheck child-order tagging and raw wire messages during the first production window.
- [ ] Retain audit evidence for approval, deployment, rollback, and incidents.

## Sign-off

- Chief Compliance Officer (CCO): ___________________________
- Engineering Owner: ______________________________________
- Date: ___________________________
