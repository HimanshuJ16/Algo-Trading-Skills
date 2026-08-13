# Blue-Green Cutover Sign-Off Checklist

Deployment ID: ____________  Date/Time (UTC): ____________
Outgoing version: ____________  Incoming version: ____________
Authorized by (named person): ____________

## Pre-deployment (staging)

- [ ] Named authorizer recorded above and passed as `authorised_by`. **EU/UK investment
      firms: this is mandatory (RTS 6 Art. 5(2)), not a formality.**
- [ ] Incoming build passes offline backtest reconciliation against the outgoing build.
- [ ] Broker/venue session concurrency confirmed: two simultaneous instances will not
      invalidate each other's session or order-placing connection.
- [ ] Sufficient hardware capacity confirmed (cores, NUMA placement, memory, feed
      handler capacity) to run both instances without degrading the live one.
- [ ] Risk parameters for the incoming version locked and reviewed.
- [ ] Kill switch tested and confirmed available for this session — it is the fallback if
      rollback is refused.
- [ ] Rollback decision criteria written down *before* cutover (what specifically
      triggers a rollback, and who decides).

## Shadow deployment (green)

- [ ] Green instance running in shadow mode.
- [ ] **Zero outgoing order messages from Green verified at the gateway**, not only in
      application config.
- [ ] Green consuming market data: flowing, current, no unrecovered sequence gaps.
- [ ] JIT warmup and initial signal-generation cycles complete.
- [ ] Green passes real-time risk checks against the *current live* portfolio, not an
      empty book.
- [ ] Ingress-to-signal latency measured against SLA for this build.
- [ ] `deploy_to_inactive()` returned `READY` (not `FAILED`).

## Synchronization and cutover

- [ ] Working-order book reviewed; cutover timed for a quiet moment where practical.
- [ ] Outgoing instance quiesced per the defined procedure before snapshot.
- [ ] State sync transfers positions, working orders (with venue order IDs) and dynamic
      alpha state; receiving instance **acknowledges ingestion**.
- [ ] Green's internalized portfolio validated against Blue's exactly before routing moves.
- [ ] Health check and state sync both bounded by an explicit timeout.
- [ ] `cutover()` returned success. If refused, refusal classified (not `READY` /
      health re-check failed / **state sync failed → redeploy, do not retry cutover**).
- [ ] Exactly one slot returns `True` from `is_authorised_to_route()` after the swap.
- [ ] Positions reconciled against the broker immediately post-cutover.
- [ ] Controlled-deployment limits applied for the observation window (cautious caps on
      instruments, order value/count, positions) and monitoring intensified — EU/UK firms:
      RTS 6 Art. 8.
- [ ] First minutes of Green's order submissions monitored against expected behaviour.

## Post-cutover

- [ ] Blue confirmed `DRAINING` and still viable as a rollback target.
- [ ] Observation window length chosen from when this strategy's failure modes actually
      surface (not a default five minutes), and end-of-window review scheduled.
- [ ] If issues detected: **rollback**. If rollback is refused → kill switch, cancel
      working orders, reconcile manually. Do **not** reach for `force=True` first.
- [ ] Any forced rollback: overridden guard(s) named in the audit record, second person
      informed, incident review opened.
- [ ] Positions reconciled against the broker before ending the observation window.
- [ ] `decommission_standby(authorised_by=...)` executed only after the window closed,
      with the loss of rollback capability explicitly accepted and attributed.
- [ ] `deployment_history` exported and retained: authorizer present on each action,
      refused operations included, forced overrides flagged.
- [ ] Deployment artifacts and performance metrics documented.
