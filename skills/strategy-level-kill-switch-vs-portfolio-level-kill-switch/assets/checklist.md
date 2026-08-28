# Pre-Flight / Sign-off Checklist — strategy-level-kill-switch-vs-portfolio-level-kill-switch

Use this before considering the skill's implementation complete.

## Scope separation

- [ ] **Independent Risk Loop:** Confirm both evaluators run in a loop separate from strategy signal generation, so a strategy bug cannot stop them running.
- [ ] **Strategy Tier Isolates:** Confirm a strategy breaching its own limit halts *only* that strategy, and that its siblings keep trading.
- [ ] **Portfolio Tier Escalates:** Confirm a fund-drawdown breach or a cascade halts every strategy, and that a healthy strategy inside a halted fund reports `PORTFOLIO_HALT_INHERITED` with `is_trading_halted = True`.
- [ ] **Fan-Out Preserves the Audit Record:** Confirm the master switch leaves an already-latched strategy's `action_taken` and `tripped_by_scope` untouched, does not re-queue it in `affected_strategies`, and stamps a real `tripped_time_epoch` on the strategies it does halt.

## Inputs and limits

- [ ] **Broker-Sourced Equity:** Confirm both strategy and fund equity come from the broker/custodian account state, not internal bookkeeping, and are in one reporting currency.
- [ ] **Limits Are Percentage Points:** Confirm every `drawdown_limit_pct` is in percentage points (`10.0` = 10%, not `0.10`), that an out-of-range value raises `ValueError`, and that any sub-1.0 limit is deliberate — the engine warns because `0.10` reads here as 0.1%.
- [ ] **Limits Are Calibrated, Not Inherited:** Confirm the 10% / 15% / cascade defaults were reviewed against your own drawdown history and recorded as firm risk policy — no regulator mandates them (`references/standards.md`).
- [ ] **Cascade Threshold Fits the Roster:** Confirm `max_tripped_strategies_limit` is meaningful for the number of registered strategies (a threshold equal to the roster only fires once everything is already dead; above it, never).
- [ ] **Unique Strategy IDs:** Confirm construction rejects duplicate `strategy_id`s rather than silently dropping a monitored strategy.
- [ ] **Capital Flows Adjusted:** Confirm every settled allocation change is reported via `capital_flow_usd`, and that a scheduled withdrawal neither trips a strategy nor ratchets its peak.

## Fail-closed behaviour

- [ ] **Bad Data Halts, Never Passes:** Confirm a `NaN`/`Inf`/non-numeric equity or a non-positive peak returns `HALTED_INVALID_INPUT` with the latch set — not a healthy report.
- [ ] **Halts Do Not Liquidate:** Confirm both fail-closed paths report `affected_strategies = []` and `action = NO_ACTION`, and route to a human instead of market-flattening on unevaluable data.
- [ ] **Halts Do Not Cascade:** Confirm a simulated feed outage that halts every strategy leaves `cascade_trip_count` at 0 and does **not** trip the master switch.
- [ ] **Fan-Out Does Not Cascade:** Confirm that after a master trip, `cascade_trip_count` counts only strategies latched by their own drawdown, so a cleared master switch does not immediately re-trip.

## Latching and dispatch

- [ ] **Trips Latch:** Confirm a trip survives a full equity recovery, a flat book and the next trading day, and that `is_latched` distinguishes a latched lock from a fresh breach.
- [ ] **Liquidation Fires Once:** Confirm the liquidation is dispatched on `is_newly_tripped` — never on `is_triggered` — and that concurrent evaluations of the same breaching state produce exactly one dispatch.
- [ ] **Breach Thresholds:** Confirm a drawdown exactly equal to a limit breaches (`>=`), and that the breach flag is decided on the unrounded value.
- [ ] **Healthy Means No Action:** Confirm a non-breaching report carries `action = NO_ACTION`, so a caller reading `action` alone cannot halt a healthy strategy.
- [ ] **Mistyped Kills Raise:** Confirm `action=NO_ACTION` and an unknown action raise `ValueError` rather than returning a report that reads like a successful kill.
- [ ] **Thread Safety:** Confirm every latch transition is lock-guarded where a strategy loop, a risk poller and an operator endpoint can run concurrently.

## Recovery

- [ ] **Human Re-Enable Gate:** Confirm `human_re_enable()` refuses a blank identity, a blank reason, an unlisted operator, an unknown scope, a missing `strategy_id` and an untripped scope, returns a checked boolean, and appends every attempt to `re_enable_log`.
- [ ] **Cooldown Gates, Never Resumes:** Confirm the dwell blocks an early re-enable and that nothing anywhere re-arms on a timer.
- [ ] **Recovery Order Enforced:** Confirm a `PORTFOLIO_LEVEL` re-enable is refused while the cascade condition still holds, and that a `STRATEGY_LEVEL` re-enable inside a halted fund leaves `is_strategy_trading_halted()` True.
- [ ] **Scoped Release:** Confirm clearing the master latch releases only the strategies the master switch itself halted, leaving own-drawdown trips latched.
- [ ] **Peak Re-Baseline Is Deliberate:** Confirm resuming after a drawdown trip requires an explicit, recorded operator change to `peak_equity_usd` / `total_peak_equity_usd` — never an automatic reset.

## Integration and operations

- [ ] **Order Gate Wired:** Confirm order entry is gated on `is_strategy_trading_halted()`, and that the gate permits reduce-only flow while halted so the halt does not veto its own liquidation (`kill-switch-and-drawdown-circuit-breakers`).
- [ ] **Cancellation Wired:** Confirm `affected_strategies` is dispatched to a gateway that latches order entry before sending cancels and tracks venue confirmations (`execution-algorithm-kill-switch-integration`).
- [ ] **Out-Of-Band Alerting:** Confirm every new trip and, more loudly, every *failed* liquidation reach a human through a channel independent of normal logging, and that an alert channel that raises cannot abort the liquidation.
- [ ] **State Is Durable:** Confirm every report and every `re_enable_log` entry is persisted, and that a process restart restores both latches rather than silently re-arming the book.
- [ ] **Automated Testing:** Run `python -m unittest discover -s skills/strategy-level-kill-switch-vs-portfolio-level-kill-switch/scripts` and confirm a 100% pass rate.
- [ ] **Live-Fire Drill:** Confirm each trigger — single strategy breach, cascade, fund drawdown, feed outage, refused re-enable, restart while latched — has been deliberately engineered in a paper/sandbox environment, not merely unit-tested.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
