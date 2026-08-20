---
name: broker-order-type-capability-matrix
description: Use when building multi-broker quantitative trading systems to maintain
  a capability matrix of native order types (Bracket, OCO, Trailing Stop, Iceberg,
  PEG, TWAP, VWAP) supported by each broker, and synthesize software-emulated order
  triggers (via local EMS) when native support is missing. Conserves the parent
  quantity exactly across synthesized slices and rejects exit legs placed on the
  wrong side of the market rather than routing an order that triggers instantly.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- order-types
- capability-matrix
- bracket-orders
- oco-orders
- synthetic-orders
- execution-algorithms
brokers_frameworks:
- Interactive Brokers TWS API
- Alpaca Trading API
- Zerodha Kite Connect
- Binance Spot API
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when deploying algorithmic strategies across broker APIs whose
native order-type support differs. IBKR exposes bracket orders, OCA groups, iceberg
via `displaySize` and the TWAP/VWAP IBALGOs; Alpaca exposes bracket and OCO order
classes but no iceberg and no execution algos; Zerodha withdrew bracket orders but
offers a native iceberg variety; Binance Spot covers OCO, OTOCO and `icebergQty` but
reaches TWAP only through a separate Algo API. Submitting an unsupported type gets it
rejected. This skill checks native support first and, where it is missing,
decomposes the order into a primary leg plus explicitly typed legs for a local
Execution Management System.

The two things it must get right, because everything downstream trusts them: the
**quantity conservation** (does the plan execute exactly the requested size?) and the
**price geometry** (are the protective legs on the side of the market that protects?).

## When NOT to Use

- **As an EMS.** `scripts/capability_matrix.py` plans an order. It performs no network
  I/O, submits nothing, watches no quotes and holds no state. The trigger watching,
  the timers, the sibling cancellation and the persistence are all yours.
- **As a live source of broker truth.** `DEFAULT_CAPABILITIES` is a dated, sourced
  template. Support changes and varies by asset class, product and entitlement —
  re-verify against `references/standards.md`'s links before trading it.
- **For VWAP, pegged, trailing-stop or auction orders the broker lacks.** These are
  refused, not approximated. Emulating VWAP as evenly spaced slices yields a TWAP
  benchmarked against the wrong number. See the Related Skills.
- **As a substitute for idempotency.** The plan tells you to fire one primary order;
  making sure a retry does not fire it twice is `order-placement-idempotency`.
- **Where a native path exists.** An emulated OCO is strictly worse than the venue's
  own: local triggers add latency, a failure domain, and real double-execution risk.

## Prerequisites

- Each target broker's **documented** order-type surface — the API reference, not the
  four types you happened to test. `references/standards.md` lists them with sources.
- A local EMS that can watch Level 1 quotes, run interval timers, cancel a sibling leg
  on trigger, and **persist emulated legs across a restart**.
- The venue's minimum order size and quantity step per instrument, for `min_slice_qty`
  and for rounding the slice schedule before dispatch.

## Workflow

1. **Register capability profiles, and make them self-consistent.** The `supports_*`
   booleans are a view of `native_order_types`, not a second switch — a profile where
   they disagree raises at construction rather than reaching order time claiming
   native OCO while silently taking the emulation path.

2. **Validate before deciding how to route.** `plan_order_execution` checks quantity,
   prices, leg completeness and price geometry *ahead of* the native/emulated branch.
   A bracket with no exit legs, or with the stop on the profitable side, is malformed
   regardless of whether the broker would have accepted it — the native path is not a
   validation bypass. Arguments the requested type does not consume are refused by
   name rather than discarded: a `stop_loss_price` silently dropped from a MARKET
   order leaves the caller believing a position is protected that nothing is watching.

3. **Read `action` per order type — they are not the same.** For `BRACKET` it is the
   **entry** side and the exits invert it. For `OCO` it is the side of **both** exit
   legs, matching the same-side constraint Binance and Alpaca document for their
   native OCOs. `BRACKET`+`BUY` therefore wants the target *above* the stop, and
   `OCO`+`BUY` (closing a short) wants it *below*.

4. **Execute the plan exactly as written.** If `has_primary_order`, fire
   `primary_quantity` of `primary_order_type` at `primary_price` once; then register
   `emulated_legs`. `primary_quantity` plus every scheduling leg's quantity already
   equals the requested quantity — do not re-slice the parent. When
   `primary_order_type` is `None` (emulated OCO), fire nothing now.

5. **Persist before you submit.** `plan.to_dict()` serializes the plan losslessly.
   Write it down before the primary order goes out, not after.

> Full procedure, EMS contract and the price-geometry table: see `references/workflows.md`.
> Per-broker capability evidence with sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Firing the primary slice *and* a feeder sized for the whole parent order.** If the
  feeder's quantity is the full parent quantity while the primary is already the first
  slice, an EMS following the contract executes `quantity + one slice` — a 100-unit
  iceberg in 3 slices sends 133. The feeder here carries only what the primary did not.
- **Slicing in binary float.** Ten thousand units in seven slices, summed, is not ten
  thousand units. The residue is tiny and the consequences are not: the final slice
  can be rejected for exceeding the parent's remaining quantity, or leave dust.
- **Assuming emulated OCO atomicity.** One leg can fill on the exchange while the
  local cancel of the sibling is still in flight — both execute. Even IBKR's *native*
  OCA only removes this risk with an `ocaType` "with block", which routes one order at
  a time; `ocaType=3` carries the same exposure a local emulation does.
- **Naming the unsupported type as the primary order.** An emulated OCO whose plan
  reports `primary_order_type=OCO` sends the caller back to the exact endpoint that
  just failed the native-support check.
- **Exit legs on the wrong side of the market.** A "protective" stop above a long
  entry is already through its trigger when the EMS registers it — it does not protect
  the position, it closes it instantly at a loss.
- **Falsy-checking a price.** `if not stop_loss_price` reads `0` as "not supplied" and
  plans a bracket with a silently missing leg instead of rejecting a bad price.
- **Iceberg slices below the venue floor.** Zerodha's iceberg takes 2–50 legs; Binance
  enforces `LOT_SIZE` and a minimum notional. A slice below the floor is a guaranteed
  rejection — pass `min_slice_qty` so the plan fails at planning time.
- **Slicing a market order and calling it an iceberg.** An iceberg is a resting limit
  order with a restricted display size; market slices sweep the book instead. This is
  why the venues offering it natively attach it to limit orders.
- **Treating "native TWAP" as unconditional.** Binance's TWAP lives only on the Algo
  endpoints, bounded by duration and notional; IBKR's TWAP/VWAP IBALGOs are documented
  for US equities. Both are "native" and neither is universal.
- **Losing emulated state.** If the EMS restarts without its legs, the emulated stops
  never fire and nothing raises an error — the position is simply unprotected.

## Verification

- Run the unit suite and confirm every test passes:
  `python -m unittest discover -s skills/broker-order-type-capability-matrix/scripts`
- Assert quantity conservation for an indivisible size: plan a 1000-unit TWAP in 7
  slices and check `primary_quantity + sum(metadata["slice_schedule"])` equals exactly
  1000. This is the highest-value single assertion in the suite.
- Assert an emulated OCO reports `primary_order_type is None` and
  `has_primary_order is False`.
- Assert an inverted bracket (stop above target on a long) raises on the **native**
  path too, not just the emulated one.
- Assert `BRACKET`+`BUY` and `OCO`+`BUY` accept opposite price orderings.
- Assert a `0` price is rejected rather than treated as absent.
- Assert `min_slice_qty` rejects a schedule whose slices fall below the venue floor.
- Assert `BrokerOrderCapabilityMatrix(custom_matrix={})` resolves no brokers at all.
- Spot-check each profile in `references/standards.md` against the broker's live
  documentation before promoting to live trading.

## Related Skills

- `broker-agnostic-adapter-interface`
- `order-placement-idempotency`
- `execution-algo-twap-vwap-slicing`
- `iceberg-order-native-broker-support-vs-simulation`
- `minimum-fill-size-and-lot-rounding-logic`
- `paper-to-live-promotion-checklist`
