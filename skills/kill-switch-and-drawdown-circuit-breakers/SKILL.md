---
name: kill-switch-and-drawdown-circuit-breakers
description: Use when a live trading bot needs hard, strategy-independent limits that
  force-flatten positions and halt new orders, so that a strategy bug or unexpected
  market condition cannot cause unbounded loss
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
brokers_frameworks: []
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any bot that will place live orders with real capital, regardless of how well-validated the strategy logic is — this skill exists specifically because strategy logic, no matter how well-tested, can behave unexpectedly on data patterns not seen in backtesting, and because the code implementing risk limits must be structurally independent from the code implementing strategy signals so that a bug in the strategy cannot also disable the safeguard.

## When NOT to Use

- **As a substitute for per-strategy risk logic.** This is a last-resort backstop that halts everything. Strategy-level stops and per-strategy drawdown limits belong in `strategy-level-kill-switch-vs-portfolio-level-kill-switch` and `portfolio-level-stop-loss-independent-of-strategy-stops`; if this breaker fires routinely, your strategy-level controls are too loose.
- **As a source of regulatory thresholds.** The limits you configure here are *your* risk policy. No rule surveyed in `references/standards.md` imposes a drawdown or daily-loss number on a trading firm — SEC Rule 15c3-5 binds broker-dealers, and SEBI's kill-switch obligation sits with the exchanges. Do not present these numbers to an auditor as regulatory minimums.
- **In backtests.** A high-water-mark breaker inside a backtest silently truncates the equity curve and flatters the result. Model it explicitly as a strategy rule instead — see `lookahead-bias-elimination`.
- **Where a lower-latency control already exists.** If your broker or venue offers a native position/loss limit enforced at their edge, use it *as well*; a client-side breaker cannot act on orders already in flight.

## Prerequisites

- Explicit, pre-agreed numeric limits: max position size per instrument, max aggregate position size, max daily loss (absolute and/or percentage of capital), max drawdown from peak equity
- A reliable, low-latency source of current position and P&L state independent of the strategy's own internal bookkeeping (ideally reconciled against the broker's actual position/margin data, not just the bot's internal model of its positions)
- A notification of every settled cash deposit and withdrawal, so the equity high-water mark can be adjusted for capital flows rather than reading them as P&L

## Workflow

1. Implement risk checks as a separate module/process from strategy signal generation, with the risk module having the authority to veto or override any order the strategy logic attempts to place — never implement risk limits as "just another condition" inside the strategy's own decision function, since a bug there can silently bypass the check.
2. Define at minimum these circuit breakers, each independently triggerable:
   - **Position size limit:** reject any order that would push a single instrument's position beyond a defined max size.
   - **Aggregate exposure limit:** reject any order that would push total notional exposure across all positions beyond a defined max (this interacts with `correlation-aware-exposure-limits` for concentration-specific limits).
   - **Daily loss limit:** once realized + unrealized P&L for the trading day breaches a defined negative threshold, halt all new order placement and, depending on policy, force-flatten open positions.
   - **Max drawdown from peak equity:** track a running peak-equity high-water mark; if current equity falls a defined percentage below that peak, trigger the same halt/flatten response as the daily loss limit — this catches slower-bleeding losses that a single-day check might not.
3. **Carve out risk-reducing orders from the veto.** If every order routes through the risk gate — which it must — then a gate that rejects *everything* while halted also rejects the force-flatten orders it just demanded. Classify each proposed order: an order that strictly moves the position toward zero without crossing it is permitted while halted; an order that increases exposure, opens from flat, or *reverses* the position (long 100 → short 50 closes one exposure and opens another) stays blocked.
4. **Validate the limits themselves at construction, and fail closed on inputs you cannot evaluate.** A drawdown limit passed as `10` meaning "10%" but read as 1000% disables the breaker for the life of the process with no outward signal. So does a `NaN` P&L: every threshold comparison against `NaN` returns false, so the breaker reports healthy while checking nothing. Reject out-of-range limits at construction, and treat a non-evaluable risk input as a halt condition, not as a passed check.
5. On any circuit breaker triggering, the response must be deterministic and pre-defined, not decided in the moment: typically (a) immediately reject/cancel any pending new orders, (b) force-flatten existing positions via market or aggressive limit orders (not passive resting orders that may not fill in time), and (c) require explicit human re-enable before the bot resumes placing new orders — do not auto-resume after a cooldown period without human review, since the condition that triggered the breaker may still be present.
6. **Make the re-enable gate an actual gate, and make it recoverable.** Enforce the operator identity rather than merely logging whatever string was passed, refuse blank identities and blank reasons, return a boolean the caller must check, and append every attempt — granted *and* refused — to an audit trail. Then note the trap: after a drawdown halt the breached high-water mark survives the re-enable, so the next evaluation re-halts immediately. Resuming requires the operator to explicitly re-baseline the peak. Never re-baseline automatically — that silently erases the drawdown limit.
7. Reconcile the risk module's view of positions/P&L against the broker's actual account state on a tight polling interval (or via webhook/push updates if the broker supports it) — a risk module relying solely on the bot's internal record of "what I think I've traded" can be wrong if an order's fill status was misread, defeating the entire purpose of an independent safeguard.
8. **Adjust the high-water mark for capital flows.** A settled withdrawal lowers equity without being a loss; left unadjusted, a routine cash movement trips the drawdown breaker and liquidates a healthy book at market. A deposit raises the peak and understates subsequent real drawdown.
9. Make circuit breaker triggers loud: alert via a channel independent of the bot's normal logging (SMS, push notification, a dedicated alert channel) so a human is aware immediately, not only discoverable by checking logs later. Isolate the alert call — a channel that raises must not abort the liquidation — and escalate a *failed* force-flatten as its own, louder alert, because that is the one outcome where positions are still live during a breach.
10. Guard every state transition with a lock if any of the strategy loop, a reconciliation poller, and an operator kill-switch endpoint can run concurrently. Two threads both reading `halted == False` before either sets it means two liquidation cascades, or two orders that each individually passed the position check.
11. Test each circuit breaker's trigger condition and response in a paper/sandbox environment by deliberately engineering the trigger condition (e.g., simulate a large adverse price move) before ever relying on it in live trading — an untested safety mechanism is not a safety mechanism.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- Implementing risk limits as conditional logic inside the same function/class that generates trading signals, so a bug in signal logic can bypass the check entirely.
- Building a gate that rejects every order while halted, then routing the force-flatten through that same gate — the kill switch vetoes its own liquidation, and the positions it was built to close stay open.
- Trusting a risk input you never checked for `NaN` or `Inf`. A stale mark or a zero-denominator P&L calculation makes every threshold comparison false, so the daily loss limit and the drawdown limit both go quiet at once, and nothing reports it.
- Passing a drawdown limit as `10` for "10%" into a parameter that expects a fraction. The breaker can never fire, and the first evidence is the loss it was supposed to prevent.
- Treating a settled cash withdrawal as drawdown, so a scheduled transfer trips the kill switch and market-flattens a book that was never in trouble.
- Auto-resuming trading after a circuit breaker cooldown period without human review — if the underlying condition (bad data, a broker issue, an actual bad market regime) is still present, auto-resume just delays the same breach.
- A re-enable function that accepts any string as the operator, always returns success, and writes nothing to an audit trail — it documents an authorization gate rather than enforcing one, and leaves no evidence of who resumed trading or why.
- Relying on the bot's internal position bookkeeping alone for the risk module's P&L calculation, without reconciling against actual broker account state, so a fill-tracking bug can mask a real breach.
- Using passive/resting orders to force-flatten during a breach, which may not fill promptly during exactly the volatile conditions that likely caused the breach.
- Letting the out-of-band alert call sit in the halt path unguarded — the network failure that triggered the breach is often the same one that makes the alert raise, and an unhandled exception there skips the liquidation entirely.
- Reporting a failed force-flatten only to the log file, when it is the single event most in need of waking a human.
- Never having actually tested the circuit breaker trigger path end-to-end before going live, so the first real test occurs during an actual loss event.

## Verification

- In a paper/sandbox environment, engineer each defined trigger condition (breach position limit, breach daily loss limit, breach drawdown limit) individually and confirm the bot halts new orders and force-flattens as designed, with the alert firing through the independent channel.
- Confirm a position-reducing order is still accepted while halted, and that an exposure-increasing order and a position *reversal* are both still rejected.
- Feed the P&L/equity check a `NaN` and confirm it halts rather than returning healthy; construct the breaker with an out-of-range drawdown limit and confirm it raises rather than accepting it.
- Confirm the risk module's position/P&L view, when deliberately desynced from a simulated broker-side fill discrepancy, is caught by the reconciliation check rather than silently trusting stale internal state.
- Simulate an alert channel that raises and confirm the force-flatten still runs; simulate a force-flatten that raises and confirm the escalated alert fires and the failure is recorded.
- Confirm that after a triggered breach in a test environment, the bot does not resume placing orders without an explicit manual re-enable action, that a blank or unauthorized operator identity is refused, and that resuming after a drawdown halt requires an explicit high-water-mark re-baseline.
- Run `python -m unittest discover -s skills/kill-switch-and-drawdown-circuit-breakers/scripts` and confirm all tests pass.

## Related Skills

- `correlation-aware-exposure-limits`
- `order-placement-idempotency`
- `paper-to-live-promotion-checklist`
- `model-staleness-detection`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `execution-algorithm-kill-switch-integration`
- `risk-limit-calibration-against-historical-drawdowns`
- `portfolio-level-stop-loss-independent-of-strategy-stops`
