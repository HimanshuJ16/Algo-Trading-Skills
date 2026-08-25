---
name: execution-algo-behavior-under-halted-instrument
description: >-
  Use when a parent TWAP/VWAP/POV algo's instrument stops trading continuously
  (LULD pause, news halt, volatility interruption) — issues child-order cancel
  requests and tracks them to venue acknowledgement rather than assuming
  success, suppresses slicing through the reopening auction, and re-benchmarks
  the unexecuted residual over the recovered horizon under a catch-up rate cap.
domain: Execution Algorithms
subdomain: Execution Safety & State Machine
tags:
- execution-algo
- trading-halt
- luld
- limit-state
- twap
- vwap
- cancel-acknowledgement
- reopening-auction
brokers_frameworks:
- Nasdaq/Cboe LULD
- CME Globex Market States
- Eurex T7 Volatility Interruption
- Python Dataclasses
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill in institutional execution algorithms (TWAP, VWAP, POV, Implementation Shortfall), Smart Order Routers, and automated execution risk engines that must survive their instrument going non-continuous mid-schedule.

Two facts make the naive implementation of this dangerous, and both are why the engine is shaped the way it is:

- **Resting orders do not disappear when trading stops.** In US equities they persist through a LULD pause and are *eligible interest for the reopening auction* — so an algo with stale limit orders on the book is quoting into the single most volatile print of the day. Cboe additionally cancels-or-preserves resting orders based on a **port-level configuration setting**, so what happens to your book is a function of how your session was provisioned, not something you can assume.
- **A cancel is a request, not a state change.** The order stays live until the venue acknowledges, and it can fill in the meantime. Worse, some phases refuse cancels outright: CME Globex accepts cancels in `Pause` but forbids them in `Pre-Open - No Cancel`, and Eurex T7 holds deletions as *pending* during the freeze phase of an extended volatility interruption.

The engine therefore tracks `RESTING → PENDING_CANCEL → CANCELLED/FILLED` and reports `orders_still_live_count` — the number of child orders that can *actually* still execute. **Gate downstream risk logic on that field, not on `cancelled_child_orders_count`.**

## When NOT to Use

- **As a kill switch.** This reacts to a *venue* state change for one instrument. It does not react to your own runaway algo, P&L breach, or connectivity loss, and it never mass-cancels across venues. See `execution-algorithm-kill-switch-integration` (MiFID II RTS 6 Art. 12 kill functionality).
- **As the portfolio-level halt response.** This decides what one parent order does. Hedging a halted position, MWCB handling, and fair-value auction participation belong to `black-swan-playbook-for-halted-markets`.
- **Without mapping your venue's states first.** The status tokens here are normalised internal values, not wire values. LULD is US-NMS-specific; CME Globex, Eurex T7, JPX and NSE/BSE use materially different phase models and different cancel permissions. Porting the token set without re-deriving it from the venue rulebook will silently mis-handle the cancel-forbidden phases, which is the one case that costs money.
- **As an order gateway.** The engine mutates algo state and emits intent. It does not send FIX, does not retry, and does not know whether your `CancelRequest` reached the venue — you must feed acknowledgements back in via `apply_cancel_ack`.
- **On a venue whose halts are shorter than your slice interval.** A CME Velocity Logic event pauses matching for roughly 5–10 seconds; re-benchmarking a multi-hour schedule around it is noise. Reserve this machinery for outages long relative to the slice period (a 5-minute LULD pause, a news halt).

## Prerequisites

- Parent algo state (`parent_algo_id`, `symbol`, `algo_type`, `total_target_qty`, `executed_qty`, `algo_state`).
- Active child orders (`child_ord_id`, `venue_id`, `side`, `price`, `order_qty`) with their true lifecycle status.
- A normalised instrument status feed mapped onto the engine's token set (`TRADING_CONTINUOUS`, `LIMIT_STATE`, `STRADDLE_STATE`, `HALTED_*`, `AUCTION_REOPENING`, `PRE_OPEN_NO_CANCEL`, `CLOSE_FINAL`, …).
- An **explicit `event_ts`** on every call. The engine reads no wall clock, so replay and live produce identical decisions.
- For the backlog guard: `schedule_start_ts`, `schedule_end_ts`, and ideally `hard_end_ts` (session close). Omitting them is legal for volume-driven algos — the engine then reports `rebenchmark_applied=False` rather than inventing a rate.

## Workflow

1. **Classify the instrument status — exhaustively, and fail safe.**
   - Match against the known token set. **Do not prefix-match on `"HALTED"`**: an unmapped or newly-added feed token must not fall through to "carry on trading".
   - An unrecognised status suspends slicing and escalates, but deliberately issues **no cancels** — cancelling on a malformed token is itself an unrequested trading action.
   - **Decision point — band stress is not a halt.** In `LIMIT_STATE`/`STRADDLE_STATE` the instrument is still trading, but marketable orders are rejected at the band. Keep slicing passively; stop sending marketable children (`marketable_child_orders_permitted=False`). A limit state persisting 15 seconds escalates to a pause, so treat it as your warning.

2. **On a halt, issue cancel requests — and do not believe them.**
   - Move each `RESTING` child to `PENDING_CANCEL` and record `cancel_requests_issued`. Never re-request a cancel that is already pending; duplicate cancels are rejected by most venues and inflate your order-to-trade ratio.
   - **Decision point — check `cancel_permitted` first.** In a no-cancel phase the count of live orders is not a to-do list, it is committed exposure into the auction print. Escalate; there is no cancel to send.
   - The halt clock is stamped once. A duplicate halt message must not restamp it, or the measured outage — and therefore the recovered horizon — is understated.

3. **Reconcile acknowledgements before trusting any exposure number.**
   - `CANCELLED` retires the order (optionally with a partial fill that printed first). `FILLED` means the order executed before the cancel landed — `executed_qty` increases. `CANCEL_REJECTED` returns the order to `RESTING`: **it is still live**, and this is the case operators most often miss.

4. **Hold through price discovery.**
   - `AUCTION_REOPENING` / `PRE_OPEN` transitions to `REBALANCING_POST_RESUMPTION` with slicing **off**. There is no continuous matching in an auction; dispatching continuous child slices into it is not participation, it is uncontrolled exposure to the crossing price.
   - The halt clock keeps running through the auction — the instrument is not continuously tradable yet.

5. **Re-benchmark on `TRADING_CONTINUOUS`, under a rate cap.**
   - Freezing the slice timer means giving back the horizon lost to the halt: `new_end = schedule_end + halt_duration`, clamped at `hard_end_ts`. The session close cannot be extended.
   - $\text{required rate} = Q_{\text{remaining}} / (\text{new end} - t_{\text{now}})$, compared with the original $Q_{\text{total}} / (\text{end} - \text{start})$.
   - **Decision point — if `required_rate > max_rate_multiple × original_rate`, do not resume.** The engine holds in `REBALANCING_POST_RESUMPTION` with slicing off and `rebenchmark_breach=True`. Working a large residual into a thin reopening is how an algo triggers the *secondary* LULD pause; that call belongs to a human or to the parent strategy, not to a catch-up formula.
   - The schedule is not extended while the guard is tripped, so repeated resumption messages yield a stable required rate rather than a drifting one.

6. **Emit `AlgoHaltAuditReport`** for the compliance record on every transition — including the no-op ones.

> Full procedure: see `references/workflows.md`.
> Standards and venue citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Marking orders cancelled locally the moment you send the cancel.** This is the defect this engine exists to prevent. The desk reads `cancelled_child_orders_count` as "flat", while the orders are still resting and eligible for the reopening auction. Confirmed cancels and requested cancels are different numbers; the engine reports both, plus `orders_still_live_count`.
- **Assuming the venue will accept the cancel.** CME `Pre-Open - No Cancel` refuses it; Eurex's extended-VI freeze queues it as pending. These phases occur immediately *before* the auction match — exactly when a stale order is most expensive.
- **Retrying a cancel that is already `PENDING_CANCEL`.** It does not make the order more cancelled; it draws a reject and pollutes your order-to-trade ratio.
- **Missing the fill that raced the cancel.** If the order printed before the cancel landed, `executed_qty` is stale, and every downstream number — remaining quantity, participation rate, position — is wrong. Reconcile from acknowledgements, not from intent.
- **Resuming continuous slicing at `AUCTION_REOPENING`.** The reopening is an auction, not continuous trading. Treat the auction as a distinct phase with its own order types.
- **Dumping the accumulated backlog on resumption.** Catching up "on schedule" concentrates the missed slices into the thinnest, most volatile book of the session and can trigger a secondary pause. Cap the catch-up rate and escalate when the cap binds.
- **Letting slice timers run during the halt.** Decrementing the horizon while trading is paused silently inflates the required participation rate for the remainder of the schedule.
- **Extending the schedule past the close.** A halt near the end of the session cannot be recovered by pushing the deadline out — hence `hard_end_ts`. If a LULD pause runs into the close, Nasdaq cancels DAY/LOC/MOC/IO orders back to the entering firm when no halt cross can occur before 16:00, so the parent ends the day short of target with its orders killed by the venue rather than by you.
- **Prefix-matching the status string.** `startswith("HALTED")` misses `LIMIT_STATE`, `PRE_OPEN_NO_CANCEL` and every venue-specific phase, and silently classifies unknown tokens as "keep trading".
- **Silently clamping over-execution.** `max(0, target − executed)` turns a fill-reconciliation break into a clean-looking zero. The engine flags `reconciliation_breach` and forces slicing off instead.

## Verification

- Instantiate `ExecutionAlgoHaltEngine()`. Start a TWAP parent (`PARENT_TWAP_01`, 10,000 shares, 4,000 executed) with two resting child orders and ingest `HALTED_LULD`. Verify state `RUNNING → PAUSED_HALTED`, `cancel_requests_issued == 2`, **`cancelled_child_orders_count == 0`**, `orders_still_live_count == 2`, and both children at `PENDING_CANCEL`.
- Apply `CANCEL_REJECTED` to one child: verify it returns to `RESTING` and still counts as live. Apply `FILLED` to the other: verify `executed_qty` rises by the unfilled quantity.
- Ingest `PRE_OPEN_NO_CANCEL` with a live child: verify `cancel_permitted is False` and `cancel_requests_issued == 0`.
- Ingest `AUCTION_REOPENING`: verify `REBALANCING_POST_RESUMPTION` with `is_slicing_active is False`.
- Re-benchmark check with independently derived values: 36,000 shares over `[1000, 4600]` (rate 10.0/s), 12,000 executed, halt 2000→2300. Verify `halt_duration_s == 300.0`, `rebenchmarked_end_ts == 4900.0`, and `required_rate_qty_per_s == 24000/2600 ≈ 9.2308`, no breach.
- Guard check: 10,000 shares over `[0, 1000]`, 1,000 executed, halt 900→950. Verify `required_rate_qty_per_s == 90.0`, `rebenchmark_breach is True`, slicing off, and `schedule_end_ts` unchanged at 1000.0.
- Negative checks: unrecognised status, zero `total_target_qty`, negative `executed_qty`, NaN/Inf `event_ts`, inverted schedule, `hard_end_ts` before `schedule_end_ts`, zero-quantity child, duplicate acknowledgement, and over-fill must each be rejected or fail safe.
- Run `python -m unittest discover -s skills/execution-algo-behavior-under-halted-instrument/scripts` and confirm 100% pass rate.

## Related Skills

- `black-swan-playbook-for-halted-markets`
- `execution-algo-twap-vwap-slicing`
- `execution-algorithm-kill-switch-integration`
- `smart-order-router-failover-on-venue-outage`
- `order-placement-idempotency`
