# Pre-Flight / Sign-off Checklist — execution-algorithm-kill-switch-integration

## Limits and calibration
- [ ] `max_daily_loss_usd`, `max_order_rate_per_sec` and `max_net_exposure_usd` are set, and the calibration rationale is recorded (RTS 6 Art. 15(1) mandates the controls, not the numbers; 15c3-5(c)(1)(i) requires thresholds to be "appropriate" and pre-set).
- [ ] No runbook, dashboard or doc claims a regulator-mandated kill-switch latency — no regulator publishes one.
- [ ] `max_snapshot_age_seconds` is calibrated to the real risk-feed cadence, or the gate is disabled deliberately and that decision is recorded.

## Risk data
- [ ] `daily_pnl_usd` is wired **signed** — a loss is negative. Verified end-to-end with a live-shaped negative value, not just a unit test.
- [ ] `net_exposure_usd` is signed; the limit is enforced on its absolute value.
- [ ] A NaN or infinite PnL rejects the order (`REJECTED_RISK_DATA_INVALID`) instead of passing every comparison silently.
- [ ] Stale-snapshot and invalid-data rejections are alerted on — a stream of them is a data outage quietly halting trading.

## Trigger paths
- [ ] `evaluate_risk_state()` runs on a timer, not only on the order path — otherwise the switch cannot fire once the algorithm stops submitting.
- [ ] Order-entry attempts are counted before the decision, so a rejection loop still trips the rate limit.
- [ ] Threshold behaviour at exactly the limit is tested and matches policy (this engine breaches at `>=`).
- [ ] An unknown scope, or `STRATEGY` with no `strategy_id`, raises — it never returns a `NORMAL_OPERATIONS` report.

## Cancellation
- [ ] Order entry is latched **before** any cancel is dispatched.
- [ ] A gateway is configured for **every** venue the firm can have orders at; orders at an unconfigured venue appear in `uncancelled_order_ids`.
- [ ] GLOBAL scope fans out to every configured gateway, not only venues with orders in the local map.
- [ ] `MassCancelRequestType` (tag 530) = 7 is used for firm-wide cancellation; FIX tag 530 is **not** used to express a strategy scope (value 1 is "orders for a security").
- [ ] Per-venue mass cancel rejection (tag 531=0 / tag 532, including 0 = "Mass Cancel Not Supported") is handled, surfaced and escalated.
- [ ] A gateway exception is contained: one dead FIX session does not stop the other venues.
- [ ] Escalation and dashboards key off `uncancelled_order_ids` and `manual_intervention_required` — never off `cancel_requested_count`.

## Order state
- [ ] Orders move `NEW/PARTIALLY_FILLED → PENDING_CANCEL → CANCELLED/FILLED`; nothing is marked cancelled on dispatch.
- [ ] The `ExecutionReport` feed calls `apply_execution_report()`; without it nothing ever confirms.
- [ ] A fill arriving for a `PENDING_CANCEL` order raises a warning and triggers a position re-check.
- [ ] Partially filled orders are chased by the kill switch, and residual quantity is tracked.

## Reset
- [ ] Nothing re-arms automatically — no cool-down timer, no restart-clears-latch.
- [ ] `reset()` refuses while cancels are unconfirmed; `acknowledge_unconfirmed=True` is used only after a human reconciled the venue book.
- [ ] `authorized_by` and `reason` are captured for every reset and retained (15c3-5(b), (e)).
- [ ] The runbook states that resetting this engine does **not** lift a venue-side kill switch (e.g. Nasdaq Rule 6130 requires exchange operations to reactivate the MPID's ports).

## Defence in depth
- [ ] A venue-side kill switch is provisioned and tested (Nasdaq Rule 6130 / BX 4764 / PSX 3316, CME Globex Kill Switch, or venue equivalent).
- [ ] Cancel-on-disconnect is enabled where offered — it covers the case where your own process is what failed.
- [ ] Human authorisation for manual triggers is enforced upstream (`emergency-manual-override-access-control`).
- [ ] Whether to flatten positions after the kill is a separate, documented decision — cancelling orders does not reduce the position.

## Durability and concurrency
- [ ] Kill latches, the order map and the audit trail are persisted, or the system fails closed on start-up.
- [ ] The engine's lock is not bypassed by a caller doing its own check-then-act around it.
- [ ] Audit records (`event_id`, `timestamp_utc`, `triggered_by`, `scope_target`) are shipped to an append-only sink.

## Testing
- [ ] Run `python -m unittest discover -s skills/execution-algorithm-kill-switch-integration/scripts` — 79 tests, 100% pass rate.
- [ ] Fire drills rehearsed in a sandbox: venue rejects the cancel · gateway throws · order at an unconfigured venue · fill lands after cancel accepted · process restart while engaged.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
