# Workflows for Portfolio-Level Stop-Loss Independent of Strategy Stops

The engine is a pure function of an externally supplied `PortfolioState` plus one piece of
mutable state — the latched lockout. Everything below is the operational procedure around it.

## 1. Assemble the portfolio snapshot

1. Pull cash, positions and marks from the **broker/custodian**, not from the bot's internal
   record of what it thinks it traded. A fill-tracking bug otherwise hides a real breach from
   the one control designed to catch it (MiFID II RTS 6 Art. 17(3) frames this reconciliation
   duty for EU investment firms).
2. Normalize every monetary value into one reporting currency before it reaches the engine —
   it performs no FX conversion. See `multi-currency-pnl-and-fx-conversion`.
3. Attach the settled capital flows for the day (`capital_flow_since_sod`) and since the
   high-water mark was set (`capital_flow_since_peak`). Positive for deposits, negative for
   withdrawals. Only **settled** movements count; a pending transfer that has not left the
   account would be double-counted.
4. Where marks carry observation timestamps, populate `price_epoch_s` per position and
   `as_of_epoch_s` on the state, and configure `max_price_staleness_s`.

## 2. Choose the NAV valuation mode once, per account type

| Account type | Mode | NAV |
|---|---|---|
| Cash-funded equities / spot crypto | `CASH_PLUS_MARKET_VALUE` | cash + Σ(qty × price) |
| Futures, CFDs, perpetual swaps, any margined book | `CASH_PLUS_UNREALIZED_PNL` | cash + Σ(unrealized P&L) |

Getting this wrong is silent and catastrophic in one direction: on a margined book,
`qty × price` is notional, so a $500,000 notional position on $1,000,000 of cash reports NAV
of $1,500,000 and the stop never fires. If a single engine covers accounts of both types,
run one engine instance per account type rather than mixing them into one state.

## 3. Evaluate on a fixed cadence

1. Call `evaluate_portfolio_stop(state)` from a risk loop that is **separate** from strategy
   signal generation, so a strategy bug cannot stop the evaluation from running.
2. Pick the cadence from how fast the book can move against you between evaluations, and
   budget the end-to-end latency explicitly (`risk-control-latency-budget`). RTS 6 Art. 16(5)
   sets a five-second alert-generation expectation for EU investment firms as a reference
   point for what "real time" is taken to mean.
3. Watch for the fail-closed statuses as first-class outcomes, not as errors to retry past:
   - `HALTED_INVALID_INPUT` — a `NaN`/`Inf` value, or a non-positive equity baseline.
   - `HALTED_STALE_PRICES` — marks older than `max_price_staleness_s`.
   Both latch the lockout and report `positions_to_flatten_count = 0`: block new risk, page a
   human, and do **not** liquidate on data the engine could not evaluate. A halt does not
   swallow a later breach — once usable data shows a genuine drawdown breach, the latch is
   upgraded to that breach status and the flatten is requested on that evaluation.

## 4. Act on a breach

1. On the first evaluation that reports `DAILY_DRAWDOWN_BREACH_FLATTEN` or
   `PEAK_DRAWDOWN_BREACH_FLATTEN`, `positions_to_flatten_count` is non-zero exactly once.
   Treat that transition as the liquidation trigger; every later poll reports `0` while the
   lockout stays latched, so a 1-second risk loop does not re-issue the cascade every second.
2. Cancel resting orders first, then reduce. Use aggressive marketable orders rather than
   passive resting ones — the conditions that caused the breach are exactly the conditions in
   which a passive order does not fill — while remembering that RTS 6 Art. 16(5) frames the
   remedy as an *orderly* withdrawal from the market, not an indiscriminate dump.
3. Route the liquidation through an order gate that permits reduce-only flow while halted, or
   the lockout vetoes its own flatten. That gate is
   `kill-switch-and-drawdown-circuit-breakers`.
4. Alert out of band (SMS/push/dedicated channel), not only to the log file. Escalate a
   *failed* flatten louder than the breach itself — that is the one outcome where positions
   are still live during a breach.

## 5. Resume only through the human gate

1. `human_re_enable(operator_id, reason)` returns a boolean the caller **must** check. It
   refuses a blank identity, a blank reason, an operator outside `authorized_operators`, and
   a call made while the engine is not locked. Every attempt, granted or refused, is appended
   to `re_enable_log`.
2. Re-enabling clears the latch, not the breach. If the portfolio still breaches, the next
   evaluation re-latches immediately — this is correct behaviour, not a bug.
3. After a **peak** drawdown halt this is the trap: the breached high-water mark survives the
   re-enable, so the engine re-latches on the next call. Resuming requires the operator to
   deliberately re-baseline `peak_equity` in their own state store, with the decision
   recorded. Never re-baseline it automatically — that erases the peak limit while appearing
   to satisfy it.
4. Do not auto-resume after a cooldown. The condition that triggered the breach (bad data, a
   broker issue, a genuine regime shift) may still be present.

## 6. Audit Report Generation

Persist every `PortfolioStopReport` — it carries `current_nav`, `nav_for_drawdown` (NAV net
of capital flows, the figure the limit was actually measured against), both drawdowns, both
breach flags, `is_latched`, `evaluated_at` and the full `audit_notes` string. Together with
`re_enable_log` this is the evidence trail for who resumed trading, when, and why.

## 7. Drill it before relying on it

Engineer each trigger deliberately in a paper/sandbox environment — a breaching NAV, a `NaN`
mark, a stale feed, a re-enable attempt by an unauthorized operator — and confirm the
observed behaviour end to end, including the out-of-band alert. An untested safety mechanism
is not a safety mechanism. See `position-limit-breach-simulation-fire-drills`.
