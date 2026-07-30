---
name: exchange-gateway-redundancy-and-failover-testing
description: >-
  Quantitative exchange connectivity and resilience engine for orchestrating Active-Standby FIX/ETI gateway failover, sequence number resynchronization, and in-flight order audit tests.
domain: Venue Integration & Protocols
subdomain: Exchange Connectivity & High Availability
tags: ["gateway-redundancy", "fix-failover", "active-standby", "sequence-resync", "poss-dup-flag", "rto-sla", "high-availability"]
brokers_frameworks: ["FIX 4.2 / 5.0 SP2", "Eurex T7 ETI", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in high-frequency trading infrastructure, broker order routing engines, and exchange connectivity failover testing harnesses. Exchange order gateways (FIX / ETI sessions) must maintain **Active-Standby** or **Active-Active** redundancy. When the primary connection experiences a network partition, heartbeat timeout ($> 3000\text{ms}$), or severe latency degradation ($> 100\text{ms}$), the engine must trigger an immediate failover to the secondary gateway within strict RTO SLAs ($< 100\text{ms}$), resynchronize sequence numbers (`MsgSeqNum`), and flag in-flight orders with `PossDupFlag` (`Tag 43 = Y`) to prevent duplicate executions.

## Prerequisites

- Primary and Secondary Gateway endpoints (`gateway_id`, `ip_address`, `port`, `last_sent_seq_num`).
- Health monitoring metrics (`heartbeat_delay_ms`, `latency_rtt_ms`, `tcp_connected`: True/False).
- Active in-flight order list (`cl_ord_id`, `order_status`: `'PENDING_NEW'`, `'FILLED'`).

## Workflow

1. **Gateway Health & Liveness Audit**:
   - Monitor heartbeat interval, latency RTT, and TCP socket connection.
   - If `heartbeat_delay_ms > 3000` or `tcp_connected` is False $\implies$ Trigger Failover.
2. **Failover Execution & Role Switch**:
   - Demote Primary Gateway to `DISCONNECTED` or `DEGRADED`.
   - Promote Secondary Gateway from `STANDBY` to `ACTIVE`.
3. **Sequence Number & In-Flight Order Resynchronization**:
   - Synchronize `MsgSeqNum` to match current session log.
   - Re-send pending in-flight orders (`PENDING_NEW`) marked with `PossDupFlag = Y` (FIX Tag 43).
4. **Audit Report Generation**: Output structured `GatewayFailoverAuditReport`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Duplicate Order Placements During Failover**: Retransmitting un-confirmed in-flight orders over secondary gateway without setting `PossDupFlag = Y`, causing double execution on the exchange.
- **Split-Brain Execution**: Allowing both Primary and Secondary gateways to send orders simultaneously during network partitions.
- **FIX Sequence Number Desynchronization**: Failing to sync `MsgSeqNum` prior to sending logon messages on secondary gateway, triggering instant counterparty logout.

## Verification

- Instantiate `ExchangeGatewayRedundancyEngine`. Setup Primary Gateway (`GW_PRIMARY`, seq=150, ACTIVE) and Secondary Gateway (`GW_SECONDARY`, seq=150, STANDBY). Simulate Primary TCP disconnect and 4500ms heartbeat delay. Execute failover. Verify engine demotes Primary, promotes Secondary to ACTIVE, marks pending in-flight orders with `PossDupFlag = Y`, and reports recovery time ($RTO < 50\text{ms}$).
- Run `python scripts/test_exchange_gateway_redundancy_and_failover_testing.py`.

## Related Skills

- `smart-order-router-failover-on-venue-outage`
- `disaster-recovery-runbook-for-full-region-outage`
---
