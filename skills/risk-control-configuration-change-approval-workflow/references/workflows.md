# Risk Configuration Approval Workflows

Use this procedure with the requirements in `standards.md`. The Python module is a reference aggregate; production orchestration must persist state and audit/outbox records transactionally.

## Contents

- State model
- Normal change
- Application timeout and retry
- Partial propagation
- Version conflict
- Rejection, cancellation, and expiry
- Emergency risk reduction
- Rollback
- Periodic reconciliation
- Production adapter contract

## State model

| Current state | Command | Required conditions | Next state |
|---|---|---|---|
| — | submit | Valid immutable payload, unique ID, current base version | `PENDING` |
| `PENDING` | approve | Non-maker authorized checker, matching digest, unexpired | `PENDING` or `APPROVED` at quorum |
| `PENDING` / `APPROVED` | reject | Non-maker authorized checker, matching digest, reason | `REJECTED` |
| `PENDING` / `APPROVED` | cancel | Original maker, matching digest, unexpired | `CANCELLED` |
| `PENDING` / `APPROVED` | expiry evaluation | `now >= expires_at` | `EXPIRED` |
| `APPROVED` | apply | Authorized deployer, quorum intact, matching digest, base version current | `APPLIED` |
| `APPROVED` | reconcile | Durable apply ledger proves same request and digest applied | `APPLIED` |

`REJECTED`, `CANCELLED`, `EXPIRED`, and `APPLIED` are terminal. A changed payload, refreshed expiry, new base version, or revised rollout plan requires a new request and review.

## Normal change

### 1. Establish scope and evidence

1. Identify environment, broker/venue, account, strategy, instrument class, runtime consumers, and current authoritative version.
2. Retrieve the current configuration from the authoritative store, not a UI cache or local file.
3. Produce a field-level diff with units and tighter/looser classification. Evaluate portfolio-wide effects, not only the changed leaf value.
4. Attach test results, simulation/replay evidence where relevant, monitoring thresholds, rollout plan, and last-known-good version.
5. Select quorum and independent-applier requirements from server-side policy based on environment and blast radius.

### 2. Validate and submit

1. Parse with a strict versioned schema; reject unknown fields and implicit numeric coercion.
2. Run pure domain validation across fields and scopes. Validate venue/broker precision, supported ranges, capital/FX freshness, and account permissions where those inputs affect safety.
3. Reject secrets, non-finite numbers, oversized input, no-op changes, and stale base versions.
4. Canonicalize the proposed payload server-side.
5. Compute a digest over request ID, environment/scope, base version, maker, reason, ticket, policy/schema version, and canonical payload.
6. Persist request, idempotency record, and `CHANGE_SUBMITTED` outbox event in one transaction.
7. Return the digest and exact review representation. Never accept a client-computed digest as authoritative.

### 3. Review and approve

1. Authenticate the checker and derive authorization from trusted IAM claims.
2. Verify the checker differs from the maker and is entitled to the environment and risk scope.
3. Present the current value, proposed value, units, risk direction, effective targets, evidence, rollback, base version, expiry, policy version, and digest.
4. Require an explicit approval or rejection; do not infer approval from comment, ticket status, or notification acknowledgement.
5. Within one transaction, enforce unique checker identity, append the digest-bound decision, advance state when quorum is met, and emit an outbox/audit event.
6. Treat an identical duplicate approval as a successful idempotent retry. Reject a different digest or reused request ID.

### 4. Pre-apply gate

Immediately before application:

1. Reauthorize the deployer and, when enabled, verify independence from maker and checkers.
2. Re-evaluate expiry and quorum.
3. Verify every approval references the current request digest.
4. Re-run schema and domain validation using the approved policy artifact.
5. Confirm the authoritative version still equals `base_version`.
6. Confirm deployment window, market/session restrictions, incident state, and runtime health permit the change.
7. Acquire the single-writer/transaction boundary required by the configuration store; do not use a distributed lock as a substitute for compare-and-set.

### 5. Apply and propagate

1. Atomically compare version, write the immutable configuration version, and record `(request_id, request_digest, new_version)` for idempotency.
2. Publish propagation through a transactional outbox or an equivalent mechanism that cannot lose the event after commit.
3. Let consumers deduplicate, validate, and acknowledge the version/digest. Consumers must not partially merge a payload with local values.
4. For rolling activation, continuously enforce aggregate safety while old and new versions coexist. Abort when the mixed-version bound is unsafe.
5. Keep the prior version available but immutable.

### 6. Verify and close

1. Read back the authoritative version and digest.
2. Collect acknowledgements from every intended consumer and query runtime-loaded version/digest independently of the deployment channel.
3. Run safe control probes or shadow evaluations. Never send intentionally unsafe live orders merely to test a limit.
4. Verify no unexpected reject-rate, exposure, latency, stale-config, or control-health alert.
5. Mark complete only when the change record, applied ledger, authoritative store, distribution state, and runtime state agree.
6. Retain evidence and hand monitoring ownership to a named operator for the defined observation window.

## Application timeout and retry

An apply timeout is an unknown outcome, not a failure.

1. Keep the request `APPROVED`; do not submit another request or mutate the payload.
2. Query the store’s idempotency ledger using `request_id` and expected request digest.
3. If the ledger proves application, reconcile the workflow to `APPLIED`, record the returned version, and verify propagation.
4. If the store proves no application and the base version is unchanged, retry the same atomic command with the same idempotency key and digest.
5. If the outcome remains ambiguous, freeze further changes to the scope, fail safe, alert operations, and investigate. Never issue a blind second write.

The reference `reconcile()` method demonstrates state repair after the store committed but the response or local state update was lost.

## Partial propagation

1. Stop further rollout and freeze changes for the affected scope.
2. Determine authoritative version and enumerate every target’s loaded digest; do not rely on aggregate success counts.
3. If mixed state can increase risk, stop affected order entry or invoke the approved kill switch.
4. Choose roll-forward only when the approved version is safe and remaining targets are healthy. Otherwise roll back all targets to one known version.
5. Deduplicate late events so a recovered target cannot reapply an abandoned version.
6. Reconcile target state, emit incident/audit evidence, and reopen only after one effective version is proven.

## Version conflict

When compare-and-set reports a version conflict:

1. Leave the original request terminally unapplied; do not auto-rebase it.
2. Read the new authoritative version and determine which intervening change won.
3. Recompute the complete diff and domain impact against the new base.
4. Submit a new request with a new ID, digest, expiry, and approval set.
5. Alert on repeated conflicts because they may indicate parallel change ownership or out-of-band writes.

## Rejection, cancellation, and expiry

- **Reject:** Require a checker reason and preserve the reviewed digest. A corrected proposal is a new request.
- **Cancel:** Permit only the maker (or an explicitly governed administrative path) before application. Expiry evaluation precedes cancellation.
- **Expire:** Evaluate on privileged commands and with a scheduled sweeper. Expire both pending and approved-but-unapplied requests.
- **Late messages:** Consumers must reject approval or apply messages for terminal requests even if queue delivery is delayed or duplicated.

## Emergency risk reduction

Use only a predefined monotonic operation whose semantics are mechanically verifiable, such as disabling a strategy, reducing an absolute limit, or activating a kill switch.

1. Strongly authenticate and authorize the emergency actor.
2. Prove the operation cannot loosen any affected control, including through scope precedence or sign inversion.
3. Atomically create a version and audit record; do not depend on the normal approval service being healthy.
4. Verify propagation and runtime enforcement immediately.
5. Notify risk/operations and create a retrospective review record.
6. Require normal maker-checker approval for restoration or any later increase in risk.

Do not label arbitrary configuration writes “break glass.” If monotonic safety cannot be proven, fail closed and use the kill switch.

## Rollback

1. Declare the rollback condition before the original apply (for example propagation deadline, reject-rate increase, or control-health failure).
2. Select the last-known-good immutable version and verify its compatibility with current schema, instrument metadata, capital, and broker/venue state.
3. Create a new versioned rollback request against the current version. Link the original change and incident.
4. Use normal approval unless a predefined emergency tightening rule applies.
5. Apply with compare-and-set, verify every consumer, and continue incident monitoring.
6. Never delete the failed version or rewrite history.

## Periodic reconciliation

At a cadence shorter than the maximum tolerated stale-configuration interval:

1. Compare nonterminal requests with the applied idempotency ledger.
2. Compare authoritative configuration version/digest with distribution state and every runtime consumer.
3. Detect expired requests, missing audit/outbox events, duplicate IDs, unknown versions, lagging targets, and out-of-band mutations.
4. Auto-repair only when durable evidence determines one unambiguous state. Emit `CHANGE_RECONCILED` or equivalent.
5. Quarantine ambiguity, freeze writes to the scope, and page the owning team.

## Production adapter contract

Replace `InMemoryVersionedConfigStore` with an adapter that guarantees:

- linearizable or transactionally serialized compare-and-set per configuration scope;
- durable uniqueness for request ID and approval identity;
- atomic persistence of new version and idempotency result;
- read-by-request-ID after timeout;
- immutable version history and access-controlled retention;
- transactional outbox or equivalent propagation durability;
- bounded, observable retries with no retry on validation/authorization/conflict errors;
- explicit distinction among not-applied, applied, and unknown outcomes;
- defensive snapshots rather than mutable shared objects;
- UTC server timestamps and correlation identifiers.

Keep broker- or venue-specific side effects behind separate adapters. The approval aggregate decides whether a change is authorized; it must not directly place/cancel orders, log secrets, or assume a broker acceptance means runtime risk enforcement is effective.
