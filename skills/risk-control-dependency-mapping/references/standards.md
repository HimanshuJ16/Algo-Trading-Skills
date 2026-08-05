# Risk-Control Dependency Mapping Standards

Use these requirements for production dependency models. “Must” denotes a required control; exceptions require documented risk acceptance.

## Contents

- Scope and evidence
- Node and edge model
- Data and trading semantics
- Failure and redundancy semantics
- Analysis and verification
- Security and access
- Ownership and lifecycle
- Acceptance gates

## Scope and evidence

- **MAP-01 — Explicit boundary:** Record environment, legal entity, venue/broker, account, strategy, instrument class, order paths, control objectives, analysis timestamp, and excluded systems.
- **MAP-02 — Complete lifecycle:** Map signal/order creation, pre-trade checks, routing, broker/exchange acknowledgement, fills, positions, balances, post-trade limits, kill switches, and operator intervention paths.
- **MAP-03 — Evidence-backed:** Derive edges from code, configuration, infrastructure manifests, schemas, deployment topology, access policies, telemetry, and fault tests. Interviews and diagrams alone are insufficient.
- **MAP-04 — Stable identity:** Give every node and edge a stable identifier. Do not use mutable hostnames, pod IDs, or display labels as primary keys.
- **MAP-05 — Timestamped snapshot:** Version the graph and retain the source revision, inventory time, evidence references, and policy/schema version used to produce it.

## Node and edge model

- **MOD-01 — Directed meaning:** Orient every edge `dependency -> consumer`. State why the consumer depends on the source and what contract violation activates the failure response.
- **MOD-02 — Typed nodes:** Distinguish data feeds, state stores, services, risk controls, actuators, and external systems. Model a combined component as separate logical nodes when its failure semantics differ by responsibility.
- **MOD-03 — Required attributes:** Record owner, criticality, scopes, description, recovery objective, and—where applicable—freshness, completeness, precision, and capacity requirements.
- **MOD-04 — No silent references:** Reject unknown nodes, duplicate IDs, duplicate edges, and self-dependencies. Orphans and controls without mapped inputs require review.
- **MOD-05 — Cycles:** Detect dependency cycles. Preserve real cycles but document bounded startup, health, failover, and recovery behavior; an unexamined cycle is an error.
- **MOD-06 — Scope hierarchy:** Represent global, broker, venue, account, strategy, instrument, and session scopes without assuming a one-to-one deployment topology.
- **MOD-07 — Actuation path:** Map control outputs through decision aggregators, order gateways, cancel paths, broker sessions, and kill-switch actuators. A correct decision that cannot block or cancel an order is not an effective control.

## Data and trading semantics

- **DAT-01 — Market data:** Map price source, book/trade channel, sequence/gap detector, timestamp/clock source, normalization, instrument mapping, cache, and stale-data policy.
- **DAT-02 — Positions and orders:** Include working orders, partial fills, cancel/replace state, late/out-of-order executions, allocation state, external/manual trades, and reconciliation inputs. Broker API success is not position truth.
- **DAT-03 — Balances and credit:** Map broker balances, internal ledger, unsettled cash, margin model, credit allocation, collateral haircuts, and update cadence.
- **DAT-04 — Reference data:** Include tick/lot sizes, contract multipliers, currencies, expiries, trading status, corporate actions, symbol mappings, and venue rules. Stale metadata can invalidate otherwise fresh prices.
- **DAT-05 — FX and valuation:** Record FX sources, currency triangulation, valuation time, stale bounds, and fallback semantics. Aggregate limits must not silently omit unconvertible exposure.
- **DAT-06 — Time:** Map NTP/PTP source, monotonic timers, timezone/session calendar, and clock-skew monitoring wherever freshness, throttling, expiry, or daily resets depend on time.
- **DAT-07 — Configuration:** Map authoritative risk configuration, version distribution, cache/hot reload, schema/validator, and runtime acknowledgement.
- **DAT-08 — Precision:** Document decimal/fixed-point representation, rounding direction, overflow behavior, and broker/venue precision. Binary floating-point assumptions must not change a threshold boundary.

## Failure and redundancy semantics

- **FAIL-01 — Contract failures:** Analyze unavailable, stale, frozen, delayed, duplicated, reordered, incomplete, corrupt, contradictory, unauthorized, rate-limited, and semantically invalid dependencies.
- **FAIL-02 — Explicit response:** Classify each consumer response as degraded, fail closed, or fail open for each relevant failure class. Verify observed behavior; configuration intent is not evidence.
- **FAIL-03 — Fail-open exposure:** Treat every fail-open path as an error requiring removal, compensating control, or formally accepted residual risk with immediate detection and bounded exposure.
- **FAIL-04 — Safe closure:** “Fail closed” must identify the actual action: reject new orders, cancel working orders, freeze strategy, disconnect routing, or activate kill switch. Confirm closure itself has functioning dependencies.
- **FAIL-05 — True alternatives:** Put dependencies in one redundancy group only when any surviving member is capacity-sufficient, compatible, timely, and exercised automatically or by a tested procedure.
- **FAIL-06 — Shared failure domains:** Record vendor, region/AZ, network, DNS, credentials, certificate authority, IAM, deployment artifact, code/library, schema, state, clock, power, and operator dependencies. Nominal replicas sharing one domain are not independent.
- **FAIL-07 — Partial redundancy loss:** Surface loss of redundancy as degraded resilience even if service remains correct. Alert before the final alternative fails.
- **FAIL-08 — Simultaneous failures:** Evaluate combinations selected from common-cause domains and realistic incident history; single-node analysis alone is insufficient.
- **FAIL-09 — Recovery dependency:** Map what detection, failover, reconciliation, restart, and rollback require. A recovery system that depends on the failed component does not meet its objective.

## Analysis and verification

- **ANA-01 — Determinism:** The same versioned graph and failure set must produce identical ordered results suitable for review and regression testing.
- **ANA-02 — Conservative propagation:** Do not treat degraded or unverified risk data as healthy. State propagation assumptions explicitly and distinguish degraded resilience from functional loss.
- **ANA-03 — Fixed point:** Continue propagation until no impact changes. Bound or reject analysis that cannot converge because of custom cyclic semantics.
- **ANA-04 — Prioritization:** Report affected controls, fail-open controls, fail-closed controls, scopes, owners, criticality, and triggering dependencies.
- **ANA-05 — Single points:** Identify individual failures that cause functional loss or unsafe behavior in high/critical controls. Track reduced redundancy separately to avoid conflating the two.
- **ANA-06 — Runtime corroboration:** Compare the model with service catalogs, deployed manifests, subscriptions, configuration versions, telemetry, and runtime-loaded dependencies.
- **ANA-07 — Fault injection:** Test disconnect, stale/frozen feed, corrupt payload, queue backlog, rate limit, cache divergence, clock skew, partial network partition, state-store failover, and broker API ambiguity outside production.
- **ANA-08 — Expected response:** Validate both risk decision and actuator outcome, including working-order cancellation and downstream broker/exchange state.
- **ANA-09 — Recovery proof:** Measure detection, failover, reconciliation, and restoration against objectives; verify no hidden exposure accumulated during impairment.

## Security and access

- **SEC-01 — Sensitive topology:** Restrict graphs revealing accounts, credentials, endpoints, control thresholds, privileged paths, or exploitable fail-open behavior.
- **SEC-02 — No secrets:** Store secret references, never credentials, tokens, private keys, cookies, or broker session material in graph attributes or exports.
- **SEC-03 — Trusted ingestion:** Validate and authenticate inventory sources. Treat imported labels/descriptions as untrusted when rendering DOT, HTML, tickets, or dashboards.
- **SEC-04 — Least privilege:** Separate graph discovery, editing, risk classification, approval, publishing, and incident access as appropriate to environment and sensitivity.
- **SEC-05 — Integrity:** Version, review, and protect graph and evidence artifacts from unauthorized mutation. Sign or immutably retain approved production snapshots where required.

## Ownership and lifecycle

- **OPS-01 — Named owner:** Every node, critical edge, fail-open exception, and remediation must have a service/team owner and escalation route; shared aliases without accountability are insufficient.
- **OPS-02 — Change integration:** Require dependency-map review for new feeds, services, controls, brokers/venues, scopes, failover paths, schema/configuration changes, and decommissioning.
- **OPS-03 — Drift detection:** Reconcile the approved model with deployed topology and telemetry on a defined cadence and after material deployments.
- **OPS-04 — Incident integration:** Use the version effective at incident time, record discrepancies as findings, and feed confirmed hidden dependencies back into the graph.
- **OPS-05 — Deprecation:** Remove nodes only after consumers, historical evidence, alerts, recovery procedures, and shadow/manual paths are verified absent.
- **OPS-06 — Metrics:** Monitor stale inventory age, unowned nodes, unmonitored critical edges, fail-open count, single-point count, drift, fault-test age, and overdue remediation.

## Acceptance gates

Production readiness requires evidence that:

- all high/critical controls and their actuation paths are represented;
- all data feeds have explicit freshness and validity contracts;
- every critical dependency has monitored failure behavior and an owner;
- fail-open behavior is eliminated or formally accepted with bounded exposure;
- redundancy groups have tested independence, capacity, and failover;
- common-cause and simultaneous failure scenarios are analyzed;
- fault tests confirm modeled control, actuator, alert, and recovery behavior;
- the graph matches deployed topology and has a defined drift-reconciliation cadence.
