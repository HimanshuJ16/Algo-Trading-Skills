# Risk Configuration Change Checklist

Change ID: ____________________  Ticket/incident: ____________________

Environment/scope: ____________________  Planned window (UTC): ____________________

Maker: ____________________  Checker(s): ____________________  Deployer: ____________________

Base version: __________  Policy/schema version: __________  Request digest: ____________________

## Scope and impact

- [ ] The authoritative current version and complete effective configuration were read from the production source of truth.
- [ ] Broker, venue, account, strategy, instrument, and session scopes are explicit; precedence is understood.
- [ ] Every changed field has units, precision, old value, new value, and tighter/looser classification.
- [ ] Cross-field, aggregate portfolio, capital/FX, and broker/venue constraints were evaluated with fresh inputs.
- [ ] Blast radius, affected runtime consumers, activation semantics, and maximum propagation time are documented.
- [ ] The request is not a no-op and contains no secrets, credentials, tokens, or unnecessary sensitive data.

## Validation evidence

- [ ] Strict schema validation passed; unknown fields, implicit coercion, NaN, and infinity are rejected.
- [ ] Domain and cross-scope invariants passed against the exact proposed payload.
- [ ] Unit, integration, replay/simulation, and concurrency evidence is attached as appropriate to risk.
- [ ] The canonical payload and server-generated digest bind request ID, scope, base version, maker, reason, ticket, policy version, and configuration.
- [ ] Monitoring thresholds, observation window, named owner, and abort criteria are defined.
- [ ] Last-known-good version and tested rollback steps are recorded.

## Authorization and approval

- [ ] Identities and roles come from trusted IAM claims and are entitled to this environment/scope.
- [ ] Maker and checker are distinct, non-shared identities; quorum contains unique authorized checkers.
- [ ] Independent applier separation is enabled when required by policy or blast radius.
- [ ] Checkers reviewed the exact diff, units, risk direction, evidence, rollback, expiry, base version, and digest.
- [ ] Each approval is bound to the current digest; the payload has not changed since approval.
- [ ] The request is unexpired and no incident, freeze, or market/session condition blocks deployment.

## Apply

- [ ] Authorization, quorum, digest, expiry, schema, and domain invariants were revalidated immediately before apply.
- [ ] The authoritative version still equals the approved base version; conflicts trigger a new request and approval.
- [ ] The write uses atomic compare-and-set and durable request-ID idempotency; last-write-wins is disabled.
- [ ] Configuration version, idempotency result, and propagation event are durably and transactionally recorded.
- [ ] Retry behavior distinguishes validation/authorization/conflict, known failure, and unknown timeout outcomes.

## Effective-state verification

- [ ] The authoritative store returns the expected new version and approved configuration digest.
- [ ] Every intended target acknowledged and independently reports the loaded version/digest.
- [ ] No unexpected mixed-version state, stale-cache state, or out-of-band mutation exists.
- [ ] Safe control probes/shadow evaluation passed; runtime risk enforcement and kill switch remain healthy.
- [ ] Reject rates, exposures, latency, errors, propagation lag, and stale-config metrics remain within abort thresholds.
- [ ] Audit/outbox records contain identities, UTC timestamps, versions, digest, correlation ID, and outcome without raw secrets.

## Close, reconcile, or roll back

- [ ] Workflow state, idempotency ledger, source of truth, distribution state, and runtime consumers agree.
- [ ] Any apply timeout was resolved by read-by-request-ID before retry; no blind duplicate write occurred.
- [ ] Partial propagation froze further changes and trading was stopped where safety could not be proven.
- [ ] If abort criteria fired, rollback created a new immutable version and was verified on every target.
- [ ] Evidence, approvals, monitoring results, exceptions, and final status are retained under policy.
- [ ] Monitoring ownership and observation-window completion are acknowledged.

Final status: [ ] Applied and verified  [ ] Rejected  [ ] Cancelled  [ ] Expired  [ ] Rolled back

Outcome/new version: ____________________  Closed at (UTC): ____________________

Exceptions/risk acceptance reference: ________________________________________________
