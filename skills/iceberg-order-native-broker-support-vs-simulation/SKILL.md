---
name: iceberg-order-native-broker-support-vs-simulation
description: >-
  Use when deciding which layer hides the reserve quantity of a large order: a
  venue-native iceberg, a broker-simulated one, or client-side slicing, including venue
  minimum-display rules. Detecting others' icebergs is a different skill.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: execution-algorithms, iceberg-orders, order-slicing, display-quantity, reserve-orders, queue-priority, smart-order-routing
  brokers_frameworks: "CME Globex (tag 1138 DisplayQty); Nasdaq Equity 4 Rule 4703(h) Reserve Orders; Deutsche Boerse T7 / Xetra (iceberg peak, randomised peak); Binance Spot API (icebergQty); Interactive Brokers TWS (Display Size)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a parent order is large enough that displaying it in full would move the market, and you must choose *which layer* hides the reserve quantity. Three layers are possible and they are not equivalent:

- **Native exchange iceberg** — the matching engine holds the reserve (CME `DisplayQty`, Nasdaq Reserve Order, T7 iceberg peak, Binance `icebergQty`). One order message; the venue replenishes.
- **Broker-simulated iceberg** — the broker's servers re-post each slice. The API looks identical to native, but the reserve rests at the broker, not the exchange.
- **Client-side synthetic** — your process sends one child order per displayed slice, paying a network round trip per refill.

The distinguishing question this skill answers is *not* "does the broker API accept a display-size field". It is "**where does the hidden quantity actually rest**", because a broker can expose one display-size parameter that is native on some exchanges and simulated on others.

## When NOT to Use

- **To preserve queue priority.** No iceberg preserves it. On every venue checked, replenishment goes to the back of the queue at that price level (CME: refreshed priority is "the lowest of the remaining orders"; Nasdaq Rule 4703(h): the replenished portion gets a new timestamp; T7: the new peak is entered with a new timestamp behind same-limit orders). Native routing removes the *client round trip*, not the priority reset. If queue position is the objective, see `queue-position-modeling-for-passive-orders`.
- **To stay undetected.** Slice-size randomisation defeats a fixed-size pattern; it does not defeat the standard detection method, which compares cumulative traded volume against displayed depth and counts refills at a price level — see `iceberg-order-simulation-and-detection`. Treat randomisation as raising the cost of detection, not as concealment.
- **When the order needs a non-GTC time in force.** Some venues accept the native iceberg field only with GTC — Binance spot: "Any order with an `icebergQty` MUST have `timeInForce` set to `GTC`." An IOC/FOK iceberg is a rejection, not a fallback.
- **When the display size would fall below the venue minimum.** Nasdaq requires the displayed size to be one or more round lots at entry; T7 sets a minimum peak per security; CME sets it per product. A sub-minimum peak is rejected or silently rounded.
- **As a schedule-driven execution algorithm.** An iceberg reacts to fills, not to a clock or a volume curve. For benchmark-tracking participation, use `execution-algo-twap-vwap-slicing` or `participation-of-volume-pov-execution`.
- **As the dispatch layer.** `IcebergExecutionRouterEngine` produces a *plan*. It submits nothing, acknowledges nothing, and reconciles nothing.

## Prerequisites

- Parent order: `symbol`, `side` (`BUY`/`SELL`), `total_quantity`, `target_display_quantity`, `limit_price`, `time_in_force`.
- Per **broker-and-exchange pair** (not per broker): `iceberg_support` (`NATIVE_EXCHANGE` / `BROKER_SIMULATED` / `UNSUPPORTED`), `native_parameter_name`, `min_display_quantity`, `lot_size`, `supports_native_display_randomization`, `requires_gtc_for_iceberg`.
- `client_refill_round_trip_ms`: **your own measured** fill-notification-in plus replacement-order-out round trip. There is no defensible default; an uncalibrated value produces a latency estimate of zero, which reads as "free".
- An order-dispatch layer with client-order-ID idempotency (`order-placement-idempotency`) to actually submit what this skill plans.

## Workflow

1. **Classify the venue's iceberg support — three states, not a boolean.**
   - Confirm from the broker's per-exchange order-type documentation whether the display parameter is passed through to the matching engine or emulated by the broker. Default to `BROKER_SIMULATED` when the documentation does not say; assuming `NATIVE_EXCHANGE` overstates the reserve's protection.
   - **Decision point:** `BROKER_SIMULATED` still avoids the client round trip, but the reserve is exposed to broker outages and broker-side refill latency, and is not covered by exchange order-handling rules. Where that exposure is unacceptable, treat the venue as `UNSUPPORTED` and slice under your own supervision.

2. **Resolve the effective display quantity against venue constraints.**
   - Round the requested peak *down* to `lot_size` (Nasdaq Rule 4703(h) rounds a mixed lot down), then raise it to `min_display_quantity` if the round-down went below.
   - **Decision point:** if the resolved display quantity now covers the whole parent, there is nothing to hide — route a plain limit order rather than an iceberg with a zero reserve.

3. **Native / broker-simulated path: build one parent payload.**
   - Emit `{native_parameter_name: effective_display}` alongside the parent quantity and price.
   - **Decision point — validate time in force before sending, not after rejection.** If the venue restricts the iceberg field to GTC and the request specifies otherwise, raise. Do not silently rewrite the caller's time in force; that changes the order's semantics.
   - **Decision point — do not apply client-side randomisation here.** Where the venue randomises the peak natively (T7 min/max peak volume), use that. Where it does not, the requested randomisation is unachievable and must be reported as ignored, not silently dropped.

4. **Synthetic path: build the child-slice schedule.**
   - Draw each slice lot-aligned from $[\,Q_{\text{display}}(1-p),\; Q_{\text{display}}(1+p)\,]$ with a **seeded** RNG, so a schedule can be reproduced in a backtest or a post-trade investigation.
   - **Decision point — bound the schedule before generating it.** Compute the worst-case slice count as $\lceil Q_{\text{total}} / Q_{\min} \rceil$ and reject it above a configured ceiling. A tiny display size against a large parent is a message storm, not an execution plan.
   - **Decision point — never emit an undersized tail.** Merge any final slice below `min_display_quantity` into its predecessor. A lone odd-lot residual advertises both that a parent order existed and that it is now exhausted.

5. **Report the plan, with its costs stated rather than assumed.**
   - Client refill latency $= (N_{\text{slices}} - 1) \times$ `client_refill_round_trip_ms`, populated **only** on the synthetic path. For venue-side refills report `None`, not `0.0`.
   - Carry `loses_time_priority_on_refill` (true in every iceberg mode) and the message-count warning through to the caller.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a native iceberg as latency-free and priority-preserving.** It is neither. It removes the client round trip only; the refreshed peak still goes to the back of the queue. Modelling native refills as instant *and* priority-preserving overstates native fill rates in every backtest that assumes it.
- **Reading a broker's display-size field as proof of exchange support.** The same parameter can be native on one exchange and broker-emulated on another. Sizing the hidden reserve as if it rests at the exchange, when it rests on a broker server, misprices the outage risk.
- **Reporting a plan as an execution.** Child slices that have not been sent are `PLANNED`, not `FILLED`. Stamping them as filled makes an unsent schedule look like a completed execution to any downstream reconciliation or agent reading the report.
- **Ignoring the venue minimum display size.** A 40-share peak on Nasdaq is not a stealthier iceberg; it is a rejected or silently rounded order. The constraint is per venue and per security — do not carry one venue's round lot to another.
- **Leaving an odd-lot tail on the synthetic schedule.** Randomising the interior slices and then sending a 51-share residual undoes the randomisation: the tail is the most informative message in the sequence.
- **Ignoring the message-rate cost of synthetic slicing.** One parent order becomes N order messages, which counts against venue order-to-trade-ratio limits and message-rate fees — see `order-to-trade-ratio-fee-penalty-avoidance`.
- **Assuming the client stays connected.** A synthetic iceberg only works the parent while the process is up. A disconnect between slices leaves the remainder unworked *and* the last child order live at the venue; reconcile open orders on reconnect before sending the next slice, or you will double up.
- **Unseeded randomisation.** An unseeded RNG makes a slice schedule irreproducible, which means a fill sequence cannot be replayed in a post-trade investigation and a backtest cannot be rerun.
- **Rewriting the caller's time in force to satisfy an iceberg constraint.** Converting an IOC to GTC to make `icebergQty` valid turns a fill-or-move order into a resting order — a different order, silently.

## Verification

- Instantiate `IcebergExecutionRouterEngine(seed=11)`. Route 10,000 shares with `target_display_quantity=450` on a native venue with `lot_size=100, min_display_quantity=100`: verify `effective_display_quantity == 400` (mixed lot rounded down), `native_order_parameters["displaySize"] == 400`, `planned_slice_count == 1`, `client_refill_latency_ms_total is None`, and `loses_time_priority_on_refill is True`.
- Route the same order on an `UNSUPPORTED` venue with `client_refill_round_trip_ms=30.0`: verify every child slice is lot-aligned within $[400, 600]$, `sum(slice_quantity) == total_quantity`, all slices are `PLANNED`, and the latency total equals $(N-1) \times 30$ ms.
- Boundary: 2,050 shares at display 500 with `min_display_quantity=100` must not end in a 50-share slice, and must still sum to exactly 2,050 across every seed.
- Negative checks: `side="LONG"`, a non-finite or non-positive `limit_price`, `slice_randomization_pct` outside $[0, 1)$, a non-integer quantity, `iceberg_support=True` (the removed boolean API), a GTC-only venue with `time_in_force="IOC"`, and a schedule exceeding `max_child_slices` must each raise.
- Determinism: two engines built with the same seed must produce identical schedules; different seeds must not.
- Run `python -m unittest discover -s skills/iceberg-order-native-broker-support-vs-simulation/scripts` and confirm all tests pass.

## Related Skills

- `broker-order-type-capability-matrix`
- `iceberg-order-simulation-and-detection`
- `execution-algo-twap-vwap-slicing`
- `queue-position-modeling-for-passive-orders`
- `minimum-fill-size-and-lot-rounding-logic`
- `order-to-trade-ratio-fee-penalty-avoidance`
- `order-placement-idempotency`
