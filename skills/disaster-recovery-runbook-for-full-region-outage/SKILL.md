---
name: disaster-recovery-runbook-for-full-region-outage
description: Gated executor for a cross-region trading-stack failover — sequences outage
  verification, order cancellation, database promotion, DNS switchover and reconciliation,
  and refuses to promote or resume trading until split-brain and open-order interlocks
  are evidenced.
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
- CME Cancel on Disconnect
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a trading stack spans two cloud regions and someone must decide, under time pressure, whether to move it. A full-region event — the primary region unreachable, its execution nodes and writer database gone — puts two things at risk simultaneously: the *data* (unreplicated writes vanish on promotion) and the *book* (orders placed from the dead region can still be resting live at the venue). This module sequences the runbook and enforces the interlocks that keep those two risks from compounding.

It is equally the rehearsal harness: run it with the facts a drill produced and it reports where the sequence would have stopped.

## When NOT to Use

- **As an AWS client.** It calls no APIs and moves no traffic. The operator (or the surrounding automation) performs each action and reports back what happened. It is the decision and audit layer, not the actuator.
- **For single-AZ or single-service impairment.** AWS's own guidance is to "consider the full scope of the outage to make sure cross-Region failover is the proper solution" — most outages are localized. A zonal shift or a service-level retry is the cheaper, safer response; a cross-region promotion is not reversible without data-loss consequences.
- **As a planned migration tool.** A controlled move is a *switchover* — Aurora synchronizes first and RPO is 0. This runbook models the unplanned path, which explicitly accepts data loss.
- **As evidence of regulatory compliance.** The 300s/15s objectives are internal engineering targets. See `references/standards.md` for what is actually mandated and to whom it applies.

## Prerequisites

- Distinct `primary_region` and `secondary_region` (the engine rejects equal values — a target inside the failed region is not a failover).
- Objectives: `rto_sla_sec` (default 300.0) and `max_rpo_sec` (default 15.0), set from your own business impact analysis.
- Replication lag at the moment of promotion — for Aurora Global Database, the `AuroraGlobalDBRPOLag` metric read from the *secondary*.
- The TTL on your failover DNS records; it counts toward the RTO.
- **Break-glass credentials** stored outside the primary region. AWS recommends long-lived IAM credentials created specifically for DR, kept "in an on-premises physical safe or a virtual vault", plus a local copy of your five ARC Regional cluster endpoints — during the event you may not be able to reach the control-plane APIs that would tell you what they are.
- Two facts you must be able to *evidence*, not assume: `primary_write_fenced` and `cancel_all_confirmed`. Both default to `False`.

## Workflow

1. **Verify the outage independently.** Corroborate before acting; a cross-region promotion is expensive to undo.
2. **Dispatch cancel-all — and treat dispatch as distinct from confirmation.** The kill switch is issued from wherever still works, not from the dead region. The step succeeding means the request went out, nothing more.
3. **Do not promote until primary writes are fenced.** Aurora's write fencing is best-effort: "it's possible that writes might be momentarily accepted in the old primary Region, causing split-brain issues." Wait for the fencing event, or take applications offline. Without that evidence the engine blocks promotion and the run ends as `FAILOVER_ABORTED_AT_INTERLOCK` — the correct outcome, because a split brain in a trading database means two truths about your positions.
4. **Decide about data loss explicitly.** If replication lag exceeds the RPO objective, promotion discards those writes. The engine requires `accept_data_loss=True`, mirroring the `--allow-data-loss` flag AWS demands to turn a switchover into a failover. Where there are several secondaries, choose the one with the least lag.
5. **Switch traffic via the routing-control data plane, not the console.** ARC's data plane is "a data plane of endpoints in five AWS Regions"; AWS recommends the `UpdateRoutingControlState` API "instead of changing routing control states in the AWS Management Console", retrying across endpoints. Console click-ops during a regional event is how a 5-minute objective becomes a 45-minute outage.
6. **Count the TTL.** The switchover moves *new* connections when caches expire. The engine adds `dns_ttl_sec` to the RTO and flags a TTL at or above the budget as making the objective unreachable.
7. **Bootstrap and reconcile — then stop.** Reconciling positions is safe. *Resuming trading* is gated on `cancel_all_confirmed`, verified by querying the venue for resting orders rather than inferring it from step 2. Unconfirmed, the run ends `FAILOVER_DEGRADED_TRADING_HALTED`: the secondary is up, the desk stays down, a human decides.
8. **Read `outcome`, not the boolean.** `is_failover_successful` cannot distinguish "trading resumed" from "secondary up, desk halted" — and it is `False` for a run that completed every step but overran the RTO. Use `is_trading_resumed` to answer "is the desk live?"; use `outcome` to decide what to do next.

> Full procedure: see `references/workflows.md`.
> Standards and sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Assuming the cancel-all worked because it was sent.** The region that placed the orders is the one that just died. Confirm flatness against the venue before trading again.
- **Trusting Cancel on Disconnect to clean up.** CME's COD "does not include GTC (Good Till Cancel) and GTD (Good Till Date) orders" and "is not invoked for a graceful disconnect" — so an orderly shutdown of the primary's sessions cancels nothing, and long-dated orders survive regardless. Enumerate what your venues actually cancel, and when.
- **Treating the DNS switchover as a drain.** "Clients with pre-existing open connections might continue to make requests against the impaired location until the clients reconnect" — the ALB HTTP client keepalive default is 3600 seconds. New connections move; established ones do not.
- **Budgeting RTO from step durations alone.** With a 300s objective and a 300s TTL the objective is arithmetically unreachable. AWS suggests 60–120s TTLs for failover records; set them *before* the incident, since changing them during one requires the control plane you may have lost.
- **Promoting on the hope that fencing worked.** Fencing is best-effort and can time out; AWS emits a distinct event for each case. Check which one you got.
- **Setting an RPO target you cannot enforce.** Aurora PostgreSQL's `rds.global_db_rpo` accepts values "from 20 seconds" upward, so a 15-second objective cannot be enforced by that parameter — it can only be monitored. AWS also recommends leaving it unset in a two-region global database, because enforcing it can pause transactions on the primary.
- **Failing over into the failed region.** A copy-paste of the same region name into both fields produces a confident report about a move that never happened. The engine rejects it at construction.
- **Console click-ops during a regional event.** Use the ARC data plane API with retries across all five endpoints, with credentials you can reach without the primary region.

## Verification

- Instantiate `RegionDrFailoverExecutorEngine` and run the clean path with both interlocks evidenced; expect `outcome == "FAILOVER_SUCCESSFUL"`, six successful steps, and an RTO that includes the DNS TTL.
- Split-brain regression: run with `primary_write_fenced=False` and confirm promotion is *blocked* (`is_blocked=True`, zero elapsed), everything downstream is blocked, and the outcome is `FAILOVER_ABORTED_AT_INTERLOCK`.
- Open-order regression: run with `cancel_all_confirmed=False` and confirm reconciliation succeeds while `RESUME_TRADING` is blocked and the outcome is `FAILOVER_DEGRADED_TRADING_HALTED`.
- RPO regression: 900s of replication lag must block promotion unless `accept_data_loss=True`, in which case the outcome is `FAILOVER_SUCCESSFUL_WITH_ACCEPTED_DATA_LOSS` and `is_rpo_compliant` stays `False`.
- RTO regression: a 300s TTL against a 300s objective must breach the RTO even though the steps take 110s.
- Run `python -m unittest discover -s skills/disaster-recovery-runbook-for-full-region-outage/scripts`.

## Related Skills

- `database-backup-and-point-in-time-restore-testing`
- `cross-region-data-replication-lag-monitoring`
- `kill-switch-and-drawdown-circuit-breakers`
- `multi-region-failover-for-broker-connectivity`
- `graceful-degradation-priority-during-partial-outage`
