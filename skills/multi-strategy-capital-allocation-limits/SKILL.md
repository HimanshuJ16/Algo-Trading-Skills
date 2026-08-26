---
name: multi-strategy-capital-allocation-limits
description: Use when multiple concurrent strategies share a single trading account
  to allocate and cap capital per strategy, preventing any single strategy from consuming
  disproportionate capital and ensuring total allocated capital never exceeds available
  account equity.
domain: algorithmic-trading
subdomain: risk-management
tags:
- risk-management
- capital-allocation
- multi-strategy
- portfolio-management
- position-limits
brokers_frameworks:
- Custom Portfolio Engine
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill whenever multiple algorithmic strategies run concurrently on a single brokerage
account. Without explicit capital allocation limits, a single aggressive strategy can consume
all available margin, starving other strategies and creating concentrated risk. This skill
enforces per-strategy capital caps, an account-level ceiling that protects the cash reserve,
tracks real-time utilization, and blocks new orders when a strategy exceeds its allocation.

## When NOT to Use

- **As the only pre-trade control.** A capital cap bounds *notional deployed per strategy*. It
  says nothing about single-order size, price sanity, message rates, drawdown, or leverage. SEC
  Rule 15c3-5(c)(1) and MiFID II RTS 6 Art. 15 both expect a *suite* of controls; compose this
  with the skills under Related Skills.
- **As a margin or buying-power engine.** Gross notional is deliberately not netted and not
  offset by broker margin rules (portfolio margin, SPAN, cross-margin). These numbers will not
  match broker buying power and must not drive collateral decisions.
- **For risk budgeting.** Equal notional is not equal risk. A 20% allocation to a 60%-vol
  strategy is not comparable to 20% in a 5%-vol strategy — use `risk-parity-allocation-across-strategies`
  when the budget should be denominated in risk rather than capital.
- **For a single strategy in a dedicated account,** where the account balance is already the cap.
- **When exposure cannot be attributed to a strategy.** If two strategies trade the same symbol
  in one account and fills are not tagged, per-strategy exposure is a guess and the caps are
  decorative. Fix attribution first.

## Prerequisites

- Defined strategy roster with target capital allocation percentages summing to at most
  `1 - cash_reserve_pct`.
- Real-time account equity / NAV feed. NAV is supplied per call — the module holds no NAV and
  cannot detect staleness on your behalf.
- Per-strategy position tracking reporting **gross** notional (sum of absolute position values),
  marked to market, not cost basis.
- Client order ids, so capital reservations survive retries idempotently.

## Workflow

1. **Define the strategy allocation table**:
   - Assign each strategy a maximum capital allocation as a percentage of NAV.
   - The budget $\sum_s \text{alloc}_s \le 1 - \text{cash\_reserve}$ is enforced at
     registration; an over-allocating call raises and is not applied, so the roster can never
     sit in an invalid state waiting for someone to remember to validate it.
   - Changing a live strategy's cap goes through `update_allocation()`, never through
     re-registration: re-registering must not be able to reset tracked exposure to zero.

2. **Track real-time utilization on gross, marked-to-market exposure**:
   - For each strategy $s$: $\text{utilization}_s = \text{exposure}_s / (\text{alloc}_s \cdot \text{NAV})$.
   - Exposure is gross: a $50k long plus a $50k short consumes $100k, not $0. Feeding signed net
     exposure hands a market-neutral strategy unbounded headroom.

3. **Pre-trade validation, counting in-flight orders**:
   - Before placing an order for strategy $s$, verify:
     $$\text{exposure}_s + \text{in\_flight}_s + \text{order\_value} \le \text{alloc}_s \cdot \text{NAV}$$
   - The `in_flight` term is not optional. Two orders that each fit the cap will both pass a
     settled-exposure-only check, because neither has filled yet when the second is evaluated.
     RTS 6 Art. 15(2) states the point directly: a firm "shall immediately include all orders
     sent to a trading venue into the calculation of the pre-trade limits."
   - Use `reserve(strategy, value, nav, order_id)` on the live path — it decides and claims the
     capital under one lock. `check_order()` is an advisory preview only and is racy by
     construction; treating its approval as permission to trade reintroduces the double-spend.
   - Reject on any non-finite or non-positive input. A NaN order value or NaN NAV makes
     `projected > cap` evaluate **false**, which silently *approves* the order — the failure mode
     is an approval, not an exception.
   - Exposure-reducing orders (negative change in gross notional) are approved even when the
     strategy is over cap. A de-risking order must never be vetoed by a risk control.

4. **Settle or release every reservation**:
   - Fill → `settle_reservation(order_id, filled_usd)`; partial fill with the remainder still
     working → `close=False`; reject/cancel/expiry → `release_reservation(order_id)`.
   - A leaked reservation permanently sterilises capital. Reconcile open reservations against
     the broker's open-order book on every heartbeat, and on restart rebuild from the broker,
     not from local memory.

5. **Enforce the account-level ceiling**:
   - Independently verify total committed capital $\le (1 - \text{cash\_reserve}) \cdot \text{NAV}$.
     Per-strategy caps stop bounding the total the moment mark-to-market drift lifts one
     strategy above its own cap, and the cash reserve has to survive that drift.

6. **Rebalance on NAV changes**:
   - Caps are recomputed from the NAV passed to each call, so a NAV drop tightens every cap
     immediately. Expect existing positions to sit over cap after a drawdown: that is a
     remediation signal (`is_over_cap` in the utilization report), not an error.

> Full procedure: see `references/workflows.md`.
> Standards: see `references/standards.md`.
> Checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Ignoring In-Flight Orders**: Checking only settled exposure approves order B while order A
  is still working, and the pair breaches the cap on fill. Reserve capital at submission.
- **Advisory Check as Permission**: Calling `check_order()` and then placing the order leaves a
  window in which another thread claims the same headroom. Use `reserve()`.
- **NaN Fails Open**: A missing mark or a divide-by-zero upstream yields NaN exposure or NaN NAV;
  every `>` comparison against NaN is false, so the "hard block" approves everything. Validate
  finiteness explicitly and fail closed.
- **Netting Longs Against Shorts**: Net exposure lets a market-neutral strategy claim it uses no
  capital. Cap on gross notional.
- **Config Reload Wipes Exposure**: Re-registering strategies on a config reload resets tracked
  exposure to zero, unbinding every cap until the next mark-to-market cycle repopulates it.
- **Over-Allocation**: Sum of strategy allocations exceeding 100% creates implicit leverage —
  and validation that must be called explicitly is validation that eventually is not.
- **Per-Strategy Caps Without an Account Cap**: Sub-limits that sum to the aggregate limit only
  hold while each sub-limit holds. SEC staff guidance on Rule 15c3-5 makes the same point about
  venue sub-limits: they must together not exceed the aggregate limit.
- **Stale NAV**: Using yesterday's NAV for today's caps during volatile markets. NAV is an input
  here, so staleness is the caller's responsibility — timestamp it and refuse to trade on a
  stale value.
- **Ignoring Unrealized P&L**: Measuring utilization by cost basis instead of mark-to-market value.
- **Silent Limit Overrides**: Raising a cap intraday to release a blocked order is legitimate but
  must be deliberate, authorised and logged (RTS 6 Art. 15(6) permits it only "on a temporary
  basis and in exceptional circumstances", verified by risk management). Never let strategy code
  widen its own limit.

## Verification

- Create 3 strategies with 40%, 30%, 20% allocations and verify cap enforcement.
- Attempt to exceed a strategy's allocation and confirm order rejection with
  `rejection_code == "STRATEGY_CAP"`.
- Reserve an order that consumes the remaining headroom, then confirm a second order for the
  same strategy is rejected *before* the first one fills, and is approved again after
  `release_reservation()`.
- Submit `float("nan")` as the order value and as the NAV; confirm both are rejected with
  `rejection_code == "INVALID_INPUT"`.
- Drift one strategy above its own cap via `update_exposure()` and confirm an order from a
  different strategy that still has headroom is blocked with `rejection_code == "PORTFOLIO_CAP"`.
- Confirm re-registering a live strategy raises rather than zeroing its tracked exposure.
- Run `python scripts/test_capital_allocator.py` (or
  `python -m unittest discover -s skills/multi-strategy-capital-allocation-limits/scripts`)
  and confirm 100% pass rate.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `value-at-risk-var-live-monitoring`
- `correlation-aware-exposure-limits`
- `order-placement-idempotency`
- `risk-control-bypass-audit-logging`
