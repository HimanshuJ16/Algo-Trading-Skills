---
name: disaster-recovery-runbook-for-full-region-outage
description: Quantitative infrastructure disaster recovery (DR) engine for orchestrating
  automated multi-region failover (DNS switchover, database promotion, emergency order
  cancellation, position reconciliation) under a 300-second RTO SLA.
domain: Infrastructure & DevOps
subdomain: Disaster Recovery & Multi-Region Resilience
tags:
- disaster-recovery
- region-failover
- rto-rpo
- dns-switchover
- aurora-global-db
- order-cancel-killswitch
- multi-region
brokers_frameworks:
- AWS Route 53 ARC
- Aurora Global DB
- Python Dataclasses
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in quantitative infrastructure engineering, cloud SRE operations, and high-availability trading systems. Catastrophic cloud region outages (e.g. AWS `us-east-1` network blackouts or power loss) destroy primary execution nodes. To prevent unmanaged open orders and massive financial loss, an automated multi-region Disaster Recovery (DR) runbook must execute emergency order cancellations, promote secondary database read-replicas (`us-west-2`), reroute DNS traffic, and reconcile positions within a $300$-second Recovery Time Objective ($\text{RTO} \le 300\text{s}$).

## Prerequisites

- Primary and Secondary cloud region names (`primary_region`: `us-east-1`, `secondary_region`: `us-west-2`).
- Target recovery SLA parameters (`max_allowed_rto_sec`: 300.0s, `max_allowed_rpo_sec`: 15.0s).

## Workflow

1. **Outage Signal Verification**:
   - Audit 3 consecutive failed health pings to primary region.
2. **Emergency Open Order Cancellation (Kill Switch)**:
   - Dispatch REST/FIX cancel-all-orders signal to exchange/broker endpoints.
3. **Secondary Database Promotion**:
   - Promote Aurora/TimescaleDB secondary read-replica to primary read-write master.
4. **DNS & BGP Traffic Switchover**:
   - Reroute Route 53 ARC or Cloudflare DNS records to secondary region IP endpoints.
5. **Compute Bootstrap & Position Reconciliation**:
   - Launch trading bots in secondary region, fetch broker positions, reconcile state, and resume trading.
6. **Audit Report Generation**: Output structured `RegionDrFailoverReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Failing to Cancel Open Orders Prior to Switchover**: Rerouting DNS to a secondary region while leaving un-managed limit orders active in the primary region, leading to double-execution position fills.
- **Manual Click-Ops During Outages**: Relying on manual web console clicks during a regional outage, exceeding the $300$-second RTO limit ($> 45\text{ mins}$).
- **Split-Brain Database Promotion**: Promoting secondary database while primary database is still accepting writes during network partition.

## Verification

- Instantiate `RegionDrFailoverExecutorEngine`. Simulate primary region blackout (`us-east-1` down). Execute automated DR failover sequence. Verify engine executes order cancellation, database promotion, DNS switchover, and position reconciliation in $< 300\text{ seconds}$, returning `FAILOVER_SUCCESSFUL`.
- Run `python scripts/test_region_dr_failover_executor.py`.

## Related Skills

- `database-backup-and-point-in-time-restore-testing`
- `cross-region-data-replication-lag-monitoring`
---
