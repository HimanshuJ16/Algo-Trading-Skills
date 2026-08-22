# Workflows — capital-preservation-mode-for-degraded-conditions

## 1. Placement in the order path

Deploy `CapitalPreservationEngine` as middleware in the OMS, on the single path every
order submission takes. The requirement is structural, not stylistic: if any code path
can reach the gateway without calling `check_order_allowed()`, the control does not
exist for that path.

```
strategy -> [ CapitalPreservationEngine.check_order_allowed() ] -> gateway -> venue
                          |
                          +-- on_halt --> cancel-all / flatten / page
```

The engine is thread-safe within a process and holds state in memory. Two routing
processes each holding their own engine enforce two independent budgets, not one shared
budget — run a single gateway process or move the counters to shared storage.

## 2. Provisioning the reset secret

```bash
export CAPITAL_PRESERVATION_RESET_TOKEN="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')"
```

Provision it through your secrets manager (see `centralized-secrets-management-vault-integration`),
not in the repository, the image, or the process's command line. If the variable is
unset the engine refuses every reset — deliberately, so a missing secret surfaces as an
unresettable halt rather than a resettable-by-anyone one.

For tests and staging, inject the authorizer directly rather than mutating the
environment:

```python
engine = CapitalPreservationEngine(limits, authorizer=ResetAuthorizer(expected_token="staging-token"))
```

## 3. Threshold calibration

| Parameter | Starting point | Notes |
|---|---|---|
| `max_daily_drawdown_usd` | The desk's absolute peak-to-trough tolerance for one session. | Measured from a high-water mark seeded at flat, so it also bounds a straight-line loss from open. |
| `max_orders_per_minute` | ~2x observed peak legitimate rate. | Must also be *below* the venue's own message limit, or the venue throttles or bans you before this control fires. |
| `max_consecutive_errors` | 5, adjusted for your venue's normal reject rate. | No time decay: cleared only by `register_success()`. On a quiet desk, errors hours apart accumulate. |
| `max_daily_loss_usd` | Set it. | A drawdown limit alone cannot bound a loss that follows a large intraday peak. |
| `max_pnl_staleness_seconds` | Mark-to-market interval x 3, or your feed's SLA. | Leaving it `None` means a dead feed silently disarms the drawdown control. |

Invalid values (zero, negative, non-finite, non-integer counts) raise at construction.
This is intentional: `max_daily_drawdown_usd=float('nan')` would otherwise compare
`False` against every drawdown and disable the control while appearing configured.

## 4. Start-up sequence

1. Load the last persisted snapshot from durable storage.
2. `engine.restore(snapshot)` — a missing or unreadable snapshot restores as HALTED.
3. Publish a fresh `update_pnl()` before enabling routing. After a restore the monotonic
   epoch has changed, so the staleness gate blocks until real P&L arrives.
4. Only then allow the strategy to begin submitting.

## 5. Steady-state loop

- Every mark-to-market tick: `update_pnl(realized, unrealized)` with **cumulative session**
  figures, then persist `snapshot()`.
- Every order submission: `check_order_allowed()` exactly once, immediately before
  routing. It consumes one rate slot as a side effect.
- Every acknowledged operation: `register_success()`.
- Every reject / timeout / 5xx: `register_error()` — but see step 6 first.

## 6. Classifying an ambiguous submission before registering an error

A submission that timed out may already have reached the venue. Do not treat the
timeout as a simple error and retry:

1. Resolve the order's actual state against the venue (order status query, client order
   ID lookup) — see `order-placement-idempotency`.
2. If it was accepted, that is a success, not a connectivity failure. Registering it as
   an error while also retrying produces a duplicate fill *and* a false degradation
   signal that can trip a halt with no venue problem behind it.
3. Only an outcome you have confirmed the venue did not receive is an error.

## 7. Responding to a blocked order

`check_order_allowed()` returning `False` means one of two different things. Read
`engine.state` before reacting:

| State | Meaning | Response |
|---|---|---|
| `DEGRADED_WARNING` | A risk input is untrustworthy (P&L stale or non-finite). Recoverable. | Drop the order. Alert the data/infra owner, not the head of trading. The engine clears itself on the next valid `update_pnl`; no human reset is needed or possible. |
| `HALTED` | A risk limit was breached. Terminal. | Drop the order. Fire the high-priority page. Run the cancel-all / flatten. Expect no automatic recovery. |

## 8. Wiring the halt hook

```python
def on_halt(record):
    oms.cancel_all_open_orders()      # RTS 6 Article 12 lives here, not in the engine
    pager.trigger(record.reason)      # independent channel, inside five seconds

engine = CapitalPreservationEngine(limits, on_halt=on_halt)
```

The callback is invoked once per halt, after the engine's lock is released — so it may
call back into the engine without deadlocking — and any exception it raises is logged
and swallowed. A pager outage must never unlatch the kill switch. The corollary is that
a silently failing hook leaves the halt latched but unannounced, so monitor the hook
itself.

Flattening: prefer aggressive limit or market orders. Passive resting orders may not
fill during exactly the volatile conditions that caused the breach.

## 9. Manual reset

`manual_reset(auth_token, operator=..., rebaseline_session_pnl=False)`.

1. A human reviews the halt reason and the audit log.
2. They call the reset with a valid token **and their identity**. The operator string is
   written to the audit trail; an anonymous override is not an auditable one.
3. The gate re-arms, the rate window and error counter clear, but the session's P&L
   history is kept. If the drawdown limit is still breached the engine re-halts *inside
   the same call* — there is no window in which orders flow against a live breach.
4. Granting a fresh drawdown budget requires the explicit `rebaseline_session_pnl=True`,
   which re-anchors the high-water mark to the current session P&L and records that a
   new budget was granted. Note that re-baselining cannot defeat `max_daily_loss_usd`,
   which is measured from flat and is not re-anchorable by design.
5. Persist `snapshot()` immediately after the reset.

## 10. Drills

- Trip each control deliberately in paper: a give-back from a profitable peak, a burst
  above the rate limit, a run of rejects, a stopped P&L feed.
- Kill the process while halted and confirm it restarts HALTED.
- Corrupt the snapshot and confirm it restarts HALTED.
- Attempt a reset with a wrong token, and with no token configured.
- Time the gap between the halt and the page arriving; RTS 6 Article 16(5) bounds the
  equivalent alert at five seconds for firms in scope.

See `position-limit-breach-simulation-fire-drills` and `chaos-engineering-for-trading-infrastructure`.
