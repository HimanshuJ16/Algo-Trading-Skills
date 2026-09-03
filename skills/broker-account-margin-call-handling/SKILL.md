---
name: broker-account-margin-call-handling
description: >-
  Use when a bot trades a Reg T, portfolio or futures margin account and must act before
  the broker liquidates: tiered maintenance-margin warnings cross-checked against broker
  excess liquidity, initial-margin order gating and liquidity-aware de-leveraging.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: broker-integration
  tags: broker-integration, margin-call, risk-management, forced-liquidation-prevention, margin-utilization, liquidity-aware
  brokers_frameworks: "Interactive Brokers Reg T / Portfolio Margin; Zerodha RMS; Alpaca Margin API; CME SPAN"
  version: "3.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this whenever an algorithmic trading bot operates on a margin account (Reg T,
Portfolio Margin, or futures margin). When adverse market moves push maintenance margin
requirements toward or past account equity, brokers liquidate positions at market prices,
and they choose which ones. Acting first — cancelling resting orders, blocking new
leverage, and unwinding on your own schedule with liquidity limits — is the difference
between a controlled reduction and a forced one.

Use it to run three gates: a **tiered house ratio** as early warning, a **broker-cushion
check** that catches deficiency the house ratio can miss, and a **pre-trade veto** on
orders that would consume margin you do not have.

## When NOT to Use

- **As a substitute for the broker's own numbers.** The engine grades a snapshot you
  supply. If your feed is stale, the grade is stale. Poll `excess_liquidity` and
  `available_funds` from the broker; do not derive them from NLV.
- **As a guaranteed way to beat the broker to liquidation.** IBKR does not make margin
  calls — it liquidates in real time, without prior notice, and may do so without the
  account ever displaying a margin warning. If you are already at BREACH you may have no
  window at all. The pre-breach tiers are where this skill earns its keep.
- **To size liquidations under Portfolio Margin or SPAN without re-pricing.** The planner
  assumes margin is separable per position. Under portfolio-level regimes, closing one leg
  of a hedge can *raise* total margin — see Workflow step 5.
- **On a cash account**, or for exchange margin-shortfall *penalty* accounting, which is a
  separate settlement-side concern.

## Prerequisites

- Broker real-time account data: `net_liquidation_value`, `initial_margin`,
  `maintenance_margin`, and critically `excess_liquidity` and `available_funds`.
- A pre-trade margin impact source. Estimating it is the weak link — get it from the
  broker. At IBKR that is an `Order.whatIf = true` submission, whose `OrderState` returns
  `initMarginChange` and `maintMarginChange`.
- An open-order cancellation interface.
- Per-position `average_daily_volume`. It is a required field, not an optional one: it
  caps how much of a position the plan will sell, and a guessed value defeats the cap.
- An execution path for liquidation slices (TWAP/VWAP or equivalent).

## Workflow

1. **Evaluate account health** with `evaluate_margin_health(snapshot)`. It escalates on
   the *worse* of two signals:
   - the house ratio $M = \text{maintenance margin} / \text{NLV}$, against your configured
     tiers, and
   - **`excess_liquidity < 0`**, the broker's own cushion, which is authoritative.
   Unusable input (NaN, infinity, negative margin) raises `MarginDataError` rather than
   grading the account. A margin engine that reports NORMAL because its feed broke is
   worse than one that stops.

2. **Act on the tier.** Defaults are house policy, not regulation — set them for your book:
   - **NORMAL** ($M < 0.85$): trade normally.
   - **WARNING** ($0.85 \le M < 0.95$): block new leverage-increasing orders; alert.
   - **CRITICAL** ($0.95 \le M < 1.0$): cancel all resting orders to release reserved margin.
   - **BREACH** ($M \ge 1.0$, or negative excess liquidity): de-leverage now.
   For calibration, IBKR's own warning fires at roughly a 90.9% maintenance/ELV ratio,
   between the 85% and 95% defaults.

3. **Handle non-positive equity as a different problem.** If NLV $\le 0$ the ratio is
   undefined — the engine returns `math.inf` and the action `HALT_AND_ESCALATE` rather than
   `DE_LEVERAGE_IMMEDIATELY`. There is no target margin to de-leverage toward against
   non-positive equity; stop trading and involve a human.

4. **Gate every new order** through `guard_new_order(snapshot, margin_impact,
   initial_margin_impact=...)`. Pass `initial_margin_impact` whenever you have it: new
   positions are opened against **initial** margin (Reg T requires 50% on a long margin
   equity purchase) not maintenance margin (FINRA 4210's 25% minimum), so a
   maintenance-only projection is the weaker constraint and can pass an order the broker
   refuses. The guard returns `True` or raises — it never returns `False`, so treat any
   exception as a hard veto.

5. **Plan de-leveraging** with `plan_deleveraging(snapshot, positions)`, which orders by
   short-option tail risk, then margin density, then liquidity, and caps each slice at
   `max_participation_rate` of ADV. **Then re-price the plan before acting on it if you
   are on Portfolio Margin or SPAN** — the planner's linear "units × margin per unit" model
   does not hold when margin is computed on portfolio-level stressed loss, and unwinding a
   hedge leg can increase the requirement. Send each slice through the broker's own
   pre-trade check first.

6. **Expect the plan to be incomplete under illiquidity.** The ADV cap can leave the plan
   short of the required reduction. That is the intended trade-off — dumping the position
   would crush its price, lower NLV and trigger a second deficiency — but it means you must
   check whether the plan actually clears the deficit and escalate when it does not.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker triggers, cushion definitions and regulatory reference points: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Trusting the house ratio alone.** `maintenance_margin / NLV` uses NLV; the broker's
  cushion uses Equity with Loan Value, which excludes non-margin-eligible value and can be
  much lower. An account can read 70% and healthy while `excess_liquidity` is already
  negative and liquidation is underway.
- **Letting NaN through a threshold chain.** Every comparison against NaN is False, so a
  NaN ratio falls past `>= breach`, `>= critical` and `>= warning` into the healthy branch.
  A broken feed must fail closed.
- **Checking new orders against maintenance margin.** Positions are opened against initial
  margin, which is roughly double under Reg T.
- **Flooring NLV to avoid dividing by zero.** It manufactures a finite ratio from an
  undefined one and, if the deficit is computed from the floored value, understates the
  deficit by exactly the amount of negative equity.
- **Exempting "de-leveraging" orders without checking they de-leverage.** A bypass flag
  that is trusted unconditionally lets a margin-increasing order through every gate.
- **Unordered thresholds.** Setting warning above critical makes the WARNING tier
  unreachable — the account jumps straight to CRITICAL and the early gate never fires.
- **Passive waiting for broker liquidation**, taking market-order slippage on positions
  someone else chose.
- **Illiquidity spirals**: dumping an illiquid asset at once, crushing the bid, lowering
  NLV, and triggering a secondary call.
- **Ignoring tail risk**: unwinding long equity first while naked short options stay open
  through a volatility spike.
- **Ignoring the clock.** Where the broker force-closes intraday products on a schedule
  (Zerodha squares off MIS positions around 15:20 IST), being margin-healthy is not enough.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-account-margin-call-handling/scripts`
- Feed a snapshot with a healthy ratio but negative `excess_liquidity` and confirm the
  state is `MARGIN_CALL_BREACH` with `broker_deficiency` set — this is the failure mode
  most likely to be missed in production.
- Feed NaN in each snapshot field in turn and confirm `MarginDataError` every time; a
  `NORMAL` result from any of them is a fail-open bug.
- Check each tier at its exact boundary (85.0%, 95.0%, 100.0%), not just mid-band.
- Submit a snapshot with $M = 0.88$ and confirm `WARNING`.
- Submit an order whose `initial_margin_impact` exceeds `available_funds` and confirm the
  veto, even when the maintenance projection alone would have passed.
- Replay a historical drawdown that actually breached, and confirm the tiers would have
  fired early enough to matter given your broker's liquidation behaviour.

## Related Skills

- `kill-switch-and-drawdown-circuit-breakers`
- `options-margin-span-calculation-global`
- `correlation-aware-exposure-limits`
- `margin-utilization-circuit-breaker`
