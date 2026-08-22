---
name: capital-preservation-mode-for-degraded-conditions
description: Use when a live trading system needs a strategy-independent gate that
  blocks new orders once peak-to-trough drawdown, order submission rate, consecutive
  venue errors, or mark-to-market feed staleness breach hard limits, and that stays
  halted until a human clears it.
domain: Risk Management
subdomain: Emergency Controls
tags:
- kill-switch
- capital-preservation
- drawdown
- circuit-breaker
- risk
brokers_frameworks:
- Generic Risk Engineering
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill to build the last line of defence for a live algorithmic trading system: an observer that sits *between* strategy logic and the execution gateway and refuses to pass new orders once a hard limit is breached. It enforces four independent controls — peak-to-trough drawdown of session P&L, order submission rate over a rolling window, consecutive broker/venue errors, and staleness of the P&L feed the drawdown control depends on — and it does not auto-recover from a limit breach.

Reach for it when: capital is real, the strategy is capable of looping, and nobody is watching the screen continuously.

## When NOT to Use

- **As your only MiFID II RTS 6 control.** Article 12 kill functionality requires the ability to *cancel* unexecuted orders across every venue you are connected to. This engine blocks new submissions; it does not cancel resting orders or flatten positions. Wire its `on_halt` callback to whatever does, and treat that wiring as the compliance artefact.
- **As a substitute for per-order pre-trade checks.** Price collars, maximum order value and maximum order volume (RTS 6 Article 15(1)(a)–(c)) are per-order checks this engine does not perform — it never sees order contents. Use `sec-rule-15c3-5-risk-controls-us` and `kill-switch-and-drawdown-circuit-breakers` alongside it.
- **Across processes.** The engine is thread-safe within one process and holds state in memory. Two order-routing processes each running their own engine enforce two independent budgets, not one shared budget. Run a single gateway process, or promote the counters to shared storage.
- **As a strategy-level stop-loss.** Sizing and per-trade stops belong in the strategy; this is the layer that must keep working when the strategy does not.

## Prerequisites

- A chokepoint through which *every* order submission passes, so the check cannot be bypassed by a code path that forgets to call it.
- A mark-to-market source producing cumulative session realized and unrealized P&L, independent of the strategy's own bookkeeping.
- A reset secret provisioned out of band, in `CAPITAL_PRESERVATION_RESET_TOKEN` or an injected `ResetAuthorizer`. There is no default: an engine deployed without one cannot be reset at all, which is the intended failure direction.
- Durable storage for `snapshot()` if the system can restart while halted.

## Workflow

1. **Configure limits.** Instantiate `PreservationLimits`. Every field is a placeholder calibrated by you, not a standard: set `max_daily_drawdown_usd` from the desk's risk tolerance, `max_orders_per_minute` from measured peak operating rate (the reference workflow suggests ~2x peak), `max_consecutive_errors` from the venue's normal reject rate. Set `max_pnl_staleness_seconds` unless another layer guarantees P&L freshness — leaving it `None` means a dead mark-to-market feed silently disarms the drawdown control. Invalid limits (zero, negative, NaN) raise at construction rather than failing open at runtime.
2. **Restore before trading.** On start-up, load the last persisted `snapshot()` and call `restore()`. A process that restarts mid-halt must come back HALTED; an unreadable snapshot restores as HALTED rather than ACTIVE.
3. **Gate every submission.** Call `check_order_allowed()` exactly once immediately before routing each order. It consumes one slot of the rate budget as a side effect — calling it speculatively, or twice for one order, inflates the measured rate and can trip a halt that cannot be undone without human action.
4. **Distinguish the two failure states before reacting.** `False` from the gate means one of two different things, and conflating them produces the wrong operational response:
   - `EngineState.DEGRADED_WARNING` — a risk *input* is untrustworthy (stale or non-finite P&L). Recoverable; the engine clears itself on the next valid `update_pnl`. Do not page the head of trading; fix the feed.
   - `EngineState.HALTED` — a risk *limit* was breached. Terminal. Page a human, run the cancel-all, and expect no automatic recovery.
5. **Feed the inputs.** Call `update_pnl(realized, unrealized)` on every mark-to-market tick with *cumulative session* figures, not increments. Call `register_error()` on every broker reject, socket timeout or 5xx, and `register_success()` on every acknowledged operation — an error counter that is never cleared drifts into a spurious halt.
6. **Classify before retrying, then register.** A submission that timed out may still have reached the venue. Resolve the ambiguity (see `order-placement-idempotency`) before deciding whether it was an error; registering a timeout as an error *and* retrying it turns one ambiguous order into a duplicate fill plus a false connectivity signal.
7. **Act on the halt out of band.** Pass an `on_halt` callback that cancels resting orders, flattens if policy requires it, and alerts through a channel independent of the bot's normal logging. The callback runs outside the engine's lock, so it may call back into the engine, and an exception it raises is logged and swallowed — a broken pager never unlatches the halt.
8. **Reset deliberately.** `manual_reset(token, operator=...)` re-arms the gate but keeps the session's P&L history, so a reset issued while the drawdown is still breached re-halts inside the call rather than leaving a window in which orders flow. Granting a fresh drawdown budget requires the explicit `rebaseline_session_pnl=True`, which is recorded in the audit log. Always pass `operator`.

> Full procedure: see `references/workflows.md`.
> Standards, limit provenance and regulatory context: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Measuring "drawdown" from zero instead of from the peak.** Treating the loss limit as `abs(session P&L) when negative` means a strategy that runs to +$400k and gives back $390k reports a drawdown of zero and never trips. Track a high-water mark seeded at flat and measure peak-to-trough against it. If you also need a hard floor on the day, that is a *separate* limit (`max_daily_loss_usd`), because a large intraday peak makes a catastrophic loss look like a modest give-back.
- **Letting a NaN disable the control.** `float('nan') >= limit` is `False`, so a single bad mark silently turns the drawdown check into a no-op that reports ACTIVE. Treat non-finite P&L as a data-integrity failure that blocks orders, not as a value to compare.
- **Trusting a P&L feed that stopped updating.** If nothing checks freshness, a dead mark-to-market process leaves the engine ACTIVE and permanently unaware of an unbounded loss. Absence of a breach signal is not evidence of no breach.
- **Timing the rolling window on the wall clock.** `time.time()` is not monotonic. An NTP step forward empties the order-rate window and lets a runaway algorithm straight through; a step backward freezes it. Use `time.monotonic()` for every interval, and reserve wall-clock time for audit timestamps.
- **In-band risk checks.** Embedding the kill switch inside strategy logic means a strategy bug can bypass the very check meant to contain it. It must sit between the strategy and the gateway.
- **Monitoring P&L but not order frequency.** A strategy rapidly cancel-replacing far from the touch may never move P&L while it burns through exchange message budgets, order-to-trade ratio penalties, or an IP ban.
- **A hard-coded reset token.** A kill switch clearable with a constant published in the source is not a kill switch. Read the secret from the environment or an injected authorizer, compare it in constant time, and deny every reset when none is configured.
- **A reset that forgets the losses.** Zeroing the drawdown on reset hands the strategy a full second drawdown budget. Re-baselining is sometimes the right call, but it must be an explicit, recorded decision — never a side effect of clearing a halt.
- **In-memory state only.** A halted engine that restarts into ACTIVE has un-halted itself. Persist the state and fail closed on an unreadable snapshot.
- **An unauthenticated, unlogged override.** Who cleared the switch, when, and what they cleared is the first question after an incident and a standing expectation of any algo-trading governance regime.

## Verification

- Run `python -m unittest discover -s skills/capital-preservation-mode-for-degraded-conditions/scripts` from the repository root, or `python -m unittest discover -s .` from inside `scripts/`.
- Drive a fake clock (not `sleep`) to assert the rolling-window boundary exactly: at the configured rate the gate must pass; one tick tighter it must halt.
- Assert the give-back case explicitly: feed +$40,000 then +$29,000 against a $10,000 drawdown limit and confirm the engine halts *while the session is still profitable*.
- Feed a NaN mark and confirm the gate blocks and the state is `DEGRADED_WARNING`, not `ACTIVE`.
- Stop the P&L feed and confirm orders are blocked once `max_pnl_staleness_seconds` elapses, and that trading resumes by itself when the feed returns.
- Snapshot a halted engine, construct a fresh one, restore, and confirm it comes back HALTED; repeat with a corrupt snapshot and confirm it also comes back HALTED.
- Confirm `manual_reset` rejects a wrong token, rejects every token when no secret is configured, and re-halts immediately when the drawdown limit is still breached.
- In a paper environment, engineer each trigger for real before relying on any of it live — an untested safety mechanism is not a safety mechanism.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `broker-side-order-throttle-detection`
- `order-placement-idempotency`
- `black-swan-playbook-for-halted-markets`
- `risk-control-bypass-audit-logging`
