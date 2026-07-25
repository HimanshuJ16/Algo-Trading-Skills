# Institutional API Migration Workflows

Migrating critical order execution paths requires strict adherence to phase-gated workflows. The following sequence ensures zero-downtime and protects capital.

## Phase 1: Preparation & V1 Baseline (V1_ONLY)
- **Objective**: Establish baseline latency and error rate metrics for the existing API.
- **Action**: All reads and writes are routed strictly through the V1 API. No structural changes are made.
- **Metrics Gathered**: V1 Round-trip time (RTT), standard deviation of latency, error codes baseline.

## Phase 2: Shadow Mode (SHADOW_MODE)
- **Objective**: Validate read equivalence and latency for the new API version.
- **Action**: 
  - Writes remain 100% on V1.
  - Reads (positions, order status, market data) are executed on V1 and *concurrently* executed on V2.
  - The results of V2 are discarded from the application logic but are passed to the `SchemaAuditDiff` engine.
- **Criteria to Pass**: 0% schema mismatches over 48 hours. V2 latency must be <= V1 latency + 5%.

## Phase 3: Canary Cutover (CANARY_CUTOVER)
- **Objective**: Incrementally expose write paths (orders, cancels) to the new API.
- **Action**:
  - Begin with a 1% to 5% canary split. `route_order_version` directs a fraction of payload traffic to V2.
  - Monitor execution reports and fill latency.
  - Ramp up canary percentage (10% -> 25% -> 50% -> 100%) across multiple trading sessions.
- **Emergency Action**: If fill rates drop or order rejections spike, instantly trigger `ROLLBACK_V1`.

## Phase 4: Full Cutover (V2_ONLY)
- **Objective**: Run exclusively on the new API.
- **Action**: 100% of read and write traffic is routed to V2.
- **Cleanup**: Deprecate V1 code paths after 2 weeks of stability.
