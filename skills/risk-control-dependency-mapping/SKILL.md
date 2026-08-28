---
name: risk-control-dependency-mapping
description: >-
  Build or review dependency graphs and blast-radius analyses for trading risk controls, covering market/reference data, positions, orders, balances, FX, clocks, state stores, services, broker/exchange inputs, control decisions, and execution actuators. Use for architecture reviews, change impact, incident response, resilience testing, fail-open detection, redundancy validation, recovery planning, and operational ownership; do not use a static graph as a substitute for runtime health, freshness, lineage, or control enforcement.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- dependency-mapping
- blast-radius
- resilience
brokers_frameworks:
- Broker-agnostic
version: "1.2.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to identify what can impair or bypass a trading risk control and what downstream controls, strategies, accounts, venues, or order paths a dependency failure can affect. Apply it during new-control design, production-readiness review, configuration or infrastructure change assessment, incident triage, disaster-recovery planning, and resilience exercises.

Do not treat the generated graph as proof of runtime safety. Static mapping cannot establish current feed freshness, consumer-loaded configuration, broker state, hidden manual paths, or successful failover. Connect the inventory to runtime evidence and periodically reconcile it against deployed topology.

## Prerequisites

- Python 3.10+ for the dependency-free reference analyzer.
- An authoritative inventory of controls, services, feeds, stores, execution paths, environments, owners, and scopes.
- Explicit dependency contracts: freshness, completeness, precision, availability, recovery objective, redundancy semantics, and failure response.
- Evidence from code, infrastructure, message schemas, configuration, broker/venue adapters, telemetry, and operator procedures—not interviews alone.
- Named risk and engineering reviewers authorized to classify fail-open/fail-closed behavior.

Read before implementation:

- `references/standards.md` for graph, evidence, failure-semantics, security, and lifecycle requirements.
- `references/workflows.md` for discovery, validation, analysis, incident, change, and reconciliation procedures.
- `assets/checklist.md` for the production mapping and sign-off artifact.

## Workflow

1. **Set the boundary**: Define environment, trading flows, control objectives, scope hierarchy, authoritative inventories, and analysis timestamp.
2. **Inventory nodes**: Record feeds, state stores, services, controls, actuators, and external systems with stable IDs, owners, criticality, scopes, freshness bounds, and recovery objectives.
3. **Map directed contracts**: Draw each edge from dependency to consumer. Record why it is required, health/freshness monitoring, fail-open/fail-closed/degraded response, and true alternative redundancy groups.
4. **Validate topology**: Reject unknown, duplicate, and self-referential edges. Review cycles, orphan nodes, controls without inputs, feeds without staleness bounds, singleton redundancy groups, unmonitored contracts, and every fail-open edge.
5. **Analyze failures**: Evaluate simultaneous failures to a fixed point. Include stale, corrupt, delayed, incomplete, partitioned, and semantically invalid data—not only process-down scenarios.
6. **Prioritize exposure**: Identify unsafe controls, fail-closed controls, high-criticality single points, affected scopes, responsible owners, mixed failure domains, and recovery dependencies.
7. **Verify reality**: Compare the model with deployed configuration and telemetry, inject representative failures outside production, and confirm control/actuator behavior and alert routing.
8. **Publish and maintain**: Version the model, retain evidence and assumptions, link it to change/incident workflows, and reconcile drift on a defined cadence.

The reference `RiskDependencyMapper` provides immutable nodes/edges, deterministic validation, conservative fixed-point propagation, redundancy modeling, single-point analysis, JSON reports, and Graphviz DOT output. It does not discover infrastructure or poll production systems.

## Decision Points

- **Dependency versus correlation**: Add an edge only when the consumer’s safety, correctness, or availability relies on the source. Record shared failure domains separately; correlation alone is not a directed contract.
- **Redundancy group**: Group sources only when each is a tested, capacity-sufficient, independently failed-over alternative. Two feeds backed by the same vendor, network, credentials, clock, or parser are not independent.
- **Fail closed versus fail open**: Determine observed consumer behavior for stale, missing, invalid, and contradictory input. Documentation intent is insufficient.
- **Degradation propagation**: Treat degraded risk data conservatively. The reference analyzer propagates degradation without declaring total loss until every alternative is functionally lost; an alternative that is itself degraded but still serving counts as available, so a redundant contract never models worse than a single one.
- **Cycle handling**: Retain a real cycle, but classify it as an error requiring bounded startup, recovery, and failure semantics. Do not delete an edge merely to obtain a DAG.
- **Static versus runtime graph**: Use static models for review and scenario analysis; use telemetry/service catalogs for continuous verification. Reconcile the two rather than choosing one.

## Common Pitfalls

- Mapping only direct feeds while omitting clocks, reference data, schema registries, credentials, DNS, queues, state stores, feature flags, and execution actuators.
- Equating process health with valid data; a connected feed may be stale, frozen, duplicated, incomplete, or incorrectly normalized.
- Labeling active/passive instances as redundant despite a shared upstream, region, credential, deployment artifact, or corrupted state.
- Assuming a control fails closed without testing timeout, cache, restart, reconnect, and partial-partition paths.
- Ignoring aggregate exposure when strategies or accounts share a dependency.
- Treating the broker’s rejection controls as a substitute for participant-controlled pre-trade risk controls.
- Publishing sensitive topology, account identifiers, limits, credentials, or exploitable fail-open paths without access control.
- Allowing graph ownership or evidence to become stale after deployments and configuration changes.

## Expected Outputs and Success Criteria

- A versioned node/edge inventory with stable IDs, owners, scopes, criticality, dependency contracts, failure behavior, redundancy groups, and evidence references.
- A validation report with no unexplained cycles, orphan controls, missing freshness bounds, fake redundancy, unmonitored critical edges, or accepted fail-open behavior.
- Scenario reports listing affected controls, unsafe/fail-closed behavior, scopes, owners, and maximum criticality.
- A prioritized single-point/shared-failure-domain remediation backlog with named owners and target dates.
- Runtime or non-production fault-injection evidence confirming the modeled behavior and recovery procedure.
- A drift-reconciliation cadence and change-control rule that keeps the graph synchronized with deployed systems.

## Verification

Run:

```bash
python scripts/test_risk_dependency_mapper.py
```

The suite covers redundant-source degradation, degraded-but-serving alternatives, complete redundancy loss, multi-hop propagation, fail-open exposure, simultaneous failures, deterministic JSON, single-point analysis including control-to-control dependencies, cycles, missing staleness, unmonitored edges, invalid redundancy, structural rejection, enum/value/argument validation, and escaped DOT output.

Before production adoption, replay the inventory against deployed manifests and telemetry, inject stale/corrupt/disconnected dependencies in a non-production environment, and verify control decisions, kill switches, alerts, recovery objectives, and owner escalation.

## Related Skills

- `risk-control-configuration-change-approval-workflow`
- `risk-control-latency-budget`
- `kill-switch-and-drawdown-circuit-breakers`
- `position-limit-breach-simulation-fire-drills`
- `log-aggregation-and-centralized-observability`
