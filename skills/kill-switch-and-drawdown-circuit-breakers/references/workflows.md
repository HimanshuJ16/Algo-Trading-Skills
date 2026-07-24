# Deep Workflow Reference — kill-switch-and-drawdown-circuit-breakers

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## Full Procedure

1. **Independent Risk Module Isolation:**
   - Instantiate `KillSwitchCircuitBreaker` as an independent module outside signal generation logic.
   - Enforce mandatory order vetoing via `check_proposed_order()` before any broker order routing.

2. **Multi-Tier Circuit Breaker Evaluation:**
   - **Position Limit:** Veto orders exceeding instrument `max_position`.
   - **Daily Loss Limit:** Halt trading and trigger `flatten_fn()` when daily PnL breaches `-max_daily_loss`.
   - **Max Drawdown High-Water Mark:** Track running `peak_equity`; halt and force-flatten when equity drawdown $\ge \text{max\_drawdown\_pct}$.
   - **Manual Emergency Kill Switch:** Expose `trigger_emergency_kill_switch()` for immediate operator halts.

3. **Broker Position Reconciliation:**
   - Periodically execute `reconcile_broker_positions(internal_pos, broker_pos)`.
   - Halt trading immediately if internal vs broker position desync exceeds `desync_tolerance_units`.

4. **Mandatory Human Re-Enable Gate:**
   - Disallow automatic resumption after circuit breaker halts.
   - Require explicit human action via `human_re_enable(authorized_user, reason)` to clear halt state.

5. **Out-of-Band High-Priority Alerting:**
   - Emit high-priority alerts (`CRITICAL RISK ALERT`) via dedicated channels (PagerDuty, SMS, Slack/Telegram).

## Failure Modes Observed in Production

- **In-Line Risk Logic:** Implementing risk checks as conditional branches inside strategy code, bypassed when a strategy bug occurs.
- **Auto-Resume Flappers:** Automatically resuming trading after a 15-minute cooldown, incurring repeated daily loss breaches.
- **Unreconciled Fill Desync:** Relying exclusively on internal trade logs without verifying actual broker account balances.
- **Passive Order Flattening:** Placing passive limit orders to exit positions during a crash, which remain unfilled as prices gap down.

## Production Implementation Reference

- Reference code: `scripts/circuit_breaker.py` (`KillSwitchCircuitBreaker`, `CircuitBreakerStatus`, `BreachEventLog`).
- Automated unit tests: `scripts/test_circuit_breaker.py`.
