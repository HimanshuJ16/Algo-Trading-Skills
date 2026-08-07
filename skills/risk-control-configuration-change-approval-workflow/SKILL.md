---
name: risk-control-configuration-change-approval-workflow
description: >-
  Design or review maker-checker change control for trading risk configurations, including immutable payload approval, RBAC separation, expiry, optimistic concurrency, idempotent application, audit evidence, reconciliation, and rollback readiness. Use for production changes to order, position, exposure, credit, drawdown, throttling, or kill-switch parameters; do not use as a substitute for the runtime pre-trade risk gate or emergency kill switch.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- change-control
- maker-checker
- configuration-governance
brokers_frameworks:
- Broker-agnostic
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use when implementing or reviewing controlled changes to production trading-risk configuration. Apply the workflow to broker-, venue-, account-, strategy-, or global-scope limits whose incorrect modification could increase exposure or disable a control.

Do not use this workflow to approve ordinary strategy signals, to replace runtime risk enforcement, or as the sole path for emergency risk reduction. A separately authenticated break-glass path may only tighten limits or disable trading; it must be bounded, immediately audited, and retrospectively reviewed.

## Prerequisites

- Python 3.10+ for the dependency-free reference implementation.
- A typed, versioned risk schema and a pure domain validator that enforces cross-field invariants.
- Trusted IAM identities and server-derived roles; never accept `actor_id` or roles from an unverified request body.
- A durable request repository, append-only audit sink, and atomic versioned configuration store for production use.
- A deployment adapter able to verify propagation, runtime consumption, and effective values on every intended target.
- An incident-tested rollback and fail-safe procedure.

Read before implementation:

- `references/standards.md` for mandatory control and persistence requirements.
- `references/workflows.md` for state transitions, failure handling, rollout, reconciliation, and rollback.
- `assets/checklist.md` for the change record and production evidence checklist.

## Workflow

1. **Define the control boundary**: Identify scope, schema/policy version, authoritative store, runtime consumers, propagation semantics, fail-safe behavior, and rollback target.
2. **Validate and snapshot**: Validate types, units, precision, venue/account scope, cross-field relationships, and tighter/looser impact. Canonicalize one immutable payload and compute a server-side digest over the request ID, environment, base version, maker, reason, ticket, policy version, and proposed configuration.
3. **Submit against a base version**: Require a unique idempotency key, reason, ticket, owner, expiry, deployment window, validation evidence, and current version. Reject no-op, stale, secret-bearing, or semantically invalid payloads.
4. **Obtain independent approval**: Authorize the checker from trusted IAM claims, prohibit maker self-approval, present the exact diff and digest, and bind each approval to that digest. Any edit creates a new request and invalidates prior approvals.
5. **Apply atomically**: Revalidate immediately before application. Use compare-and-set on the approved base version and deduplicate by request ID. Never retry a non-idempotent write blindly.
6. **Verify effective state**: Confirm authoritative version, distribution acknowledgements, runtime-loaded digest, control health, and targeted probes. “API accepted” is not success.
7. **Reconcile and close**: Resolve timeouts by querying the idempotency key and effective version. Persist the final state and evidence. Roll back or fail closed if effective state cannot be proven.

The reference module demonstrates these invariants with `RiskConfigApprovalWorkflow`, `VersionedConfigStore`, and `InMemoryVersionedConfigStore`. The in-memory classes are test adapters, not production persistence.

## Decision Points

- **Tightening versus loosening**: Use stricter quorum, rollout, and monitoring for changes that increase permitted risk or weaken a control. Do not infer safety from a numerically smaller value; units and semantics determine direction.
- **Two versus three persons**: Maker-checker requires at least two distinct identities. Enable an independent applier when regulation, internal policy, or blast radius requires maker-checker-deployer separation.
- **Hot reload versus restart**: Use only mechanisms with observable acknowledgement and deterministic fallback. Treat mixed-version consumption as a failed rollout.
- **Emergency change**: Prefer a pre-authorized, monotonic risk-reduction operation. Never let break-glass increase limits or silently bypass audit.
- **Partial propagation**: Freeze further changes, stop affected trading when safety is uncertain, reconcile every target, and roll back to one known version.

## Common Pitfalls

- Approving a mutable database row rather than a digest-bound immutable snapshot.
- Letting the submitter approve through another account, client-supplied role, shared credential, or service principal.
- Applying with last-write-wins semantics after the base configuration has changed.
- Retrying after a timeout without an idempotency key or read-after-write reconciliation.
- Treating persistence success as proof that trading engines loaded the configuration.
- Logging full configuration payloads, credentials, account identifiers, or approval tokens.
- Expiring only pending approvals while allowing an already approved request to remain executable indefinitely.
- Using binary floating-point for values requiring exact tick, lot, currency, or percentage semantics without a canonical encoding policy.

## Expected Outputs and Success Criteria

- A stateful change record with `request_id`, environment/scope, base version, policy version, immutable digest, maker, checker(s), timestamps, reason, ticket, and terminal status.
- An append-only audit sequence containing identity, authorization decision, transition, version, and digest without secret configuration values.
- An atomic application result tied to the idempotency key and new version.
- Evidence that every intended runtime consumer loaded the approved digest, plus monitoring and rollback evidence.
- No unapproved mutation, self-approval, stale overwrite, duplicate application, or ambiguous timeout outcome.

## Verification

Run:

```bash
python scripts/test_risk_config_approval_workflow.py
```

The suite covers immutable snapshots, canonical digests, maker-checker separation, RBAC, multi-checker quorum, expiry, rejection/cancellation, optional independent deployment, stale-version conflicts, idempotent retries, lost-response reconciliation, secret/no-op/domain validation, UTC enforcement, and audit-chain hygiene.

Before production adoption, also run adapter-level concurrency tests, persistence crash tests, IAM integration tests, propagation fault injection, and a rollback exercise against a non-production environment.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `position-limit-breach-simulation-fire-drills`
- `log-aggregation-and-centralized-observability`
