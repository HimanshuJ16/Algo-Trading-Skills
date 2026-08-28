# Workflows for Strategy-Level vs Portfolio-Level Kill Switch

The engine owns two latches and nothing else. It places no orders, cancels nothing and
computes no P&L; it consumes equity figures you supply and returns a scope decision plus a
one-shot dispatch flag. Everything below is the operational procedure around it.

## 1. Assemble the equity inputs

1. Pull per-strategy equity and total fund equity from the **broker/custodian** account
   state, not from the bot's internal record of what it thinks it traded. A fill-tracking bug
   otherwise hides a real breach from the one control designed to catch it (MiFID II RTS 6
   Art. 17(3) frames this reconciliation duty for EU investment firms).
2. Normalize every figure into one reporting currency first — the engine performs no FX
   conversion. See `multi-currency-pnl-and-fx-conversion`.
3. Attach the cumulative **settled** net capital flow since each peak baseline was recorded:
   `capital_flow_usd`, positive for allocations in, negative for withdrawals. Only settled
   movements count; a pending transfer that has not left the account would be double-counted.
4. The engine does **not** check that the strategy equities sum to the fund equity. If your
   two sources can disagree, reconcile them before evaluation and treat a mismatch as its own
   alert — an engine fed a stale fund total will not notice.

## 2. Configure the two tiers

| Knob | Units | Meaning |
|---|---|---|
| `StrategyState.drawdown_limit_pct` | percentage points (`10.0` = 10%) | Per-strategy isolation limit. |
| `PortfolioState.portfolio_drawdown_limit_pct` | percentage points (`15.0` = 15%) | Fund-wide limit. |
| `PortfolioState.max_tripped_strategies_limit` | integer count | Cascade trigger, counted over strategies latched by their **own** drawdown. |
| `cooldown_seconds` | seconds | Minimum dwell before an audited human re-enable is accepted. |
| `authorized_operators` | tuple of identities | Empty tuple accepts any non-blank identity (still audited). |
| `clock` | callable | Injectable epoch-seconds source, so cooldown behaviour is testable without sleeping. |

Construction validation is deliberately strict, because every one of these failures is
otherwise silent:

- A limit outside `(0, 100]`, non-finite, or non-numeric raises `ValueError`.
- A limit **below 1.0** logs a warning naming the fraction trap. `0.10` meaning "10%" reads
  here as 0.1% and liquidates on noise. A genuine 0.5% limit is legitimate, which is why this
  warns rather than raises — read the warning before ignoring it.
- `max_tripped_strategies_limit` must be an integer `>= 1`; a limit above the registered
  roster logs a warning because the cascade trigger can then never fire.
- Duplicate `strategy_id`s raise, rather than silently shadowing a monitored strategy.

Set the cascade threshold relative to the roster. In a 3-strategy fund a threshold of 3 fires
only once everything is already dead; 2 is the meaningful setting.

## 3. Run both evaluators on a fixed cadence

1. Call `evaluate_strategy_kill_switch()` per strategy and `evaluate_portfolio_kill_switch()`
   for the fund from a risk loop that is **separate** from strategy signal generation, so a
   bug in a strategy cannot stop the evaluation from running.
2. Evaluate the strategy tier before the portfolio tier within a cycle. The cascade counter
   reads strategy latches, so a strategy that breached this cycle should already be latched
   when the portfolio tier counts.
3. Pick the cadence from how fast the book can move against you between evaluations, and
   budget the end-to-end latency explicitly (`risk-control-latency-budget`). RTS 6 Art. 16
   sets a five-second alert-generation expectation for EU investment firms as a reference
   point for what "real time" is taken to mean.

## 4. Read the report correctly

| Field | Question it answers |
|---|---|
| `is_newly_tripped` | **Dispatch the liquidation now, exactly once.** True only on the transition into the trip, including under concurrent evaluation. |
| `is_trading_halted` | **May this scope trade?** For a strategy: True when it is latched *or* the master switch is engaged. |
| `is_triggered` | Is this scope's own kill switch engaged (breach now, or latched earlier)? Latch-inclusive. |
| `affected_strategies` | Strategies **newly** halted by this report — the liquidation work list. Never the full roster of already-halted strategies. |
| `action` | What to dispatch now. `NO_ACTION` whenever there is nothing new to do, including on a fail-closed halt. |
| `reason_code` | `NO_BREACH`, `STRATEGY_DRAWDOWN_BREACH`, `PORTFOLIO_DRAWDOWN_BREACH`, `CASCADE_BREACH`, `LATCHED_PRIOR_TRIP`, `PORTFOLIO_HALT_INHERITED`, `HALTED_INVALID_INPUT`. |
| `tripped_strategy_count` | The cascade counter's current input — strategies latched by their own drawdown only. |
| `drawdown_pct` | Rounded to 2dp for readability. The breach decision uses the unrounded value, so a reported `10.00` with `is_triggered` False means the true drawdown sat just below the limit. |

Never gate a liquidation on `is_triggered`; it stays True for the whole life of the latch, so
a 1-second risk loop would fire a fresh cascade every second while the first is still working.

## 5. Handle the fail-closed statuses as first-class outcomes

`HALTED_INVALID_INPUT` is not an error to retry past. It is returned for a `NaN`/`Inf`
equity, a non-numeric equity (strings are rejected rather than coerced — `"88000"` would
trade while `"88,000"` would raise, and a control whose behaviour depends on number
formatting is a bug waiting for a bad day), and a non-positive peak.

Three properties matter operationally:

1. It **latches** and sets `is_trading_halted=True`, so new risk stops.
2. It **liquidates nothing** — `affected_strategies` is empty and `action` is `NO_ACTION`.
   The engine has no evidence the book is down; market-flattening on a bad tick is itself the
   loss event. Page a human instead.
3. It **does not advance the cascade counter**. This is the single most important coupling in
   the whole design: one dead market-data feed halts every strategy simultaneously, and if
   halts counted as strategy failures the cascade trigger would liquidate a fund that never
   lost a cent.

## 6. Escalation from tier to tier

```
strategy drawdown >= its own limit
        └─► latch that strategy only ─► siblings keep trading
                                     └─► advances the cascade counter

cascade count >= max_tripped_strategies_limit ──┐
fund drawdown >= portfolio limit ───────────────┴─► latch the fund
                                                    ├─ fan out to every NOT-already-latched strategy
                                                    ├─ already-latched strategies keep their original
                                                    │  action / scope and are NOT re-liquidated
                                                    └─ healthy strategies still report
                                                       PORTFOLIO_HALT_INHERITED
```

Two exclusions keep the tiers from contaminating each other, and both are load-bearing:

- Fail-closed halts never count toward the cascade (a data outage is not a cascade of
  strategy failures).
- The master switch's own fan-out never counts toward the cascade (otherwise the master
  switch permanently re-justifies itself and re-trips the instant it is cleared).

## 7. Recovery: strategies first, fund second

`human_re_enable(scope, operator_id, reason, strategy_id=None)` returns a boolean the caller
must check, and appends every attempt — granted *and* refused — to `re_enable_log`.

Refusals: a blank operator identity, a blank reason, an operator outside
`authorized_operators`, an unknown scope, a `STRATEGY_LEVEL` request with no `strategy_id`, a
scope that is not tripped, a request inside the `cooldown_seconds` dwell, and a
`PORTFOLIO_LEVEL` request made while the cascade condition still holds.

The order is fixed and non-racy:

1. **Clear each strategy latch.** Permitted while the fund is still halted, and safe:
   `is_strategy_trading_halted()` stays True and every report carries
   `PORTFOLIO_HALT_INHERITED`, so nothing resumes trading yet.
2. **Re-baseline that strategy's `peak_equity_usd`** if you intend it to resume. Re-enabling
   clears the *latch*, not the breach — the high-water mark survives and the next evaluation
   re-trips otherwise. Never re-baseline automatically; that erases the limit while appearing
   to satisfy it. Record the decision.
3. **Clear the master latch.** This also releases every strategy the master switch itself
   halted (`tripped_by_scope == PORTFOLIO_LEVEL`); a strategy that tripped on its own
   drawdown is left latched and needs step 1.
4. **Re-baseline `total_peak_equity_usd`** on the same deliberate basis, if the fund tripped
   on drawdown.

Attempting the master latch first is refused with a message naming the strategies still in
breach — the refusal exists precisely so the fund cannot be re-enabled straight back into a
re-trip.

The cooldown is a **minimum dwell that gates a human decision**, never an auto-resume. No
rule surveyed in `references/standards.md` mandates a duration; Nasdaq's venue-side kill
switch is likewise human-gated rather than time-gated, requiring the participant to explain
why re-authorisation is safe.

## 8. Wire it into the rest of the stack

- **Order entry:** gate every order on `is_strategy_trading_halted(strategy_id)`, and make
  sure the gate permits reduce-only flow while halted, or the halt vetoes its own liquidation
  — `kill-switch-and-drawdown-circuit-breakers`.
- **Cancellation:** dispatch `affected_strategies` to the order gateway, which latches order
  entry *before* sending cancels and tracks which cancels the venue actually confirmed —
  `execution-algorithm-kill-switch-integration`. A cancel is a request, not a state change.
- **Authorisation:** RBAC, four-eyes sign-off and break-glass tokens around
  `human_re_enable()` belong to `emergency-manual-override-access-control`.
- **Alerting:** route every `is_newly_tripped` report, and more loudly every failed
  liquidation, through a channel independent of normal logging. Isolate the alert call so a
  channel that raises cannot abort the liquidation.
- **Durability:** all engine state is in memory. A restart re-arms both tiers with no record
  that anything was halted. Persist every report and every `re_enable_log` entry, and rebuild
  the latches on start-up or fail closed.

## 9. Live-fire drills

Unit tests are necessary and not sufficient. Before relying on any of this, deliberately
engineer each trigger in a paper/sandbox environment:

1. A single strategy breaching its own limit — confirm siblings keep trading.
2. Enough strategies breaching to reach the cascade threshold with fund equity healthy.
3. Fund drawdown breaching with every strategy individually inside its limit.
4. A market-data feed cut mid-session — confirm halts, confirm **nothing** is liquidated, and
   confirm the fund does not cascade.
5. A full recovery sequence with an unauthorised operator, an in-cooldown attempt, and a
   master-first attempt, all refused and all present in the audit trail.
6. A process restart while both tiers are latched — confirm your persistence layer restores
   them rather than silently re-arming the book.
