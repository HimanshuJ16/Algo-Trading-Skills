# Risk-Control Dependency Mapping Workflows

Use these procedures with `standards.md`. The reference analyzer consumes an already authorized inventory; it does not discover or mutate production systems.

## Contents

- Establish the analysis boundary
- Discover and normalize inventory
- Build dependency contracts
- Validate the graph
- Run blast-radius scenarios
- Verify fail-open and fail-closed behavior
- Analyze redundancy and common causes
- Use the graph during changes
- Use the graph during incidents
- Reconcile drift and retire nodes
- Production data contract

## Establish the analysis boundary

1. Identify environment, legal entity, brokers/venues, accounts, strategies, instrument classes, trading sessions, and control objectives.
2. Trace all paths capable of creating, modifying, routing, cancelling, or externally introducing orders and positions, including manual and recovery paths.
3. Record authoritative inventories and their revisions: source repositories, infrastructure, service catalog, schemas, configuration stores, broker adapters, and telemetry.
4. Define what is excluded, why it is excluded, and who accepted the residual risk.
5. Timestamp and version the snapshot. Incident analysis must use the graph effective at the incident time, not only the latest graph.

## Discover and normalize inventory

Collect evidence in this order:

1. **Code and configuration:** Find risk decisions, feature flags, config reads, subscriptions, RPC clients, database queries, cache access, time sources, and order-routing calls.
2. **Infrastructure:** Resolve services, queues/topics, stores, caches, regions/AZs, networks, DNS, IAM/workload identities, certificates, secrets references, and deployment artifacts.
3. **Runtime telemetry:** Compare observed calls, subscriptions, loaded versions, broker sessions, feed sequence/freshness, and control decisions with declared topology.
4. **Operations:** Review runbooks, dashboards, alerts, failover/rollback procedures, manual interventions, incident reports, and emergency paths.
5. **External systems:** Record broker/exchange APIs, market/reference/FX vendors, calendars, regulatory/credit services, and their rate, session, precision, and recovery constraints.

Normalize each logical dependency to a stable `RiskNode`. Split one physical component into multiple logical nodes when market data, position state, configuration, and actuation fail differently. Merge replicas only through explicit redundancy groups, never by hiding them behind one generic “cluster” node.

## Build dependency contracts

For each `dependency -> consumer` relationship:

1. State why the consumer needs it and which safety/correctness property depends on it.
2. Specify accepted freshness, completeness, ordering, precision, capacity, authorization, and semantic validity.
3. Define detection: heartbeat, sequence/gap check, timestamp age, reconciliation, checksum/schema validation, quorum, or independent comparison.
4. Classify observed response to contract failure:
   - `DEGRADE`: continue with explicitly bounded functionality or reduced redundancy.
   - `FAIL_CLOSED`: block/stop/cancel so new risk cannot increase.
   - `FAIL_OPEN`: continue without a valid control decision or with potentially unsafe data.
5. Record the actual actuator behavior. “Control returns reject” is insufficient if the gateway ignores, times out, overrides, or cannot deliver that decision.
6. Record evidence and owner outside the reference graph if the local schema does not carry evidence URLs.

### Redundancy groups

Assign a shared `redundancy_group` only when all members are substitutable alternatives for the same consumer contract. Verify:

- each surviving member handles full required capacity and scope;
- failover does not require the failed dependency;
- formats, semantics, symbols, clocks, and precision remain compatible;
- shared vendor, region, network, DNS, IAM, credentials, code, state, and operator domains are understood;
- failover has been exercised within its recovery objective.

Loss of one alternative should produce a degraded-resilience signal. Loss of every alternative applies the group’s declared failure response.

## Validate the graph

1. Construct `RiskDependencyMapper(nodes, edges)`. Structural corruption—duplicates, unknown endpoints, or self-dependencies—must fail immediately.
2. Run `validate()` and disposition every issue:
   - cycle requiring bounded startup/recovery semantics;
   - control without dependencies;
   - orphan node;
   - data feed without a staleness bound;
   - fail-open dependency;
   - unmonitored dependency;
   - singleton or inconsistently configured redundancy group.
3. Compare graph counts and IDs with authoritative inventories. Validation cannot detect an omitted node that never entered the input.
4. Have control owners review dependencies and have independent risk reviewers confirm fail behavior and criticality.
5. Reject publication if high/critical controls, actuation paths, owners, or evidence are incomplete.

Cycles may represent real feedback such as exposure updates influenced by order decisions. Do not falsify the model by deleting them. Document initialization order, stale/default state, timeout, convergence, and recovery, then test these semantics explicitly.

## Run blast-radius scenarios

1. Select scenarios from architecture, common failure domains, incidents, vendor limits, and planned changes.
2. Include simultaneous nodes for region, network, credential, deployment, schema, clock, or upstream-vendor failures.
3. Call `analyze(failed_nodes)` and capture the versioned graph plus ordered JSON report.
4. Review:
   - direct and propagated impacts;
   - affected controls and scopes;
   - `FAIL_OPEN` controls requiring immediate remediation;
   - `FAIL_CLOSED` controls and whether closure reaches the actuator;
   - degraded controls running without redundancy;
   - responsible owners and maximum criticality.
5. Render `to_dot(report)` for review, but use JSON as the machine-readable evidence. Protect both according to topology sensitivity.
6. Challenge the result for missing dependencies and common causes; graph analysis is only as complete as its inventory.

The reference algorithm propagates to a deterministic fixed point. A degraded dependency degrades an ungrouped consumer; a functional failure applies the edge response. Partial redundancy loss degrades the consumer, while loss of all alternatives applies the group response.

## Verify fail-open and fail-closed behavior

For each critical edge, test unavailable, stale, frozen, corrupt, incomplete, reordered, unauthorized, rate-limited, slow, and contradictory inputs where applicable.

### Fail-closed verification

1. Observe the control detect the contract failure within the stated bound.
2. Verify the decision blocks new exposure and reaches every order path.
3. Verify working-order cancellation when policy requires it.
4. Verify local cache, restart, reconnect, delayed messages, and partial network partition cannot restore permissive behavior.
5. Verify the operator sees actionable alerts and can reconcile broker/exchange state.

### Fail-open verification

1. Treat observed fail-open behavior as a defect or formally governed exception.
2. Quantify maximum exposure using order rate, working orders, account/venue scope, detection delay, and actuation delay.
3. Add an independent compensating control that does not share the dependency where feasible.
4. Page immediately on activation and define automatic containment.
5. Track remediation owner, due date, acceptance authority, and expiry.

Do not intentionally submit unsafe live orders. Use unit/integration harnesses, shadow decisions, broker simulators, paper environments, or bounded non-production probes.

## Analyze redundancy and common causes

1. Run `single_points_of_failure()` and prioritize dependencies functionally impairing high/critical controls.
2. Review degraded-only results separately; they identify loss of resilience before total control failure.
3. Create simultaneous scenarios for every shared failure domain.
4. Verify capacity and compatibility after failover, including peak market load, rate limits, symbol mapping, precision, clock source, and state catch-up.
5. Verify failback does not duplicate subscriptions, replay stale state, double-count positions, or create mixed configuration/data versions.

## Use the graph during changes

1. Diff the proposed deployment/configuration against the approved graph.
2. Identify added, removed, or behaviorally changed nodes, edges, scopes, owners, stale bounds, redundancy, or failure responses.
3. Run blast-radius scenarios for the changed component and its common failure domains.
4. Require risk review when a change adds fail-open behavior, widens scope, increases criticality, removes monitoring, weakens freshness, or reduces redundancy.
5. Update and approve the graph with the change; do not defer documentation until after production deployment.
6. Verify runtime topology and health after rollout before closing the change.

## Use the graph during incidents

1. Pin the graph version effective when the incident began.
2. Seed analysis with confirmed and suspected failed/degraded dependencies; keep confidence/evidence in the incident system.
3. Prioritize unsafe controls and affected scopes. Stop trading or activate a kill switch when safety cannot be established.
4. Contact owners from the report while independently checking actual runtime state.
5. Reconcile internal orders/positions/balances with brokers or venues; API timeout is an unknown outcome, not proof of failure.
6. Record hidden edges, incorrect failure classifications, fake redundancy, stale ownership, and missing telemetry as incident findings.
7. Update and revalidate the graph through normal change control after containment.

## Reconcile drift and retire nodes

At the defined cadence and after material deployment:

1. Compare graph nodes/edges with code, manifests, service catalog, subscriptions/calls, configuration versions, and telemetry.
2. Flag unknown runtime dependencies, modeled-but-unobserved edges, unowned components, expired evidence, and scope changes.
3. Do not automatically delete a quiet edge; market/session conditions may make valid dependencies temporarily inactive.
4. Route discrepancies to owners with severity based on affected control criticality and fail behavior.
5. Retire a node only after all consumers, manual/shadow paths, alerts, recovery procedures, and historical references are addressed.
6. Publish a new immutable graph version and retain the previous snapshot for incident reconstruction.

## Production data contract

When adapting the reference classes to a registry or graph store, preserve:

- stable, unique node IDs and directed dependency/consumer IDs;
- explicit enum validation rather than silently accepting arbitrary strings;
- immutable/versioned snapshots for each analysis;
- deterministic ordering and canonical serialization;
- distinct redundancy groups per consumer contract;
- owner, criticality, scopes, freshness, recovery objective, response, rationale, monitoring, and evidence references;
- source revision, collection time, environment, and authorization context;
- access controls and redaction for sensitive topology;
- idempotent ingestion and conflict detection;
- change history and drift evidence.

Discovery collectors must be read-only by default, use least privilege, paginate/retry safely, distinguish stale cached inventory from live state, and avoid logging credentials or full sensitive configurations.
