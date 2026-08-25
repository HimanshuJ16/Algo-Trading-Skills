# Workflows — execution-algorithm-kill-switch-integration

## 0. Wiring

```
risk feed ──► RiskSnapshot ──┬──► evaluate_risk_state()      (timer, always running)
                             └──► audit_and_validate_new_order()  (order path)
                                        │
                                        ▼
                          ExecutionAlgoKillSwitchEngine
                                        │  1. latch lockout (local, cannot fail)
                                        │  2. dispatch cancels (network, can fail)
                                        ▼
                   KillSwitchGateway per venue ──► FIX OrderMassCancelRequest (q)
                                        ▲
             ExecutionReport feed ──────┘ apply_execution_report()  (the only confirmation)
```

## 1. Continuous risk evaluation

Run **both** paths. The order path alone cannot fire once the algorithm stops
submitting — the exact behaviour of an algorithm stuck holding a losing
position.

- `audit_and_validate_new_order(req, snapshot)` — per order.
- `evaluate_risk_state(snapshot)` — on a timer, typically 100 ms–1 s.

Evaluation order on the order path is deliberate: **latch state → risk-data
usability → staleness → limits.** An order is rejected by an engaged latch
without ever consulting a risk feed that may itself be broken.

Every order-entry attempt is counted toward the rate window *before* any
decision, so a rejection loop still trips the runaway limit.

## 2. Limit evaluation

| Limit | Comparison | Notes |
|---|---|---|
| Max daily loss | `-daily_pnl_usd >= max_daily_loss_usd` | `daily_pnl_usd` is **signed**; negative is a loss. Realized + unrealized. |
| Max net exposure | `abs(net_exposure_usd) >= max_net_exposure_usd` | Signed input, absolute comparison — a runaway short breaches identically. |
| Runaway order rate | `rate >= max_order_rate_per_sec` | `order_rate_per_sec=None` makes the engine use its own 1-second sliding window of attempts it has seen. Prefer this: a looping algorithm cannot under-report a rate the gate measures itself. |

Breach is `>=`. A loss that reaches the limit has breached it.

**Unusable data fails closed for the order, not firm-wide.** A NaN or infinite
PnL is rejected as `REJECTED_RISK_DATA_INVALID`; a snapshot older than
`max_snapshot_age_seconds` (or far enough ahead of the engine's clock to be
skew) is rejected with the same status and a `RISK_DATA_STALE` reason code.
Neither engages a firm-wide kill: a broken telemetry feed proves the limit
cannot be checked, not
that the firm is losing money. Alert on these — a persistent stream of them is a
data-feed outage silently halting trading.

## 3. Engaging the kill switch

`trigger_kill_switch(scope, reason, strategy_id=None, triggered_by=...)`

1. **Latch first.** `GLOBAL` sets the global block; `STRATEGY` blocks that
   strategy. This is local, synchronous, and cannot fail.
2. **Select scope.** In-scope orders are those in `NEW`, `PARTIALLY_FILLED` *or*
   `PENDING_CANCEL` — an unconfirmed cancel has killed nothing, so re-firing the
   switch re-requests it. Mass cancel is idempotent.
3. **Dispatch.**

| Scope | Mechanism | `fix_mass_cancel_tag_530` |
|---|---|---|
| `GLOBAL` | `OrderMassCancelRequest` with `MassCancelRequestType=7` to **every configured gateway**, including venues with no orders in the local map | `"7"` |
| `STRATEGY` | One `OrderCancelRequest` per order, grouped by venue | `None` |

`STRATEGY` deliberately does not use tag 530: its narrowest scope (value 1) is a
*security*, which cancels other strategies in that symbol and misses the target
strategy elsewhere. If a venue supports the FIX 5.0 SP2 `TargetParties`
component, implement the party-scoped mass cancel inside the gateway adapter.

4. **Record outcomes per venue.**

| Dispatch status | Meaning | Order state | Escalates |
|---|---|---|---|
| `ACCEPTED` | Venue acknowledged the request | `PENDING_CANCEL` | no |
| `REJECTED` | Tag 531=0 / tag 532 reason, or individual cancels refused | unchanged — still live | yes |
| `ERROR` | Gateway raised (transport failure) | unchanged — still live | yes |
| `NO_GATEWAY` | No adapter configured for that venue, or none at all | unchanged — still live | yes |

A gateway exception is contained per venue: one dead FIX session must never
prevent the others being cancelled.

## 4. Reading the report

Escalate on `uncancelled_order_ids` and `manual_intervention_required`.
`cancel_requested_count` is a dispatch statistic, not a safety signal.

| Field | Use |
|---|---|
| `is_new_order_blocked` | The lockout — true even when every cancel failed |
| `cancel_requested_count` | Orders a cancel was *sent* for |
| `pending_cancel_order_ids` | Requested, not yet confirmed |
| `uncancelled_order_ids` | **No cancel accepted — these may still trade** |
| `manual_intervention_required` | A human must reconcile the venue book |
| `dispatches` | Per-venue detail including FIX tag 532 reject reason |
| `event_id`, `timestamp_utc`, `triggered_by`, `scope_target` | Audit record (RTS 6 Art. 12(3); 15c3-5(b)) |

## 5. Confirmation

`apply_execution_report(cl_ord_id, order_status, filled_qty=None)` is the only
thing that moves an order out of `PENDING_CANCEL`.

- `CANCELLED` — confirmed dead.
- `FILLED` while `PENDING_CANCEL` — it traded in the race between the cancel
  request and the matching engine. Logged at WARNING: post-kill exposure
  changed, and the flattening decision must be revisited.

## 6. Manual reset

`reset(scope, authorized_by=..., reason=..., acknowledge_unconfirmed=False)`

Refuses while any cancel is unconfirmed or any dispatch failed. Override only
after a human has reconciled the venue book, by passing
`acknowledge_unconfirmed=True` — which is recorded in the audit note along with
the identity and reason.

Then, in order:

1. Reconcile every venue's open-order book against the local map.
2. Confirm the *cause* is fixed — a timer does not fix a bug or a market state.
3. Lift the **venue-side** kill switch where one fired (Nasdaq Rule 6130
   requires asking exchange operations to reactivate the MPID's ports).
4. Only then reset this engine, and watch the first orders through.

## 7. Fire drills

Rehearse each of these against a sandbox, at least at every release that touches
the order path:

- Venue rejects the mass cancel (tag 532=0) — does the desk get paged?
- Gateway raises mid-dispatch — do the other venues still cancel?
- An order sits at a venue with no configured gateway — is it in
  `uncancelled_order_ids`?
- A fill lands after the cancel was accepted — is the position re-checked?
- Process restart while engaged — does the system come back locked, or open?
