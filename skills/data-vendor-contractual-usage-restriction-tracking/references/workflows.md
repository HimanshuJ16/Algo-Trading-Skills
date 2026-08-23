# Workflows for Data Vendor Contractual Usage Restriction Tracking

## 1. Contract onboarding

1. Read the executed agreement and its schedules with the compliance owner. Encode
   only what the document actually grants.
2. Register a `VendorContractSpec`: licensed `allowed_use_cases`, the non-display
   and redistribution booleans, the seat cap, and `contract_expiration_date` as an
   ISO-8601 date taken from the contract.
3. If expiry is genuinely tracked in another system, pass `None` rather than a
   placeholder date, and make sure that other system can deny access. The engine
   logs a warning once per vendor when expiry is untracked — do not filter it out.
4. Registration rejects self-contradictory scopes (a contract listing
   `EXTERNAL_REDISTRIBUTION` while `is_redistribution_allowed` is False), an
   over-subscribed starting seat count, and an empty use case list. Fix the data;
   do not work around the error.
5. Re-registering an existing `vendor_id` requires `replace=True` and discards the
   live seat count — use it only for a genuine contract renewal, at a moment when
   outstanding reservations are known.

## 2. Request interception

1. Route every internal consumer of vendor data through
   `evaluate_access_request` before the feed is opened, not after.
2. Pass `as_of_date` explicitly in batch or replay contexts so the decision is
   reproducible; the default of "today" makes an audit re-run non-deterministic.
3. Classify the use case honestly. `NON_DISPLAY_TRADING` covers any automated
   machine consumption without a person reading a display, including risk engines
   and auto-hedgers, not just the alpha strategy.
4. Set `is_external_redistribution` for anything leaving the licensed entity —
   client portals, published charts, redistributed indices, and derived series from
   which the underlying quotes can be recovered.

## 3. Violation handling

1. Deny the request. Do not retry, do not widen `allowed_use_cases`, and do not
   raise the seat cap to make a denial disappear — each of those converts a caught
   breach into an uncaught one.
2. Escalate `REDISTRIBUTION_LICENSING_VIOLATION` and `CONTRACT_EXPIRED` to the
   compliance owner; both mean data is being used outside the licence.
3. For a genuinely new business use, submit the revised usage to the vendor and
   obtain written approval and pricing before re-registering the contract with the
   broadened scope.

## 4. Entitlement lifecycle

1. Approval reserves seats. Pair every approval with a
   `release_entitlement(vendor_id, seats)` on disconnect, shutdown, or session
   teardown.
2. Reconcile reserved seats against actual connections periodically. A steadily
   rising `current_active_entitlements` with flat real usage means teardown is not
   wired up.
3. `release_entitlement` refuses to release more than is reserved rather than
   clamping to zero — that error means the caller's accounting has diverged and
   should be investigated, not suppressed.

## 5. Audit trail

1. Persist every returned `VendorUsageAuditReport` durably as it is produced,
   including denials. `get_audit_trail()` is a bounded in-memory buffer for
   monitoring and debugging only.
2. Retain records for at least the vendor's audit look-back period — three years
   under the Nasdaq Global Data Agreement (s.7(e)).
3. Keep `applied_policy` verbatim. It is the contemporaneous reason an auditor
   asks for, and reconstructing it later is not equivalent.
