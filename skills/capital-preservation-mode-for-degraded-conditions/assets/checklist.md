# Pre-Flight Checklist — Capital Preservation Mode

Sign off before the engine gates real capital.

## Placement

- [ ] Is the engine a mandatory gateway on the single path every order takes, rather than an optional strategy-level check?
- [ ] Has every code path that reaches the execution gateway been traced to confirm none bypasses `check_order_allowed()`?
- [ ] Is `check_order_allowed()` called exactly once per outbound submission — not speculatively, not twice?
- [ ] Is there exactly one engine instance for the order flow it governs? (Two routing processes enforce two independent budgets, not one.)

## Limits

- [ ] Has every default in `PreservationLimits` been replaced with a value calibrated to this desk? (None of them is a regulatory or venue figure.)
- [ ] Is `max_orders_per_minute` set below the venue's own message limit, so this control fires before the venue throttles or bans?
- [ ] Is `max_pnl_staleness_seconds` set? (Left `None`, a dead mark-to-market feed silently disarms the drawdown control.)
- [ ] Is `max_daily_loss_usd` set? (A drawdown limit alone cannot bound a loss that follows a large intraday peak.)
- [ ] Is `max_consecutive_errors` calibrated for this desk's message rate, given the counter has no time decay?

## Correctness of the drawdown control

- [ ] Is drawdown measured peak-to-trough against a high-water mark, not as absolute loss from flat?
- [ ] Has the give-back case been tested — profitable session, large intraday peak, breach triggered while still net positive?
- [ ] Does a non-finite P&L block orders rather than silently comparing `False` against every limit?
- [ ] Is `update_pnl` fed **cumulative session** figures, not increments?

## Timing

- [ ] Are all intervals measured on a monotonic clock, with wall-clock time used only for audit timestamps?
- [ ] Has the rolling-window boundary been tested with a fake clock rather than `sleep`?

## Persistence

- [ ] Does the engine persist `snapshot()` after every state change, to storage that survives the process?
- [ ] Does start-up `restore()` before routing is enabled?
- [ ] Has a restart during a halt been drilled, confirming the engine wakes up HALTED?
- [ ] Has a corrupt/missing snapshot been drilled, confirming it also fails closed to HALTED?

## Override

- [ ] Is the reset secret supplied at runtime (environment or injected authorizer) with no default anywhere in source or images?
- [ ] Has it been confirmed that an unconfigured secret denies every reset?
- [ ] Is the token compared in constant time?
- [ ] Is `operator` supplied on every reset, and does the audit log capture who reset, when, what was cleared, and whether a new drawdown budget was granted?
- [ ] Is `rebaseline_session_pnl=True` gated on a deliberate human decision rather than used as the routine way to clear a halt?

## Halt response

- [ ] Is `on_halt` wired to something that actually cancels resting orders and flattens if policy requires it? (The engine blocks new orders only — it does not satisfy MiFID II RTS 6 Article 12 on its own.)
- [ ] Does the alert travel on a channel independent of the bot's normal logging?
- [ ] For firms in scope of RTS 6, does the alert arrive within five seconds of the halt (Article 16(5))?
- [ ] Is the hook itself monitored, given that an exception inside it is logged and swallowed to keep the halt latched?
- [ ] Do runbooks distinguish `DEGRADED_WARNING` (recoverable, fix the feed) from `HALTED` (terminal, page a human)?

## Drills

- [ ] Has each control been tripped deliberately in a paper environment — give-back, rate burst, reject run, stale feed?
- [ ] Has a wrong-token reset been attempted and rejected?
- [ ] Is the whole suite green: `python -m unittest discover -s skills/capital-preservation-mode-for-degraded-conditions/scripts`?
