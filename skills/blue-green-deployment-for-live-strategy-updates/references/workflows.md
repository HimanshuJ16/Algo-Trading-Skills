# Blue-Green Deployment Workflows for Live Trading

The deployer in `scripts/blue_green_deployer.py` owns exactly one thing: **which slot is
authorized to route orders**. It does not start processes, move positions, or cancel
orders. Everything below describes the surrounding procedure that the two callbacks —
`health_check_fn` and `state_sync_fn` — must actually implement.

## 0. Slot lifecycle

```
IDLE ──deploy──> DEPLOYING ──health ok──> READY ──cutover──> LIVE
  ^                   │                     │                 │
  │                   └──health fail──> FAILED <──sync fail────┘
  │                                       │              (quarantine)
  └────decommission──── DRAINING <────cutover (outgoing slot)
```

- `DRAINING` is the last-known-good rollback target. `deploy_to_inactive()` refuses to
  overwrite it; releasing it requires an explicit `decommission_standby()`.
- `FAILED` is redeployable but is **never** a rollback target.
- `READY` is also refused for redeployment, so an un-cut-over deployment is not silently
  replaced by another.

## 1. Strategy version staging (green deployment)

- Instantiate the new version in an isolated memory space, on isolated cores (see
  `references/standards.md` §6).
- GREEN subscribes to live market data and runs in **shadow mode**: it computes signals
  and maintains its own view, but emits no outgoing FIX/binary order messages. Verify the
  absence of outgoing order messages at the gateway, not just in application config — a
  shadow instance that is actually wired to the venue is the worst possible outcome.
- Warm JIT caches, instantiate risk models, and replay whatever history the strategy
  needs to have a meaningful state.
- Call `deploy_to_inactive(version, authorised_by=...)`. Warmup and the health check
  deliberately run **outside** the deployer's lock, so an emergency rollback of the
  currently live slot is never queued behind a slow warmup.

## 2. Validation and health checks

`health_check_fn(color)` must answer "is this instance fit to trade *right now*", and it
is called twice: once after warmup, and again inside the cutover critical section. It
should verify at minimum:

- **Market data**: subscribed, flowing, no unrecovered sequence gaps, timestamps current.
- **Risk**: in-memory risk constraints loaded and evaluated against the *current* live
  portfolio state, not an empty book.
- **Latency**: market-data-ingress-to-signal latency measured against whatever SLA the
  strategy is held to. Measure it; do not assume the new build kept the old build's
  profile.
- **Dependencies**: order gateway session established, reference data loaded, clock in
  sync.

An exception raised inside the callback is treated as a failed check, never as a pass.
The slot lands in `FAILED`, which is redeployable — a raising health check does not strand
the pipeline.

## 3. State synchronization (the critical path)

`state_sync_fn(source, target)` must transfer, and the receiving instance must
acknowledge:

- **Positions** per instrument, with the source's own view of average cost.
- **Open/working orders**, including venue order IDs, so the incoming instance can manage
  and cancel orders it did not place.
- **Dynamic alpha/working state** — rolling features, inventory targets, schedule
  progress for a parent order — anything whose loss would make the new instance restart a
  execution schedule from scratch.

Practical requirements:

- Quiesce briefly and deliberately: the outgoing instance should stop *originating* new
  orders and take a consistent snapshot, rather than snapshotting a book that is moving
  underneath it. Define what "quiesce" means concretely in your system (typically: stop
  new order origination, let in-flight acknowledgements settle to a known state, then
  snapshot) and bound how long it may last.
- Transfer over shared memory or fast IPC as appropriate, but treat **acknowledged
  ingestion** — not transmission — as success.
- Return `False` on partial application. Do not report success on a half-transferred book;
  the deployer's guarantee is only as good as this return value.
- The sync runs **inside** the deployer's lock, so it also blocks
  `is_authorised_to_route()` and therefore order submission. That is intended — no order
  should be sent while the book is mid-transfer — but it means an unbounded sync stalls
  trading. Bound it with your own timeout.

## 4. Atomic cutover

`cutover(authorised_by=...)` performs, in one critical section:

1. Confirm the standby is `READY`.
2. **Re-run the health check.** The `READY` stamp may be minutes old.
3. Run `state_sync_fn(live, standby)`.
4. Swap the routing pointer; standby becomes `LIVE`, the outgoing slot becomes `DRAINING`.

Because all four happen under one lock, there is no interval in which both slots or
neither slot is authorized.

**If it is refused, classify before acting:**

| Refusal | Meaning | Correct next step |
|---|---|---|
| Slot is not `READY` | Nothing staged, or a previous attempt quarantined it | Deploy the version again |
| Pre-cutover health re-check failed | The staged instance degraded while parked | Investigate the instance; the slot is now `FAILED` and redeployable |
| State sync failed | The transfer may be **partially applied** | Do **not** retry the cutover. The slot is quarantined as `FAILED` by design; redeploy cleanly |

After a successful cutover, apply RTS 6 Art. 8-style controlled-deployment limits if in
scope: cautious caps on instruments, order value/count and positions, plus heightened
monitoring for the observation window. The pointer swap is not the whole obligation.

## 5. Rollback

`rollback(authorised_by=...)` is **not** the inverse pointer swap of a cutover.

1. Confirm the target is a viable slot (`DRAINING` or `READY`). `IDLE` (never deployed or
   decommissioned) and `FAILED` (rejected) are refused.
2. Run `state_sync_fn(live, target)` — **backwards**, from the currently live instance to
   the rollback target, because the target's book is stale by the length of the live
   window.
3. Only then move routing. The outgoing (bad) slot becomes `FAILED`, so a second rollback
   cannot ping-pong routing back into the version just rejected.

**If rollback is refused, escalate to the kill switch, not to `force=True`.** Cancel
working orders, stop submission, reconcile against the broker by hand. `force=True` is
reserved for the case where a human has *already* confirmed the target's state by other
means; whichever guards it overrides are named in the audit record and logged at CRITICAL.

Rollback restores *routing*, not *executions*. Fills the bad version already booked at the
venue are real, and reconciling them is the state sync's job.

## 6. Drain and decommission

- BLUE remains `DRAINING` through the observation window: no signal generation, still
  holding last-known-good state.
- Choose the window length from when your failure modes actually surface — a bad fill
  model or a close-specific signal bug will not appear in the first five minutes — and
  reconcile positions against the broker before ending it.
- `decommission_standby(authorised_by=...)` is the deliberate end of that window. After it
  returns there is no rollback target until a new version has been deployed and cut over.
  It is refused for a `LIVE` slot, and it records who accepted the loss of rollback
  capability.
