---
name: execution-algo-twap-vwap-slicing
description: Use when a parent order is large relative to the instrument's typical
  volume and must be split into scheduled child orders tracking a TWAP or VWAP
  benchmark, including lot-aligned sizing, partial-fill and rejection rescheduling
  under an explicit catch-up policy, and side-adjusted post-trade slippage reporting.
domain: algorithmic-trading
subdomain: execution-algorithms
tags:
- execution-algorithms
- twap
- vwap
- order-slicing
- market-impact
- implementation-shortfall
- transaction-cost-analysis
brokers_frameworks:
- Interactive Brokers IBALGO (TWAP / best-efforts VWAP)
- Binance Algo Orders (TWAP, VP)
- Generic DMA / FIX child-order routing
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this when a parent order is large enough relative to the instrument's typical volume that sending it as one market or aggressive limit order would move the price against the strategy, and the goal is to track a **schedule-based benchmark**: TWAP (an even distribution across a fixed time window) or VWAP (a distribution matching the market's own volume curve).

The `execution-realistic-simulation` skill *models* that impact cost in backtests. This skill *reduces* it live, by slicing the parent order into child orders and holding an explicit accounting invariant across their lifecycle.

## When NOT to Use

- **When the alpha decays faster than the execution window.** A TWAP/VWAP schedule deliberately trades slowly and therefore carries maximum timing risk: if the signal is right, the price moves away while the schedule is still working, and the opportunity cost on the unfilled remainder swamps the impact saving. Use `arrival-price-benchmark-execution-algo` or `implementation-shortfall-minimization` instead.
- **When the order is a large share of expected window volume.** Slicing does not create liquidity. Above roughly the participation rate your own impact model says is safe, a schedule that insists on completing simply pays impact in instalments — and a VWAP schedule becomes self-referential, because your own prints are moving the benchmark you are measured against. Use `participation-of-volume-pov-execution`, which caps participation instead of promising completion, or spread the order with `multi-day-execution-schedules-for-very-large-orders`.
- **When the instrument can halt or gap mid-window.** This module has no halt state machine. Pair it with `execution-algo-behavior-under-halted-instrument`, and with `execution-algorithm-kill-switch-integration` so the parent order can be stopped as a unit.
- **When the parent quantity is only a few lots.** The scheduler warns and falls back to fewer actionable child orders — slicing finer than the instrument's lot size cannot help, and the fixed per-order cost starts to dominate.
- **As a pre-trade risk control.** The slicer schedules and accounts; it does not enforce exposure, capital, or price-collar limits. Those must sit *outside* it (`sec-rule-15c3-5-risk-controls-us`, `kill-switch-and-drawdown-circuit-breakers`) so that a scheduling bug cannot disable them.

## Prerequisites

- A defined execution window (start, interval length, interval count) and, for VWAP, an intraday volume curve with **one weight per interval** — the curve's length is the schedule's length.
- The instrument's **lot size / minimum quantity increment**, and a parent quantity that is a whole multiple of it. See `minimum-fill-size-and-lot-rounding-logic`.
- The **parent order side**. Slippage cannot be signed without it.
- A benchmark price to measure against (interval TWAP, interval VWAP, or arrival price) and, to account for an incomplete execution, a decision/end-of-window price.
- Order-placement infrastructure from `order-placement-idempotency` and `multi-broker-rate-limit-handling`. Slicing multiplies the number of placements; every child order needs the same idempotency and rate-limit discipline as a standalone order.

## Workflow

1. **Choose the benchmark before writing any scheduling code.** TWAP distributes evenly across time and ignores volume; VWAP matches the market's volume distribution. They produce different schedules and are measured against different numbers.
   - **Decision point — is volume-following actually what reduces impact here?** For an instrument with a pronounced U-shaped intraday curve, a TWAP schedule over-trades the illiquid midday and under-trades the close. For an instrument with flat or unpredictable volume, VWAP just adds estimation error to a TWAP.
   - **Decision point — do not label a flat curve "VWAP".** `ExecutionSlicer` raises rather than defaulting a missing curve to uniform weights, because a uniform-weight VWAP *is* a TWAP and reporting it as VWAP misstates the benchmark.

2. **Build the schedule in whole lots.** `allocate_lots()` normalises the weight vector, applies size jitter, and apportions by largest remainder, so `sum(target_qty) == total_qty` exactly, every clip is a non-negative whole number of lots, and fractional instruments (0.5 BTC) work.
   - **Decision point — check the warning about actionable slices.** If the parent is fewer lots than intervals, some child orders are zero-sized. That is the lot size telling you the order cannot be sliced this finely; reduce `num_intervals` rather than shipping empty clips.

3. **Recompute the VWAP schedule when live volume diverges from the historical curve.** Call `reweight_pending(observed_curve)` — it redistributes only currently-open quantity plus any unassigned residual, leaving filled and abandoned quantity alone. A VWAP algo that never re-weights is a TWAP wearing a VWAP label the moment the day stops looking like the average day.

4. **Jitter timing and size; never ship a perfectly regular pattern.** Identical clips on exact interval boundaries are trivially detectable and can be traded against. `jitter_pct` drives both size and timing, and is bounded to `[0, 0.5)` so jittered timestamps cannot reorder.
   - **Decision point — jitter is anti-gaming, not concealment.** It must not become message-rate abuse or layering; child-order flow stays in scope for surveillance (`wash-trade-and-spoofing-self-detection`, RTS 6 Art. 13).

5. **Separate fill accounting from child-order closure.** `on_child_fill()` accumulates quantity and the quantity-weighted average price only. `on_child_expired()`, `on_child_reject()`, and `on_child_cancel()` close a child order, truncate its target to what actually filled, and release the residual to the catch-up policy.
   - **Decision point — do not release residual on a partial fill.** A working child order may fill more. Releasing early and *also* leaving the original target in place is what makes the schedule sum to more than the parent order and over-execute it.
   - **Decision point — classify a rejection before rescheduling it.** A rejection is not a retry signal. Min-notional, tick-size, buying-power, and venue-halt rejections each need a different response; pushing the refused quantity into a later interval only re-submits it larger.
   - **Decision point — a request that timed out is not a rejection.** The venue may have accepted it. Reconcile against the broker before releasing that quantity, or the catch-up policy will schedule a duplicate (`order-placement-idempotency`).

6. **Pick the catch-up policy deliberately, and cap it.** `PASSIVE_CONTINUE` holds the schedule and accepts under-completion. `AGGRESSIVE_CATCHUP` redistributes pro-rata across open slices — pro-rata, so a VWAP curve is preserved rather than flattened. `GIVE_UP_AT_DEADLINE` catches up only into slices before the deadline and abandons the rest.
   - **Decision point — set `max_child_multiple` for any non-passive policy.** Uncapped catch-up lets one late clip absorb the entire residual, which is precisely the market-impact event the skill exists to prevent. Capped quantity stays unassigned and is reported unfilled rather than dumped on the market.
   - The quantity invariant `sum(target_qty) + unassigned_qty == total_qty` holds after every transition; `quantity_invariant_ok()` re-checks it.

7. **Report against the benchmark, side-adjusted, including what did not fill.** `get_execution_report(benchmark_price, final_price)` returns the achieved VWAP, `slippage_bps` on the filled portion, and — when `final_price` is supplied — `opportunity_cost_bps` on the unfilled remainder plus the combined `implementation_shortfall_bps` (Perold 1988).
   - **Decision point — a fill-only slippage number flatters every incomplete execution.** An algorithm that gives up early can post excellent slippage on the 20% it filled. Without the opportunity-cost term, that reads as good execution.

> Full step-by-step procedure with API detail: see `references/workflows.md`.
> Regulatory obligations and verified broker-algo behaviour: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Rescheduling without truncating the closed slice.** Redistributing a partial fill's residual across the remaining slices *while leaving the partly-filled slice's original target in place* makes the schedule sum to more than the parent order. A 1000-share parent with one 50-share partial fill schedules 1200 shares, and a caller driving orders from `target_qty` over-executes by 200.
- **Flattening the curve on catch-up.** Re-sizing every open slice to `remaining / count` silently converts a VWAP schedule into a TWAP one at the first partial fill — the algorithm keeps the VWAP label while no longer tracking VWAP.
- **Integer rounding as a stand-in for lot size.** Rounding child sizes to whole units zeroes out any fractional-quantity instrument (a 0.5 BTC parent becomes an all-zero schedule that executes nothing), and patching the accumulated rounding residual onto the last slice can drive it **negative** — which downstream reads as an order on the opposite side.
- **A side-blind slippage number.** `(achieved − benchmark) / benchmark` is a cost for a buy and a *saving* for a sell. Reporting it unsigned marks every good sell as bad execution and hides every bad one.
- **Treating unfilled quantity as costless.** Slippage measured only on fills ignores Perold's opportunity-cost term. A give-up policy needs the unfilled remainder priced, or it always looks cheap.
- **Silently dropping an unrecognised fill.** Ignoring a fill event for an unknown child-order id discards a position that really exists at the broker. Raise and reconcile.
- **Drawing schedule jitter from the global RNG.** It makes backtests irreproducible and perturbs every other consumer of the process-wide stream. Seed a slicer-local generator (`backtest-determinism-and-reproducibility`).
- **Retrying an order placement because the HTTP request timed out.** The venue may have accepted it before the response was lost. Reconcile first; a slicing algorithm multiplies this risk by the number of child orders.
- **Applying idempotency and rate limiting to the "parent order" concept only.** Every child order is a real order placement and needs the same discipline.
- **Never measuring achieved price against the intended benchmark**, so a poorly-performing schedule goes unnoticed indefinitely.

## Verification

- **Quantity conservation.** After any sequence of fills, expiries, rejections, and cancellations, assert `quantity_invariant_ok()` and `sum(target_qty) + unassigned_qty == total_qty`. The randomised lifecycle sweep in `scripts/test_slicer.py` checks this across all three policies.
- **Apportionment.** `allocate_lots(10, [1,1,1]) == [4,3,3]` (largest remainder, ties to the lowest index); `allocate_lots(0.5, [1]*5, lot_size=0.1)` sums to exactly `0.5`; no schedule contains a negative clip across a seed sweep.
- **Benchmark tracking.** `vwap_schedule(10000, [0.10, 0.80, 0.10], jitter_pct=0.0) == [1000, 8000, 1000]` — the schedule matches the volume curve by definition, not approximately.
- **Side-adjusted slippage.** A SELL of 100 filled at 101.0 against a benchmark of 100.0 must report `slippage_bps == -100.0` (price improvement), and at 99.0 must report `+100.0`.
- **Implementation shortfall.** Buy 1000 at benchmark 100 with 600 filled at 100.50 and a final price of 102: verify `slippage_bps == 50`, `opportunity_cost_bps == 200`, `implementation_shortfall_bps == 110` ($0.6 \times 50 + 0.4 \times 200$).
- **Determinism.** Two slicers built with the same `seed` produce identical sizes and timestamps; building one must not advance the global `random` stream.
- **Rejection path.** A deliberately rejected child order must land in `REJECTED` with its reason recorded, release exactly its residual, and leave the invariant intact — not be silently dropped or blindly resubmitted at the same size.
- **Negative checks that must raise:** unknown `slice_id`; non-finite or non-positive fill quantity or price; `num_intervals=0`; non-positive `total_qty`; `jitter_pct >= 0.5`; a VWAP curve whose length differs from `num_intervals`; a negative volume weight; a `total_qty` that is not a whole multiple of `lot_size`; a non-positive `benchmark_price`.
- Run `python scripts/test_slicer.py` and confirm a 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `multi-broker-rate-limit-handling`
- `execution-realistic-simulation`
- `arrival-price-benchmark-execution-algo`
- `implementation-shortfall-minimization`
- `participation-of-volume-pov-execution`
- `execution-algo-behavior-under-halted-instrument`
- `execution-algorithm-kill-switch-integration`
- `minimum-fill-size-and-lot-rounding-logic`
- `transaction-cost-analysis-tca-integration`
- `backtest-determinism-and-reproducibility`
