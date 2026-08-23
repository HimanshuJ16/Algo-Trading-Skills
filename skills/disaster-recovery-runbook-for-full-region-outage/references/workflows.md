# Workflows for Disaster Recovery Runbook for Full Region Outage

## A. Before the incident (the part that decides the outcome)

1. Set failover-record TTLs to 60–120s and lower client keepalive durations.
   Both are control-plane changes; you cannot make them during the event they
   are meant to survive.
2. Store break-glass IAM credentials and the five ARC cluster endpoints outside
   the primary region, reachable without it.
3. Enumerate, per venue, exactly what is cancelled on disconnect and what is
   not — GTC/GTD orders and graceful logouts are the usual exclusions — and
   write down how you will *query* resting orders, not just cancel them.
4. Alarm on `AuroraGlobalDBRPOLag` from the secondary, so the failover decision
   starts with a known lag rather than a guess.

## B. The failover decision

| Question | If yes | If no |
|---|---|---|
| Is the whole region actually affected? | Continue | Prefer zonal shift / service retry; cross-region promotion is not reversible cheaply |
| Are primary writes fenced (event observed, or apps offline)? | Promote | **Stop.** Fence first; promoting risks split-brain |
| Is replication lag inside the RPO objective? | Promote | Promote only as an explicit `accept_data_loss` decision, recorded |
| Are resting orders confirmed cancelled venue-side? | Resume trading | **Stop at halted.** Reconcile against the venue first |

## C. Execution order (enforced by the engine)

1. `OUTAGE_VERIFICATION` — corroborate independently.
2. `CANCEL_OPEN_ORDERS` — dispatch the kill switch from a surviving location.
3. `PROMOTE_SECONDARY_DB` — gated on fencing and on the data-loss decision.
4. `DNS_SWITCHOVER` — routing-control data plane API, retried across endpoints.
5. `COMPUTE_BOOTSTRAP_RECONCILE` — bootstrap nodes, reconcile positions.
6. `RESUME_TRADING` — gated on confirmed cancellation.

A blocked step is not a failed step: nothing was attempted, and everything
downstream stays blocked. That distinction is what makes the report usable in a
post-incident review.

## D. Interpreting the outcome

| Outcome | Meaning | First action |
|---|---|---|
| `FAILOVER_SUCCESSFUL` | Full sequence completed inside both objectives | Monitor; plan the switchover back |
| `FAILOVER_SUCCESSFUL_WITH_ACCEPTED_DATA_LOSS` | Completed, but promotion discarded writes beyond the RPO | Recover unreplicated data from the failure-point snapshot; reconcile fills with brokers |
| `FAILOVER_DEGRADED_TRADING_HALTED` | Secondary up and reconciled; trading deliberately not resumed | Query venues for resting orders; resume only once flat |
| `FAILOVER_ABORTED_AT_INTERLOCK` | Stopped before promotion | Fence writes, or make the data-loss decision explicitly |
| `FAILOVER_FAILED` | A step was attempted and failed, or the RTO was breached | Triage the failed step; the desk stays down |

## E. After the event

1. Recover unreplicated writes from the snapshot AWS takes of the old primary
   volume at the point of failure, and reconcile against broker records.
2. Fail back with a *switchover* (RPO 0), not another failover, once the
   original region is healthy and rebuilt as a secondary.
3. Compare the drill's predicted RTO against the incident's actual: the gap is
   almost always DNS TTL, connection draining, or a manual step nobody timed.
