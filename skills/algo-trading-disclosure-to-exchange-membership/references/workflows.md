# Workflows for Algo Trading Disclosure

## Pre-Trade Compliance Pipeline

1. **Rule Resolution**: Resolve venue, broker, jurisdiction, account type, and
   order path. Load the current venue tagging specification and registry snapshot.
2. **Classification**: Determine `is_algorithmic` upstream from strategy behavior
   and the applicable legal policy. A missing tag must never silently downgrade
   an order to manual.
3. **Order Interception**: Intercept the order before FIX or broker serialization.
   Validate identity fields, manual trader attribution, and algorithm metadata.
4. **Registry Validation**: Require an exact `algo_id` match and `APPROVED` status.
   Apply venue and registered-version constraints when present. The registry
   itself is validated once, when the engine is constructed: a duplicate key, a
   blank status or version, or a venue scope declared as a bare string aborts
   startup rather than degrading a later order decision.
5. **Lineage Validation**: When an order is a child of an algorithmic parent,
   propagate the parent identifier and reject mismatches or omissions at the
   child-order boundary.
6. **Gate Execution**: Allow only compliant orders to proceed. Return a
   structured report and emit an audit event for both approvals and rejects.
7. **Wire Verification**: Confirm that the venue adapter serialized the logical
   identifier into the venue-specific field. Store the adapter mapping version
   with the deployment evidence.

## Registry Update and Deployment

1. Create a new registry record for a new or materially changed algorithm;
   never overwrite the prior record in place, and never mutate the running
   engine's snapshot — rebuild and redeploy it so the change carries an
   approval and audit record.
2. Obtain the required compliance, exchange, or broker approval and record the
   effective venue scope and version.
3. Test the strategy and adapter in a non-production environment, including
   missing, stale, and child-order tags.
4. Promote the strategy and registry atomically, or keep the route blocked until
   both are consistent.
5. Monitor rejection counts by `reason_code`, venue, algorithm ID, and strategy
   version during the first production window.

## Failure and Recovery

- If the registry cannot be loaded or validated, fail closed and retain a
  durable incident record; restore the last known-good signed snapshot.
- If approval is revoked, move the record to `SUSPENDED` or `DEPRECATED`, stop
  new orders, and reconcile/cancel resting orders according to the venue plan.
- If a child-order propagation defect is detected, stop the router, reconcile
  all submitted children, correct the adapter, and rerun the wire-level tests.
- If reject rates spike after deployment, use the route or strategy kill switch,
  compare the deployment manifest with the registry snapshot, and roll back the
  last change before reopening flow.

## Monitoring and Evidence

Retain, at minimum:

- order ID, timestamp, venue, algorithmic classification, algorithm ID, and
  decision outcome;
- stable rejection reason code and registry status;
- registry snapshot/version and venue-adapter mapping version;
- deployment, approval, rollback, and incident references;
- evidence that the outbound payload carried the expected venue-specific field.
