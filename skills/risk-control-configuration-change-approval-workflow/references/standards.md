# Risk Configuration Change-Control Standards

Use these requirements when adapting the reference workflow. “Must” denotes a production control; deviations require documented risk acceptance.

## Contents

- Control boundary
- Configuration model
- Identity and authorization
- Approval integrity
- Persistence and concurrency
- Application and propagation
- Audit and observability
- Security and privacy
- Availability and recovery
- Verification gates

## Control boundary

- **CC-01 — Independent runtime enforcement:** Approval controls who may change risk configuration. Runtime pre-trade and post-trade controls must independently enforce the effective limits and fail safe when configuration is missing, invalid, stale beyond policy, or inconsistent.
- **CC-02 — Explicit scope:** Every field must have documented units, precision, valid range, default prohibition/behavior, scope hierarchy, and tighter/looser semantics. Define precedence among global, venue, broker, account, strategy, instrument, and session scopes.
- **CC-03 — Emergency boundary:** Maintain a separately authenticated emergency path for disabling trading or monotonically reducing risk. It must not increase exposure, weaken a kill switch, bypass durable audit, or become the normal change path.
- **CC-04 — Authoritative source:** Declare one authoritative configuration version. Caches, files, environment variables, and runtime replicas must not accept out-of-band production edits.

## Configuration model

- **CFG-01 — Typed schema:** Validate schema version, required fields, enumerations, units, bounds, and unknown fields. Reject implicit coercion such as strings to numbers.
- **CFG-02 — Domain invariants:** Validate relationships such as order limit ≤ position limit, strategy allocation ≤ account limit, daily loss limit ≤ approved capital, and venue limits ≤ broker constraints. A syntactically valid payload is not necessarily safe.
- **CFG-03 — Exact representation:** Represent monetary values, prices, quantities, tick/lot sizes, and ratios with an agreed canonical format. If binary floats are allowed, reject NaN and infinity and document cross-language digest behavior. Prefer fixed-point integers or decimal strings where exactness is required.
- **CFG-04 — Immutable snapshot:** Canonicalize the complete proposed configuration server-side. Bind the request, diff, review, approval, and apply operation to one cryptographic digest. Any mutation requires a new request and approvals.
- **CFG-05 — Versioned policy:** Include the risk schema and approval-policy version in the signed/hashed approval context. Revalidate at apply time; invalidate or re-review requests after materially incompatible policy changes.
- **CFG-06 — No secrets:** Risk configuration, diffs, reasons, tickets, logs, and events must not contain API keys, private keys, passwords, bearer tokens, session cookies, or approval credentials. Reference secrets through a secrets manager identifier only when unavoidable.
- **CFG-07 — Bounded payload:** Enforce request size and nesting limits before parsing or persistence.

## Identity and authorization

- **IAM-01 — Trusted principal:** Derive actor identity, roles, tenant, and environment entitlements from verified IAM/session claims. Never trust user-supplied identity or role fields.
- **IAM-02 — Maker-checker separation:** The submitter must not approve the same request. Shared users, generic accounts, and interchangeable service credentials do not satisfy separation of duties.
- **IAM-03 — Least privilege:** Separate submit, approve/reject, apply, cancel, emergency, and policy-administration permissions. Scope permissions by environment and risk domain.
- **IAM-04 — Fresh authorization:** Re-evaluate authorization at every transition. Role removal must prevent new privileged actions; historical approvals remain attributable to the identity and policy effective when recorded, subject to the organization’s revocation rules.
- **IAM-05 — Strong authentication:** Require phishing-resistant MFA or workload identity appropriate to the actor and protect service-to-service calls with short-lived credentials and authenticated transport.
- **IAM-06 — Independent applier:** Require a third identity when regulation, risk appetite, or blast radius demands maker-checker-deployer separation. Otherwise, preserve at least two distinct people across submission and approval.

## Approval integrity

- **APR-01 — Exact review context:** Show the checker current value, proposed value, effective scope, units, direction of risk, base version, digest, validation evidence, rollout plan, and rollback version.
- **APR-02 — Quorum:** Count distinct authorized identities only once. Evaluate required roles and quorum from versioned server-side policy.
- **APR-03 — Expiry:** Expire pending and approved-but-unapplied requests. Use UTC, reject naive timestamps, and define the boundary consistently (`now >= expires_at` is expired).
- **APR-04 — Terminal states:** Reject, cancel, expire, and apply are terminal for that request. Do not resurrect or edit terminal records.
- **APR-05 — Rejection evidence:** Require a reason and retain it with the reviewer identity and digest. Do not convert a rejected request back to pending.
- **APR-06 — Idempotent commands:** Duplicate submission and approval commands with the same idempotency key and digest must return the existing result. Reuse with different content must fail as an integrity violation.

## Persistence and concurrency

- **DAT-01 — Durable aggregate:** Persist request state, approvals, terminal outcome, and an outbox/audit record transactionally. In-memory state is suitable only for examples and tests.
- **DAT-02 — Optimistic concurrency:** Apply only when the authoritative version equals the approved base version. A mismatch requires a new diff, validation, and approval; never auto-rebase an approval.
- **DAT-03 — Atomic apply:** The configuration store must atomically verify expected version, write the new immutable version, and record the request idempotency key. Last-write-wins is prohibited.
- **DAT-04 — Uniqueness:** Enforce unique request IDs and unique approval identity per request at the database layer, not only in process memory.
- **DAT-05 — Transactional messaging:** Publish propagation and audit events with an outbox or equivalent atomic mechanism. Consumers must deduplicate and tolerate redelivery.
- **DAT-06 — Isolation:** Test concurrent approve/reject/cancel/apply races. Exactly one valid terminal outcome may win, with deterministic conflicts for the rest.
- **DAT-07 — Retention:** Retain records, previous values, identities, timestamps, and evidence according to legal and internal policy. Protect retention from ordinary application administrators.

## Application and propagation

- **APP-01 — Revalidate:** Repeat schema and domain validation immediately before apply. Fail closed if the validator or policy version is unavailable or incompatible.
- **APP-02 — No blind retry:** On timeout, query by request ID and version before retry. The adapter must provide an idempotent result or an unambiguous not-applied result.
- **APP-03 — Observable rollout:** Record each target’s acknowledgement, loaded version/digest, and effective timestamp. API/write acknowledgement alone is insufficient.
- **APP-04 — Consistent consumption:** Define whether targets switch atomically, by bounded rolling update, or at a future activation time. Prevent mixed versions from permitting unintended aggregate exposure.
- **APP-05 — Broker/venue constraints:** Validate approved values against live broker and venue constraints before activation, including price/quantity precision, rate limits, supported order types, account permissions, and session state. External rejection must not leave internal state falsely marked effective.
- **APP-06 — Stale data:** Configuration approval must not rely on stale capital, FX, instrument metadata, market status, or broker-limit snapshots. Define freshness thresholds and fail-safe behavior.
- **APP-07 — Rollback:** Rollback is a new versioned operation tied to the incident/change record. It may use an emergency tightening path only when the result is provably no less restrictive.

## Audit and observability

- **OBS-01 — Audit content:** Record request ID, event type, actor, trusted role context, UTC time, environment/scope, base/new version, policy version, digest, outcome, correlation ID, and reason code. Do not log raw configuration by default.
- **OBS-02 — Tamper evidence:** Store audit events in an append-only, access-controlled sink. Hash chaining improves detection but does not replace external immutability, signing, restricted administration, or retention controls.
- **OBS-03 — Metrics:** Monitor submission-to-approval latency, approval-to-apply latency, expirations, rejections, authorization failures, version conflicts, apply retries, reconciliation repairs, propagation lag, mixed-version targets, and rollback frequency.
- **OBS-04 — Alerts:** Page on unauthorized attempts, repeated digest mismatch, ambiguous apply outcome, partial propagation, invalid runtime configuration, audit/outbox failure, or inability to reconcile.
- **OBS-05 — Correlation:** Propagate request and configuration version identifiers through deployment, runtime, broker adapter, and incident telemetry without propagating secrets.

## Security and privacy

- **SEC-01 — Encryption:** Encrypt request, approval, and audit data in transit and at rest; manage keys outside application configuration.
- **SEC-02 — Output minimization:** Redact account identifiers and commercially sensitive thresholds from general logs and chat/notification systems. Grant detailed diff access only to authorized reviewers.
- **SEC-03 — Injection resistance:** Treat reasons, tickets, comments, and imported configuration as untrusted data. Escape output and never execute templates, expressions, hooks, or shell fragments from a request.
- **SEC-04 — Supply chain:** Pin and scan production dependencies, sign deployment artifacts, and bind the deployed validator/policy artifact to a reviewed release.
- **SEC-05 — Administrative separation:** Restrict direct database and audit-store access. Administrative mutation must itself be controlled and independently audited.

## Availability and recovery

- **REC-01 — Fail-safe mode:** Define behavior when IAM, approval storage, configuration storage, propagation, or audit services are unavailable. Never default to a more permissive risk configuration.
- **REC-02 — Reconciliation:** Run scheduled reconciliation across request state, applied-version ledger, authoritative store, distribution layer, and runtime consumers. Repair only from durable evidence and emit a repair event.
- **REC-03 — Backup and restore:** Back up configuration history and approval evidence, test restoration, and verify that restored idempotency records prevent replay.
- **REC-04 — Disaster recovery:** Preserve ordering/version guarantees across failover. Split-brain writers are prohibited; recovery-point and recovery-time objectives must match trading risk appetite.
- **REC-05 — Clock discipline:** Use UTC and synchronized clocks for evidence and expiry. Use database/server time for authorization decisions where practical; never rely on a client clock.

## Verification gates

Production readiness requires evidence for all of the following:

- State-machine and authorization tests cover every allowed and denied transition.
- Property/concurrency tests demonstrate no self-approval, duplicate quorum, stale overwrite, or multiple terminal outcomes.
- Storage tests inject crashes before and after request, outbox, config, and idempotency commits.
- Adapter tests inject timeouts, duplicate delivery, partial propagation, stale reads, and broker/venue rejection.
- Security tests cover role forgery, cross-environment access, identifier collision, secret leakage, oversized payloads, and audit tampering.
- Operational tests prove detection, reconciliation, rollback, emergency tightening, and restoration from backup.
