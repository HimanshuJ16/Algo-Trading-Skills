---
name: margin-utilization-circuit-breaker
description: Use when a bot trades on margin and needs a latching, strategy-independent
  circuit breaker on margin utilization that halts exposure-increasing orders at a
  house budget well below the broker's liquidation point, permits only margin-releasing
  orders while halted, and stays halted until an audited human re-arm.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- margin
- circuit-breaker
- leverage
- margin-call-prevention
brokers_frameworks:
- Custom Risk Engine
- Interactive Brokers
- Alpaca
- Zerodha Kite Connect
- MetaTrader 5
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this for any bot placing live orders against a margin account. P&L-based drawdown
breakers only fire after losses materialise; margin utilization can spike on positions
that are *winning*, because the requirement moves with volatility and with the clearing
house's schedule, not only with your P&L. CME Clearing revises performance bond
requirements by numbered clearing advisory notice with a stated effective date, so the
same unchanged position can cost materially more margin at the next session's open.

Its trip metric is a **margin budget you choose**:

$$\text{util} = \frac{\text{used\_margin}}{\text{account\_equity}}$$

and its job is to stop trading at that budget, **latch**, and refuse to resume until a
named human re-arms it. That latch is the point. A stateless "is the ratio above 0.8 right
now" check adds nothing over `broker-account-margin-call-handling`, which already grades
broker snapshots more thoroughly.

## When NOT to Use

- **As your margin-call handler.** This breaker prevents you *reaching* a call. Grading a
  broker snapshot against its authoritative `excess_liquidity` cushion, and planning a
  liquidity-aware unwind once a call is close, belong to
  `broker-account-margin-call-handling`. Run both; they answer different questions.
- **As a source of margin requirements.** Nothing here computes SPAN, portfolio margin,
  cross-margin offsets or a liquidation price. It consumes a requirement your broker
  reports. See `options-margin-span-calculation-global` and
  `cross-margining-across-asset-classes`.
- **As a regulatory threshold.** The numbers you configure are *your* risk policy. MiFID II
  RTS 6 Art. 15(4) requires an investment firm to *set* market and credit risk limits
  against its capital base and risk tolerance, and Art. 15(5) requires automatic blocking
  of orders that would compromise them — neither fixes a value. SEC Rule 15c3-5 binds
  broker-dealers with market access, not their customers. Do not present 60%/80% to an
  auditor as a regulatory minimum.
- **As an exposure cap.** Notional leverage against equity is
  `leverage-limit-enforcement-across-instruments`. A book inside a 3.0× gross cap can still
  be one tick from a margin call, and vice versa.
- **In backtests**, unless you are deliberately modelling the halt. A latching breaker
  inside a backtest truncates the equity curve and flatters the result.

## Prerequisites

- A live margin figure and account equity from the broker, **with the timestamp they were
  read at** — pass it as `as_of` and set `max_data_age_seconds`. A margin feed that has
  silently stalled is indistinguishable from a calm market.
- An explicit decision about **which requirement** `used_margin` carries. Declare it via
  `basis=MarginBasis.MAINTENANCE` or `MarginBasis.INITIAL`. Reg T (12 CFR 220.12(a)) sets
  initial margin at 50% of current market value; FINRA Rule 4210(c) sets maintenance at
  25% for long positions — roughly a factor of two, so the same threshold is a very
  different budget on each basis.
- A **pre-trade margin impact** for each order, signed: positive when the order consumes
  margin, negative when it releases it. At IBKR this comes from an `Order.whatIf = true`
  submission, whose `OrderState` carries `initMarginChange` and `maintMarginChange`.
- A named human, reachable out of hours, who owns the re-arm decision.

## Workflow

1. **Normalise the broker's fields before they reach the breaker, and check the direction
   of the ratio.** This module's utilization runs *up* toward danger. MetaTrader's margin
   level (`equity / margin × 100`) runs *down* toward it, with stop-outs at values like
   50% or 20%. Feed an MT5 `ACCOUNT_MARGIN_LEVEL` in as `used_margin` and a distressed
   account reports a tiny utilization while every order is approved. Convert at the adapter
   boundary. Watch the field semantics too: Alpaca exposes `initial_margin` and
   `maintenance_margin` separately alongside `equity`, so the basis is a free choice;
   Zerodha's Kite Connect `utilised.debits` bundles realised and unrealised M2M in with
   SPAN and exposure margin and exposes no maintenance figure at all, so it is not a pure
   requirement and its `net` is a cash balance rather than equity.

2. **Set the budget against the broker's liquidation point, not against a round number.**
   IBKR liquidates when Excess Liquidity (Equity with Loan Value − Maintenance Margin, in
   the securities segment) goes negative — maintenance utilization of 1.0 — and it does not
   make margin calls: it liquidates in real time, without prior notice, and states that an
   account moving rapidly from a greater-than-10% cushion into violation may be liquidated
   without ever showing a warning. An 80% maintenance budget leaves a 20% cushion, which is
   a policy choice about how fast your market can gap, not a safe constant.

3. **Poll and grade** with `evaluate_margin(used_margin, equity, as_of=…)`. Unusable input
   — NaN, infinity, negative margin, a stale or naive timestamp — raises `MarginDataError`
   rather than returning a grade. Treat the exception as a halt condition. A breaker that
   reports NORMAL because its feed broke is worse than one that stops.

4. **Gate every order** through `check_order(used, equity, additional_margin_required,
   as_of=…)`, which projects the requirement forward before deciding. This path never
   raises: bad input returns `approved=False` with `is_data_error=True`, so a broken feed
   fails closed instead of becoming an approval.

5. **Let de-risking through, and verify that it de-risks.** While halted, an order is
   approved only if it *strictly reduces* the projected requirement. A partial reduction
   that leaves the account still over the limit is approved — it moves in the right
   direction. A margin-neutral order or a reversal is not: equality is not reduction. The
   test is arithmetic on the projected requirement, never a caller-supplied "this is a
   closing order" flag.

6. **The halt latches.** Once tripped it stays tripped even after utilization falls back,
   because the condition that caused it — a volatility regime, a margin hike, a sizing bug
   — is usually still present. Auto-recovery just delays the same breach.

7. **Re-arm through `re_arm(operator, reason, used_margin=…, account_equity=…)`, and check
   the boolean.** It is refused, and the refusal recorded, on a blank operator, a blank
   reason, unusable input, or utilization still above `re_arm_threshold`. That last one
   matters: re-arming at the trip level re-trips on the very next poll, and an operator who
   watches trading resume for one evaluation reasonably concludes the problem is fixed.
   Every attempt, granted and refused, lands in `re_arm_log`. RTS 6 Art. 15(6) requires
   overrides of a firm's own pre-trade blocks to be temporary, exceptional, verified by the
   risk management function and authorised by a designated individual.

8. **Keep the breaker structurally separate from strategy code**, and serialise
   check-then-place at the caller. The latch is lock-guarded, but nothing stops two threads
   each passing the gate and then both placing an order against the same headroom.

> Full procedure with broker field mappings: see `references/workflows.md`.
> Thresholds, sources and jurisdictional limits: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Passing `80` for "80%".** Nothing raises, `utilization >= 80` is never true, and the
  breaker is disabled for the life of the process with no outward signal. The constructor
  now rejects any threshold outside $(0, 1]$ — this was the single highest-value guard added.
- **Letting a NaN walk the threshold chain.** Every comparison against NaN is False, so a
  NaN utilization falls past the hard stop, past the warning, and lands in the healthy
  branch. One broken field silences the whole breaker at once.
- **Mixing the initial and maintenance bases.** They are roughly 2:1 apart under Reg T
  versus FINRA 4210. A book you believe is at 80% of a maintenance budget may be at 40%, so
  the breaker never fires; or the reverse, and it fires constantly and you widen it.
- **Inverting the ratio.** MetaTrader's margin level is `equity / margin` — the reciprocal.
  A "utilization" of 900% read off an MT5 account is a *healthy* account; 45% is a stop-out
  away.
- **Blocking every order while halted**, including the closing orders the halt is demanding.
  The breaker vetoes its own remedy and the positions it exists to shrink stay open.
- **Trusting a `reduce_only`-style flag instead of checking the projected requirement.** A
  bypass that is believed unconditionally lets a margin-increasing order through every gate.
- **Grading a cached snapshot.** The moment a feed is most likely to stall is the fast move
  during which the number matters most. Without `max_data_age_seconds` and an `as_of`, a
  frozen value grades as a calm market forever.
- **Clamping the shortfall to zero.** Reporting `available_margin` as 0.0 when the
  requirement exceeds equity throws away the one number the operator needs — how far short
  they are. Read `margin_deficit`.
- **Treating non-positive equity as utilization of exactly 1.0.** Against a debit balance
  the ratio is undefined, not "exactly fully used", and the capital needed to cover exceeds
  `used_margin` by the size of the debit. This module reports `math.inf` and a
  cover-the-requirement `margin_deficit`.
- **Logging the halt on every poll.** Re-emitting CRITICAL thousands of times buries the
  transition that mattered; and logging a *projected* rejection as though the account had
  halted puts a false halt into the audit trail for an account that was fine.
- **Assuming the requirement only moves when you trade.** Clearing houses raise performance
  bonds in volatile periods, effective at a stated session boundary, and brokers add house
  margin on top. Utilization can jump overnight on an untouched book.
- **Auto-resuming after a cooldown** instead of requiring a human, or re-arming while still
  over the line and reading the one-evaluation reprieve as a fix.

## Verification

- Run `python -m unittest discover -s skills/margin-utilization-circuit-breaker/scripts`
  and confirm all tests pass.
- Construct the breaker with `warning_threshold=60, hard_stop_threshold=80` and confirm it
  raises `MarginDataError` rather than accepting a disabled breaker.
- Feed NaN into `used_margin`, `account_equity` and `additional_margin_required` in turn:
  `evaluate_margin` must raise, and `check_order` must return `approved=False` with
  `is_data_error=True`. An approval from any of them is a fail-open bug.
- Check each threshold at its exact boundary (0.60 and 0.80), not only mid-band.
- Trip the breaker at 90%, drop utilization to 10%, and confirm orders are *still* blocked
  and `is_latched` is `True`.
- While halted, confirm a margin-releasing order is approved with `risk_reducing=True`
  even when the projection stays above the limit, and that a margin-neutral order
  (`additional_margin_required=0`) is still rejected.
- Confirm `re_arm` returns `False` for a blank operator, a blank reason, a stale snapshot,
  and utilization above `re_arm_threshold` — and that each refusal appears in `re_arm_log`.
- Age a snapshot past `max_data_age_seconds` and confirm the veto; repeat with a naive
  (timezone-less) timestamp and with one dated in the future.
- Replay a historical volatility event — including a session where the clearing house
  raised requirements — and confirm the breaker would have tripped early enough to matter
  given your broker's liquidation behaviour.

## Related Skills

- `broker-account-margin-call-handling`
- `kill-switch-and-drawdown-circuit-breakers`
- `leverage-limit-enforcement-across-instruments`
- `multi-strategy-capital-allocation-limits`
- `options-margin-span-calculation-global`
- `cross-margining-across-asset-classes`
- `value-at-risk-var-live-monitoring`
- `risk-control-bypass-audit-logging`
