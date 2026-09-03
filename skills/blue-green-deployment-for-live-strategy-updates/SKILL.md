---
name: blue-green-deployment-for-live-strategy-updates
description: >-
  Use when a strategy that is currently routing orders and holding positions must be
  replaced without waiting for a maintenance window; stages the new build on a standby
  slot, health-checks it, syncs position and open-order state, then cuts routing
  authority over.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: deployment-ops
  tags: deployment-ops, blue-green, zero-downtime-cutover, rollback, state-synchronization
  brokers_frameworks: ""
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a strategy that is **currently routing live orders and holding
positions** must be replaced with a new build, and stopping it until the next maintenance
window is not acceptable. Two instances (BLUE and GREEN) run side by side; exactly one of
them holds routing authority at any instant, and the handover carries the book with it.

Use it when: the strategy holds overnight or intraday inventory that cannot be flattened
for the deploy; market coverage gaps cost money (market making, hedging, continuous
execution); or you need a rehearsed, sub-minute path back to the previous version.

## When NOT to Use

Do **not** use it when:

- The book is flat and the venue is closed. A plain restart is simpler, and simpler is
  safer — blue-green exists to solve a problem you do not have in that window.
- The change is configuration or a risk-parameter update that your system can reload
  without a new process. Do not spin up a second instance to change a number.
- You cannot actually afford two live instances. Two copies mean double the market-data
  subscriptions, double the hardware, and — during the drain window — double the licence
  and colocation footprint.
- Your broker or venue session does not permit it. Many retail broker APIs authenticate
  one session per account, or allow only one order-placing connection at a time; verify
  your broker's concurrency rules before assuming two instances can coexist, because a
  second login that silently invalidates the first one turns a zero-downtime deploy into
  an outage with an open position.
- What you actually need is to *stop*. If the running strategy is misbehaving right now,
  the primitive is the kill switch (`kill-switch-and-drawdown-circuit-breakers`), not a
  deployment.

## Prerequisites

- Two isolated strategy instances, both able to consume market data independently, with
  resource isolation so GREEN's startup does not degrade BLUE's live trading.
- A **health check that proves fitness to trade**, not just that a process is running:
  market data flowing and current, order gateway reachable, risk limits loaded, signal
  output within sane bounds.
- A **state synchronization routine** that transfers positions, open/working orders and
  live alpha state from the outgoing instance to the incoming one, and reports failure
  honestly rather than partially succeeding in silence.
- Idempotent client order IDs (`order-placement-idempotency`). A cutover is exactly the
  moment two instances can both believe they own an in-flight order.
- A working kill switch (`kill-switch-and-drawdown-circuit-breakers`). It is the correct
  fallback whenever rollback is refused, and this skill's workflow depends on it existing.
- An execution layer that gates **every** submission on the routing pointer, by calling
  `BlueGreenDeployer.is_authorised_to_route(color)` — not a cached boolean, not a direct
  read of the `slots` dict.
- A named person authorizing the deployment. For EU investment firms this is mandatory,
  not good practice — see `references/standards.md`.

## Workflow

1. **Record the baseline.** Construct the deployer with `initial_version` set to the
   version already live. A history that starts at the first deployment cannot say what
   was running before it, which makes the pre-existing version unidentifiable in a
   post-incident review.
2. **Stage the new version on the standby slot** with `deploy_to_inactive(version,
   authorised_by=...)`. It refuses to write over a `DRAINING` slot — that slot is the
   last-known-good rollback target, and overwriting it is how teams discover mid-incident
   that they have nothing to roll back to.
3. **Shadow-run and health-check.** GREEN consumes live market data and generates signals
   but emits no orders. Warmup (JIT, risk-model compilation, cache fill) runs outside the
   deployer's lock so an emergency rollback of the live slot is never blocked behind it.
   A failed health check is a normal outcome: the slot lands in `FAILED`, which is
   redeployable, and the pipeline is not stuck.
4. **Cut over** with `cutover(authorised_by=...)`. Health is re-checked *at the instant of
   the swap*, then state is synchronized, then the pointer moves — all in one critical
   section, so there is no window where both slots or neither slot is authorized.
5. **If cutover is refused, classify before retrying.** A failed pre-cutover health check
   means the staged instance degraded while parked; investigate the instance. A failed
   *state sync* is different: the sync may have partially applied, so the slot is
   quarantined as `FAILED` deliberately. Do not retry the cutover onto a possibly torn
   book — redeploy the version cleanly instead.
6. **Observe GREEN under real flow** before touching anything else. BLUE stays `DRAINING`:
   no signal generation, but still holding last-known-good state as the rollback target.
7. **If GREEN misbehaves, roll back** with `rollback(authorised_by=...)`. This is not a
   pointer swap: BLUE's book is stale by however long GREEN was live, so state is
   reconciled *backwards* from GREEN to BLUE first, and the rollback is refused if that
   reconciliation fails or if BLUE is not a viable target.
8. **If rollback is refused, use the kill switch — do not reach for `force=True`.**
   Refusal means the target's book cannot be trusted; routing live flow to a strategy
   with a wrong position does more damage than staying put. Halt, cancel working orders,
   reconcile against the broker by hand, and only then consider a forced rollback. Every
   guard a forced rollback overrides is named in the audit record.
9. **Decommission only after a full observation window** with `decommission_standby(
   authorised_by=...)`. This is the deliberate end of the drain window: after it returns
   there is no rollback target until a new version has been deployed and cut over. Weigh
   it against reclaiming the hardware, and record who accepted that trade.

> Full step-by-step procedure, including the shadow-mode and state-transfer mechanics:
> see `references/workflows.md`.
> Regulatory touchpoints and engineering standards: see `references/standards.md`.
> Printable cutover sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating rollback as a bare pointer swap.** Once GREEN has traded, BLUE's positions
  and open orders are stale. Swapping back without reconciling hands the market to a
  strategy that believes it holds a position it no longer has — and it will "correct"
  that phantom position with real orders.
- **Assuming a hot standby has a current book because it consumes market data.** Market
  data does not carry the live instance's fills. A standby that is not also consuming the
  execution/drop-copy stream has a stale position the moment the live instance trades.
- **Trusting a `READY` stamp from minutes ago.** Between staging and cutover the staged
  instance can lose its market data feed, drift out of risk limits, or die outright.
  Re-check health inside the same critical section as the swap.
- **Retrying a cutover after a failed state sync.** The failure may have left the target
  with a partially applied book; retrying cuts over onto it.
- **Deploying over the `DRAINING` slot to "save a step".** That slot is the rollback
  target. Releasing it must be an explicit, attributable act.
- **Decommissioning as soon as the cutover succeeds.** The failure modes that matter —
  a bad fill model, a signal that only misbehaves at the close, a slow memory leak —
  show up later in the session, after you have thrown away the way back.
- **Cutting over with a large working-order book.** Orders BLUE placed are live at the
  venue; if the sync does not hand them over precisely, GREEN will not manage them and
  they become orphans nobody cancels. Prefer a quiet moment with few resting orders.
- **Letting the execution layer cache "am I live?".** Both instances then believe they
  are authorized across the swap and duplicate submissions. Gate every submission on
  `is_authorised_to_route()`.
- **Unbounded health or sync callbacks.** `cutover()` and `rollback()` hold the lock
  across those callbacks so the swap is atomic — which means a hung callback stalls
  order submission itself, not just deployment. Bound both with your own timeout.
- **Using `force=True` routinely.** It exists for the case where a human has already
  reconciled the book by other means. Habitual use converts every guard in this skill
  into decoration.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/blue-green-deployment-for-live-strategy-updates/scripts`
- Assert the safety invariant continuously in staging: at every instant, exactly one slot
  returns `True` from `is_authorised_to_route()` — never zero, never two — including
  while a cutover is in flight.
- Run fault-injection drills against a paper/simulated venue, not just the happy path:
  kill the staged instance between `READY` and cutover (cutover must abort); fail the
  state sync (slot must quarantine, routing must not move); fail the backward sync during
  rollback (rollback must be refused and point you at the kill switch).
- Reconcile positions against the broker immediately after every cutover and rollback.
  The deployer moves routing; it cannot confirm the venue agrees with your book.
- Export `deployment_history` after each drill and confirm each entry carries an
  authorizer, that refused operations are present (not just successful ones), and that
  any forced rollback is flagged `forced` with the overridden guard named.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `paper-to-live-promotion-checklist`
- `multi-region-failover-for-broker-connectivity`
- `structured-logging-for-post-incident-forensics`
- `mifid-ii-algo-trading-compliance-eu`
