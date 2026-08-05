# Risk-Control Dependency Map Checklist

Graph/version: ____________________  Environment/scope: ____________________

Evidence revision/time (UTC): ____________________  Reviewer: ____________________

## Boundary and inventory

- [ ] Legal entity, broker/venue, account, strategy, instrument, session, and environment boundaries are explicit.
- [ ] Signal/order creation, pre-trade, routing, cancel, fill, position, post-trade, kill-switch, manual, and recovery paths are covered.
- [ ] Code, configuration, infrastructure, schemas, telemetry, runbooks, incidents, and external-system evidence were reviewed.
- [ ] Every node has a stable ID, type, owner, criticality, description, scopes, and recovery objective.
- [ ] Market/reference/FX feeds have freshness, completeness, ordering, precision, and validity contracts.
- [ ] Positions include working orders, partial fills, cancel/replace, external/manual trades, and reconciliation inputs.
- [ ] Clocks, calendars, DNS, IAM, credentials references, queues, caches, schemas, configuration, and actuators are represented where applicable.

## Dependency contracts

- [ ] Every edge points `dependency -> consumer` and states why the contract is required.
- [ ] Missing, stale, frozen, delayed, duplicate, reordered, corrupt, incomplete, unauthorized, rate-limited, and contradictory cases were considered.
- [ ] Each critical edge has observed `DEGRADE`, `FAIL_CLOSED`, or `FAIL_OPEN` behavior and health/freshness monitoring.
- [ ] Fail-closed behavior names the actual block/cancel/stop actuator and its dependencies.
- [ ] Every fail-open path has bounded exposure, immediate detection/containment, acceptance authority, owner, and remediation date.
- [ ] No secrets, credentials, tokens, private keys, or unnecessary sensitive limits/endpoints appear in graph attributes or exports.

## Redundancy and common causes

- [ ] Redundancy groups contain at least two capacity-sufficient, semantically compatible alternatives for one consumer.
- [ ] Vendor, region/AZ, network, DNS, IAM, credentials, certificates, code/artifact, schema, state, clock, power, and operator common causes are documented.
- [ ] Failover and failback were tested under peak capacity, rate limits, state catch-up, and subscription/replay conditions.
- [ ] Partial redundancy loss produces monitoring and a degraded-resilience state before the last alternative fails.
- [ ] Recovery and observability systems do not depend solely on the failed component.

## Graph validation and analysis

- [ ] Duplicate IDs/edges, unknown endpoints, and self-dependencies are rejected.
- [ ] Cycles have bounded startup, timeout, stale/default, convergence, and recovery semantics.
- [ ] Orphans, controls without dependencies, feeds without stale bounds, unmonitored edges, and singleton redundancy groups are resolved.
- [ ] Single-node and realistic simultaneous/common-cause scenarios were analyzed to a fixed point.
- [ ] Reports list affected and unsafe/fail-closed controls, scopes, owners, triggers, and maximum criticality.
- [ ] High/critical functional single points and degraded-only resilience risks have separate remediation priorities.
- [ ] JSON/DOT artifacts are versioned, reproducible, access-controlled, and linked to their evidence snapshot.

## Runtime verification

- [ ] The graph matches deployed manifests, service catalog, runtime calls/subscriptions, loaded configuration, and telemetry.
- [ ] Non-production tests injected disconnect, stale/frozen/corrupt data, backlog, rate limit, cache divergence, clock skew, partial partition, and state-store failover as relevant.
- [ ] Tests verified both control decisions and execution actuator outcomes, including working-order cancellation where required.
- [ ] Alerts reached named owners within the detection objective and contained enough context to act.
- [ ] Detection, failover, reconciliation, and restoration met recovery objectives without hidden exposure.
- [ ] Internal orders, fills, positions, balances, and broker/venue state can be reconciled after ambiguous failures.

## Governance and lifecycle

- [ ] Risk owners independently reviewed criticality, scope, redundancy, and fail-open/fail-closed classifications.
- [ ] New services, feeds, brokers/venues, controls, schemas, and configuration changes require map updates before rollout.
- [ ] Drift reconciliation has a defined cadence, owner, alert threshold, and escalation path.
- [ ] Previous graph versions and evidence are retained for incident-time reconstruction.
- [ ] Sensitive graph access, edit, approval, publication, and incident permissions follow least privilege.
- [ ] Remediation items have severity, accountable owner, target date, validation criteria, and risk-acceptance expiry.

Final disposition: [ ] Approved  [ ] Approved with exceptions  [ ] Rejected

Critical fail-open exceptions: _________________________________________________

Highest-priority single/common failure domains: _________________________________

Next reconciliation due (UTC): ____________________  Approval reference: ____________________
