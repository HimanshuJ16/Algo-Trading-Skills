---
name: canary-releases-for-strategy-code-changes
description: Use when a new or materially changed trading strategy must reach live
  capital gradually — running it in shadow first, then routing deliberately shrunken
  live orders bounded by absolute notional limits, then full size — with attributable
  phase promotions and lot/minimum-size handling that keeps the canary's fill data
  meaningful.
domain: algorithmic-trading
subdomain: deployment-ops
tags:
- deployment-ops
- canary
- shadow-mode
- controlled-deployment
- order-scaling
brokers_frameworks: []
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when strategy code that will send live orders is **new or materially
changed**, and you want the first live orders it sends to be small enough that a defect
costs a rounding error rather than a bad day. The `StrategyCanaryRouter` sits between
signal generation and the execution gateway and decides how much of each requested
quantity is allowed through, according to the strategy's phase:

1. **SHADOW** — live data in, signals computed, **nothing routed**. The routing decision
   still reports the quantity the strategy wanted, so the caller can record a
   hypothetical fill and compare it against production behaviour later.
2. **CANARY** — live orders, scaled down by a fraction, floored to the venue's lot step,
   and bounded by an **absolute** per-order and cumulative notional limit.
3. **PRODUCTION** — full size, canary limits no longer applied.

It is a good fit when the failure you are guarding against is *your own new code*: an
inverted sign, a units error, a position-sizing regression, a signal that fires far more
often live than in backtest.

## When NOT to Use

- **The strategy is already misbehaving right now.** Demoting to SHADOW stops *new*
  orders; it does not cancel working orders or flatten a position. The primitive for
  "stop, now" is `kill-switch-and-drawdown-circuit-breakers`.
- **You need a pre-trade risk control.** This router lives in strategy space and trusts
  its own configuration. For a US broker-dealer, SEC Rule 15c3-5 requires the financial
  and regulatory controls to be automated, pre-trade, and under the broker-dealer's
  direct and exclusive control. Put this in front of that layer, never instead of it.
- **The instrument is too illiquid, or the venue's minimum too coarse, for a scaled
  order to be representative.** A 5%-scaled order that lands below one board lot is not
  a small experiment; it is a different experiment. See Common Pitfalls.
- **The change cannot be exercised at small size.** Portfolio-level logic, netting, and
  margin behaviour often do not manifest until size is real; canarying such a change
  measures nothing and creates false confidence.
- **You are replacing a running strategy that holds positions.** That is a cutover
  problem, not a sizing problem — see `blue-green-deployment-for-live-strategy-updates`.
  The two compose: cut over blue-green, then run the new version in CANARY.
- **The quantities are fractional.** This implementation is integer-quantity (shares,
  lots, contracts). Crypto step sizes such as 0.001 BTC require `Decimal` arithmetic end
  to end; do not cast to `int` to reuse the class.

## Prerequisites

- An execution path that consults the router **before every submission** and acts on the
  returned `RoutingDecision` — not a cached phase value read once at startup.
- Idempotent client order IDs (`order-placement-idempotency`). Canary retries are exactly
  where a rescaled duplicate would appear.
- Per-instrument venue rules: lot step, minimum quantity (these are two different things
  — Binance publishes `stepSize` and `minQty` separately), and minimum notional if the
  venue enforces one.
- Objective, written promotion criteria fixed *before* the canary starts — sample size,
  slippage, rejection rate, live-vs-backtest divergence — plus who decides. See
  `assets/checklist.md`.
- A named person to authorise each phase change. For EU/UK investment firms in scope of
  RTS 6 this is a regulatory obligation, not a nicety; see `references/standards.md`.
- Shadow-mode signal storage that is physically separate from live PnL and position
  records, so hypothetical fills can never be mistaken for real ones.
- A working kill switch, independent of this router.

## Workflow

1. **Register in SHADOW.** `register_strategy(StrategyRegistration(...))` with the venue's
   real lot step, minimum quantity and minimum notional. Registering straight into CANARY
   or PRODUCTION is allowed (an already-live strategy must be describable) but is logged
   as a warning — it skips the only phase that cannot lose money.
2. **Run shadow for a period chosen from this strategy's own behaviour**, not a default
   two weeks: long enough to cover the regimes and session events the strategy trades
   through, and to accumulate enough signals for the promotion criteria to mean anything.
   Record every `SUPPRESSED` decision's `requested_quantity` as a hypothetical fill.
3. **Compare shadow signals against the backtest that justified the change.** A shadow
   run that fires at a different rate, or on different instruments, than the backtest is
   telling you the change is not what you thought — investigate before promoting, because
   CANARY will not surface it any more clearly, only more expensively.
4. **Size the canary in money, not only in percent.** Set `canary_scale_factor` *and*
   `max_canary_order_notional` *and* `canary_notional_budget`. A percentage alone is not
   a cap: 5% of a runaway 1,000,000-share order is still 50,000 shares.
5. **Promote to CANARY** with `set_phase(strategy_id, DeploymentPhase.CANARY,
   authorised_by=...)`. A SHADOW → PRODUCTION jump is refused unless explicitly forced,
   and the refusal is recorded.
6. **Act on the decision, do not just check for `None`.** `SUPPRESSED` is expected;
   `REJECTED` is not, and its `binding_constraint` tells you which one you are looking at
   — `registration` (an unknown strategy reached the order path: alert loudly),
   `signal_validation`, `min_quantity`, `min_notional`, or `canary_notional_budget`.
7. **Watch which constraint is binding.** If every canary order comes back with
   `binding_constraint == "max_canary_order_notional"`, the scale factor is no longer
   what is controlling your exposure, and the canary is sampling only the small tail of
   the strategy's order distribution.
8. **Credit back exposure that never happened.** The router counts *submitted* notional;
   it never sees fills. When the venue rejects an order or you cancel it unfilled, call
   `release_notional()`. Do not call it for filled orders.
9. **Judge the canary on execution reality, not PnL.** At 5% size, PnL is noise. Rejection
   rates, order-state transitions, latency, fee treatment, borrow availability, and
   slippage versus the model are what a canary actually measures.
10. **Promote to PRODUCTION** only against the pre-written criteria, with an authoriser
    recorded — and keep monitoring intensively through the first full session at size.
    Demotion needs no ceremony and no `force`: reducing exposure is never blocked.
11. **Export `phase_history` after each promotion** and retain it with your change
    records. Refused and forced transitions are in there deliberately.

> Full procedure, including what to measure in each phase: see `references/workflows.md`.
> Regulatory touchpoints (EU RTS 6, US SEC/FINRA) and engineering standards, with their
> jurisdictional limits: see `references/standards.md`.
> Printable promotion sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating the scale factor as an exposure limit.** It is a *ratio*. If a defect makes
  the strategy request 100× its normal size, the canary sends 100× its normal canary
  size. Only an absolute notional cap bounds the damage.
- **Scaling the order in place.** Rewriting `signal.quantity` on the caller's object
  corrupts the strategy's own order record, and a retry that re-enters the router scales
  the already-scaled order again — 5% of 5% is 0.25%, and nobody notices until the canary
  produces no fills at all. `route()` returns a new object for this reason.
- **Reading `None` as "shadow mode".** A `None` from the legacy `route_order()` wrapper is
  equally an unregistered strategy, an invalid signal, a sub-lot drop, or an exhausted
  budget. An unknown strategy reaching the order path is a serious event; it must not be
  indistinguishable from routine shadow suppression.
- **Assuming the scaled quantity is executable.** Venue minimums bite exactly here. On
  HKEX, a scaled order below one board lot cannot enter the auto-matching order book at
  all — odd lots go to a separate semi-manual special-lot market with its own liquidity
  and pricing, so any slippage you measure there is not the slippage you will get at size.
  On a crypto venue, an order that clears `minQty` and `stepSize` can still be rejected by
  the `NOTIONAL` filter's `minNotional`. In US equities the scaled order will usually
  execute as an odd lot, so the failure mode is not rejection but unrepresentative
  execution quality.
- **Rounding the scaled quantity up.** Rounding to the nearest lot can *exceed* the risk
  budget the canary exists to enforce. Floor, always — and drop the order when the floor
  is zero rather than sending one token lot.
- **Trusting `int(quantity * factor)`.** `int(100 * 0.29)` is 28, not 29: binary floating
  point makes the product 28.999999999999996. Do the scaling in `Decimal`.
- **Canarying an illiquid instrument.** One share in a name that trades twice an hour
  yields no usable slippage or fill data, while still carrying operational risk. Either
  canary on the liquid subset of the universe or accept that the phase is a smoke test,
  not a measurement.
- **Reading canary PnL as evidence.** At 5% size, a profitable canary week is a sample of
  one scaled by 0.05. Promotion criteria built on canary PnL promote noise.
- **Leaving the canary running indefinitely.** An unpromoted canary is a strategy nobody
  owns, consuming a notional budget and producing data nobody reads. Set an end date at
  the start, and demote or promote at it.
- **Letting shadow fills leak into live reporting.** Hypothetical fills in the same table
  as real ones corrupt PnL, tax lots, and every downstream risk number. Separate the
  store, not just a boolean column.
- **Promoting on elapsed time.** "Two weeks in canary" is not a criterion; a fortnight of
  a quiet market exercises less than one volatile hour. Promote on samples and behaviour.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/canary-releases-for-strategy-code-changes/scripts`
- Submit a 1,000-share order under CANARY at a 10% scale and confirm the routed decision
  is `SCALED` with `routed_quantity == 100`, and that the *input* signal object still
  reports 1,000.
- Route the same signal object twice and confirm both decisions are identical — a
  re-routed order must not be scaled twice.
- Assert at the gateway, not only in config, that a strategy in SHADOW produces **zero**
  outbound order messages over a full session.
- Set `max_canary_order_notional` deliberately low in staging and confirm the routed
  quantity is reduced to fit and the decision names `max_canary_order_notional` as the
  binding constraint.
- Exhaust `canary_notional_budget` in staging and confirm subsequent orders are `REJECTED`
  rather than silently dropped, and that `release_notional()` restores headroom.
- Attempt a SHADOW → PRODUCTION promotion and confirm it is refused, and that the refusal
  appears in `phase_history` with the attempted authoriser.
- Reconcile broker-side positions after the first canary session: the router bounds what
  it is asked to send, not what the venue actually filled.

## Related Skills

- `blue-green-deployment-for-live-strategy-updates`
- `kill-switch-and-drawdown-circuit-breakers`
- `order-placement-idempotency`
- `paper-to-live-promotion-checklist`
- `incremental-capital-deployment-for-new-strategies`
- `automated-rollback-triggers-on-anomaly-detection`
- `backtest-vs-live-performance-divergence-tracking`
- `minimum-fill-size-and-lot-rounding-logic`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
