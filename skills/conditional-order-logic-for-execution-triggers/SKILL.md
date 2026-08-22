---
name: conditional-order-logic-for-execution-triggers
description: Use when simulating conditional orders client-side — evaluating nested
  Boolean condition trees (price, volume, time, cross-asset) against market data to
  release child orders exactly once, with fail-safe handling of missing or stale quotes.
domain: Execution Algorithms
subdomain: Order Logic & Triggers
tags:
- conditional-orders
- execution-triggers
- boolean-tree
- cross-asset-trigger
- oco
- oms
- ems
brokers_frameworks:
- Generic Execution
- FIX TriggeringInstruction
- Python Dataclasses
version: "1.1.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when implementing **client-side** conditional order logic (If-Touched, bracket, One-Cancels-the-Other, or cross-asset triggers) in an OMS/EMS. Conditional orders remain dormant in local memory until incoming market data evaluates a nested Boolean condition tree to `TRUE`, upon which child orders are released for pre-trade risk checking and venue routing.

Client-side simulation is the right answer when the trigger the strategy needs does not exist at the venue or the broker: a condition spanning two instruments, a condition mixing price with volume and wall-clock time, or a trigger on an exchange that does not accept the order type at all. NYSE and NYSE MKT stopped accepting stop orders and GTC orders on 26 February 2016 and cancelled the resting ones, so equity stop logic on those books lives in a broker's or a client's simulator, not on the exchange.

## When NOT to Use

- **A native or broker-resident conditional order does the same job.** IBKR's conditional orders support price, time, margin, execution, volume and percent-change conditions combined with AND/OR, and IBKR states that active orders remain active after exiting Trader Workstation — you can be filled without being logged in. CME Globex triggers stop orders natively against the last trade price. A broker- or exchange-resident trigger survives your process crashing, your feed dropping and your host rebooting. This engine survives none of those: if the process is down when the market touches the level, nothing fires and nothing tells you so. Prefer native; simulate only what native cannot express.
- **Latency-sensitive triggering against fast markets.** A client-side trigger fires one network round trip *after* the price prints, then the child order still has to travel to the venue. For stop logic on a fast-moving book, an exchange-resident stop (with protection points, where the venue offers them) fills closer to the level.
- **As a risk control.** A kill switch or exposure limit must not depend on a condition tree that goes quiet when the data feed dies. Keep circuit breakers in a path that fails *closed* — see `execution-algorithm-kill-switch-integration`.
- **On an instrument that may be halted or in an auction.** A dormant trigger evaluating a stale pre-open or halted book will fire on the first print of a re-opening auction. Gate the tree with an explicit session/state condition; see `execution-algo-behavior-under-halted-instrument`.

## Prerequisites

- Streaming market data for the target instrument **and every referenced benchmark**, each quote carrying a timestamp (epoch seconds, UTC) if staleness enforcement is wanted.
- A decision on the **trigger price type** per condition — last trade, bid, ask, or mid. FIX `TriggerPriceType(1107)` enumerates exactly this choice (Best Offer, Last Trade, Best Bid, Best Bid or Last Trade, Best Offer or Last Trade, Best Mid), and it changes when the trigger fires.
- A unique client order identifier and a fully specified child order (`symbol`, `side`, `quantity`, `order_type`, `price` for limits).
- A downstream pre-trade risk check: a fired trigger emits an *intent*, not an authorised order.

## Workflow

1. **Condition Tree Construction**: Build atomic nodes — `PriceCondition`, `VolumeCondition`, `TimeCondition`, `CrossAssetCondition` — and compose them with `AndCondition` / `OrCondition` / `NotCondition`. Choose the price field deliberately (`last` vs `bid` vs `ask`): a `>=` trigger read off the bid and one read off the last trade fire at different moments on the same tape. Empty composites are rejected at construction, because an empty AND gate is vacuously true and would fire on the first tick.
2. **Staleness Policy**: Pass `max_quote_age_seconds` to any condition whose input can go stale — always to cross-asset legs, whose benchmark feed can disconnect while the primary keeps ticking. With it set, a quote whose `timestamp` is missing or older than the limit evaluates to UNKNOWN, not to a value.
3. **Conditional Order Registration**: Wrap the tree and a validated `ChildOrderPayload` in a `ConditionalOrderTrigger`, then `ConditionalOrderEngine.register(trigger, oco_group=...)`. Triggers sharing an `oco_group` are one-cancels-the-other; a bracket registers its target and its stop in one group so both legs cannot reach the venue.
4. **Tick Processing**: Call `engine.process_tick(market_state, now=...)` on each update. The engine pins one evaluation clock for the whole tick, so a time condition cannot read a different instant than its siblings in the same tree.
5. **Trigger Firing and Dispatch**: The engine returns the child orders released by this tick. Firing is a single atomic DORMANT → TRIGGERED transition under a lock — evaluation and state change happen together, so two feed-handler threads delivering the same tick cannot both release the order. On fire, dormant OCO siblings are cancelled.
6. **Undecided is not false**: The tree evaluates in three-valued (Kleene) logic. A missing or stale input yields UNKNOWN; only a *definite* TRUE fires. Route UNKNOWN to monitoring — a trigger that has been undecided for minutes means a feed is down, and it will not fire when the level trades.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Firing duplicate orders**: Checking `status == DORMANT` and setting `TRIGGERED` as two separate steps. Two threads can interleave between the check and the set and both release the child order. Evaluate and transition under one lock.
- **Treating missing data as `False`**: Under two-valued logic a missing quote reads as FALSE, so `NotCondition(missing)` reads as TRUE and releases a live order on data you never received. Negation is where a fail-safe FALSE stops being safe.
- **A dropped benchmark silently rewriting the trigger**: `AAPL >= 150 AND SPY >= 500` with the SPY feed dead is not "wait"; under two-valued logic it is a permanent FALSE that never fires, and if the composition is `OR` it becomes a bare `AAPL >= 150` outright trigger. Set `max_quote_age_seconds` on cross-asset legs and alert on UNKNOWN.
- **Exact float equality as a trigger**: `last == 150.00` against a decimal price feed effectively never matches. Use a band with an explicit tolerance, or an ordering operator.
- **Silently ignoring an unknown operator**: Returning `False` for a typo'd `'=>'` produces a trigger that can never fire and reports no error — discovered when the order never arrives. Validate operators at construction.
- **Naive datetimes in time conditions**: A trigger meant for 15:50 New York fires at 15:50 in whatever zone the host happens to be in. Require timezone-aware targets.
- **Confusing the trigger price type**: Simulated triggers only fire on the field you feed them. IBKR's trigger methods exist for the same reason (double bid/ask, last, double last, bid/ask, last-or-bid/ask, mid-point) and apply only to *simulated* orders — a natively handled stop ignores them entirely.
- **Blocking the hot path**: Heavy parsing, disk I/O or network calls inside the market-data callback that evaluates the tree. Keep evaluation to arithmetic on already-parsed values.
- **Treating a fired trigger as a fill**: The trigger produces an order intent. It still has to pass pre-trade risk, reach the venue, and be accepted — and it can be rejected.

## Verification

- Build `(AAPL.last >= 150.00) AND (SPY.last >= 500.00)` on a `ConditionalOrderEngine`. Feed `AAPL` 150.50 / `SPY` 499.00: the trigger must stay `DORMANT` and `process_tick` must return `[]`. Feed `SPY` 500.50: it must transition to `TRIGGERED` and return exactly one child order payload; a third tick must return nothing.
- Register a bracket as one `oco_group` and drive the take-profit level: the stop-loss leg must be `CANCELLED` and must stay silent when its own level trades afterwards.
- Drive one satisfying tick from 16 threads concurrently and assert exactly one payload is released.
- Feed a cross-asset tree with the benchmark quote removed and confirm `evaluate_tristate` returns `None` (UNKNOWN), not `False`, and that `NotCondition` over it does not fire.
- Set `max_quote_age_seconds=5.0` and feed a quote timestamped 30 seconds ago: no fire, and a stale-quote warning logged.
- Run `python scripts/test_conditional_order_logic_for_execution_triggers.py` and confirm a 100% pass rate.

## Related Skills

- `broker-order-type-capability-matrix`
- `execution-algorithm-kill-switch-integration`
- `execution-algo-behavior-under-halted-instrument`
- `order-placement-idempotency`
- `close-auction-participation-strategy`
