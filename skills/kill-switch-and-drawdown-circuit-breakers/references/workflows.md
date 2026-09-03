# Deep Workflow Reference — kill-switch-and-drawdown-circuit-breakers

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Independent Risk Module Isolation:**
   - Instantiate `KillSwitchCircuitBreaker` as an independent module outside signal generation logic.
   - Enforce mandatory order vetoing via `check_proposed_order()` before any broker order routing.
   - `check_proposed_order()` returns `(approved, reason)` where `reason` is always prefixed
     with an `OrderDecisionCode` value and `": "`. Branch on the code — `OK`,
     `REDUCE_ONLY_ALLOWED`, `HALTED`, `POSITION_LIMIT`, `INVALID_INPUT` — never on the
     human-readable remainder, which is free text and may change.

2. **Multi-Tier Circuit Breaker Evaluation:**
   - **Position Limit:** Veto orders exceeding instrument `max_position`.
   - **Reduce-only carve-out:** an order that strictly moves the position toward zero
     without crossing it (`is_risk_reducing()`) is approved even while halted and even when
     the position already exceeds `max_position`. A *reversal* (long 100 → short 50) is not
     risk-reducing: it closes one exposure and opens a new one, and stays fully gated.
     Set `allow_reduce_only_when_halted=False` only if liquidation bypasses this gate
     entirely — otherwise the halt vetoes its own force-flatten.
   - **Daily Loss Limit:** Halt trading and trigger `flatten_fn()` when daily PnL breaches `-max_daily_loss`.
   - **Max Drawdown High-Water Mark:** Track running `peak_equity`; halt and force-flatten when equity drawdown $\ge \text{max\_drawdown\_pct}$.
   - **Fail-closed on non-evaluable input:** a non-finite `daily_pnl` or `current_equity`, or
     a non-positive peak equity, halts with `HALTED_INVALID_INPUT`. NaN compares `False`
     against every threshold, so *not* halting would leave both the loss limit and the
     drawdown limit silently inert.
   - **Optional runaway escalation:** `max_consecutive_rejections=N` halts with
     `HALTED_POSITION_LIMIT` after N consecutive position-limit rejections. Disabled by
     default — pick N from your own order-rate profile; this library does not assume one.
   - **Manual Emergency Kill Switch:** Expose `trigger_emergency_kill_switch()` for immediate operator halts.

3. **Capital Flow Adjustment:**
   - Call `record_capital_flow(amount)` when a deposit (+) or withdrawal (−) settles, before
     the next `check_pnl_and_drawdown()` that uses post-flow equity.
   - Without it a scheduled withdrawal is booked as drawdown and trips the kill switch on a
     healthy book; a deposit inflates the high-water mark and understates real drawdown.

4. **Broker Position Reconciliation:**
   - Periodically execute `reconcile_broker_positions(internal_pos, broker_pos)`.
   - Halt trading immediately if internal vs broker position desync exceeds `desync_tolerance_units`.
   - Symbols are compared in sorted order, so the symbol named in the audit log is
     reproducible across processes. A desync found while *already* halted is logged without
     re-running the liquidation response.

5. **Mandatory Human Re-Enable Gate:**
   - Disallow automatic resumption after circuit breaker halts.
   - Require explicit human action via `human_re_enable(authorized_user, reason)` to clear halt state.
   - The call **returns `False` and stays halted** on a blank operator identity, a blank
     reason, an operator absent from `authorized_operators`, or when nothing is halted.
     Check the return value; do not assume the halt cleared.
   - After a *drawdown* halt the breached high-water mark survives the re-enable, so the next
     evaluation re-halts immediately. That is deliberate. To resume, the operator must pass
     `new_peak_equity=<value>` — an explicit, audited decision, never an automatic reset.
   - Every attempt, granted or refused, appends a `ReEnableEventLog` to `re_enable_log`.

6. **Out-of-Band High-Priority Alerting:**
   - Emit high-priority alerts (`CRITICAL RISK ALERT`) via dedicated channels (PagerDuty, SMS, Slack/Telegram).
   - `alert_fn` failures are caught and recorded in `BreachEventLog.alert_error` — a dead
     alert channel must never prevent the force-flatten from running.
   - The internal lock is held across `flatten_fn`, and is re-entrant: the callback may
     route its liquidation orders back through `check_proposed_order()` on the same
     thread. It must not block waiting on a *different* thread that calls into the
     breaker — that thread cannot acquire the lock until the flatten returns.
   - A `flatten_fn` failure sets `flatten_succeeded=False`, records `flatten_error`, and
     fires a **second, escalated alert** (`FORCE-FLATTEN FAILED … MANUAL INTERVENTION
     REQUIRED`), because positions are still live during a breach.

## Known Failure Modes

- **In-Line Risk Logic:** Implementing risk checks as conditional branches inside strategy code, bypassed when a strategy bug occurs.
- **Auto-Resume Flappers:** Automatically resuming trading after a 15-minute cooldown, incurring repeated daily loss breaches.
- **Unreconciled Fill Desync:** Relying exclusively on internal trade logs without verifying actual broker account balances.
- **Passive Order Flattening:** Placing passive limit orders to exit positions during a crash, which remain unfilled as prices gap down.
- **Self-Blocking Kill Switch:** Routing every order through the risk gate — as step 1 instructs — while the gate rejects *all* orders once halted, so the force-flatten orders are vetoed by the breaker that ordered them.
- **Silent NaN Fail-Open:** A stale mark or a zero-denominator producing `NaN` P&L. Every threshold comparison returns `False`, so the breaker reports "ok" while checking nothing.
- **Percent/Fraction Confusion:** `max_drawdown_pct=10` intended as 10%. The drawdown breaker can never fire and nothing signals it — hence the constructor rejects any value outside $(0, 1]$.
- **Withdrawal-Triggered Liquidation:** A scheduled cash withdrawal read as drawdown, flattening a healthy book at market.
- **Alert-Channel Coupling:** An `alert_fn` that raises when the network is down — the same failure that often accompanies the breach — propagating out of the halt path and skipping liquidation entirely.

## Production Implementation Reference

- Reference code: `scripts/circuit_breaker.py` (`KillSwitchCircuitBreaker`, `CircuitBreakerStatus`, `OrderDecisionCode`, `BreachEventLog`, `ReEnableEventLog`, `is_risk_reducing`).
- Automated unit tests: `scripts/test_kill_switch_circuit_breaker.py`.
