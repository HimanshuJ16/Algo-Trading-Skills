---
name: strategy-decommissioning-and-position-unwind-procedure
description: >-
  Use when a retired strategy's book must be flattened in an orderly way: block new
  entries, cancel working orders, then release participation-capped liquidation waves.
  Deliberately slow; an emergency exit uses the kill switch.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: portfolio-multi-strategy
  tags: strategy-decommissioning, position-unwind, participation-capped-liquidation, order-entry-block, position-reconciliation, treasury-return
  brokers_frameworks: "Multi-Strategy Liquidation Framework; Participation-of-Volume (POV) Caps; MiFID II RTS 6; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a strategy has been approved for retirement — alpha decay, a persistent
risk-limit breach, a committee vote — and its open book must be flattened without dumping it
into the market. Decommissioning is not one action but four that are easy to conflate:
**stop new entries**, **kill the orders already working**, **liquidate the inventory in
participation-capped waves**, and **prove the book is flat before releasing the capital**.
Skipping any one of them leaves a live position attributed to a strategy nobody is watching.

`StrategyDecommissioningEngine` is the ledger and state machine for that sequence. It walks
`ACTIVE` → `ORDER_ENTRY_BLOCKED` → `UNWIND_IN_PROGRESS` → `FULLY_UNWOUND` →
`DECOMMISSION_COMPLETE`, and refuses the last transition while any position, unfilled slice,
unconfirmed cancellation, or unresolved reconciliation break remains.

The regulatory anchor for the orderly-withdrawal posture is MiFID II RTS 6
(Commission Delegated Regulation (EU) 2017/589) Art. 17(1) — remedial action on a triggered
post-trade control "may include adjusting or shutting down the relevant trading algorithm or
trading system or an orderly withdrawal from the market" — and Art. 12, which requires the
firm to be able to "cancel immediately, as an emergency measure, any or all of its unexecuted
orders". See `references/standards.md` for the exact scope of each.

## When NOT to Use

- **As a kill switch or emergency flatten.** This engine is deliberately slow: it caps each
  wave at a fraction of ADV. When the reason to exit is a runaway algorithm or a breached
  loss limit, the correct control is an immediate halt-and-cancel — see
  `execution-algorithm-kill-switch-integration` and
  `kill-switch-and-drawdown-circuit-breakers`. Decommissioning is what happens *after* the
  bleeding stops, not instead of stopping it.
- **As an execution algorithm.** It authorises a quantity; it does not schedule, price,
  route, or send anything. There is no volume curve and no time grid. Each slice must be
  handed to a real algo (`execution-algo-twap-vwap-slicing`,
  `participation-of-volume-pov-execution`) — sending a whole wave as one market order
  reintroduces exactly the impact the cap exists to avoid.
- **As an entry block the strategy does not call.** `assert_entry_allowed()` is a gate, not
  an interceptor. The engine owns no order path; an entry path that never calls it is an
  unblocked entry path regardless of the engine's state.
- **On instruments where quantity is not the binding constraint.** Options at expiry, futures
  into delivery, and anything with early-assignment or physical-settlement mechanics need
  expiry-aware handling first — see `options-pin-risk-management-at-expiry` and
  `physical-vs-cash-settlement-handling`.
- **Without an independent position source.** The engine's inventory is whatever was loaded
  into it. It detects overfills against its own book, not against the broker's; reconcile
  against the broker (`multi-broker-consolidated-position-view`) before and after.

## Prerequisites

- Strategy identifier and an auditable decommissioning reason (required, non-empty — it is
  the audit record).
- Position inventory: `StrategyPosition(symbol, quantity, market_price, avg_daily_volume,
  max_adv_slice_pct=10.0, lot_size=1.0)`. `quantity` is signed (positive long, negative
  short); `avg_daily_volume` and `market_price` must be positive.
- The broker order ids of every order **already working** at the moment of the decision.
- A fill feed that carries the broker's execution/fill id — without it, duplicate-fill
  suppression is impossible.

## Workflow

1. **Hard-block entries and register working orders**
   - `initiate_decommissioning(reason, working_order_ids=[...])` → state
     `ORDER_ENTRY_BLOCKED`, `new_entries_allowed` becomes `False`.
   - **Decision point — blocking entries does nothing to orders already resting.** A
     retired strategy with 40 working limit orders will keep acquiring inventory while you
     unwind it. Register those ids here; the engine will not let the strategy reach
     `FULLY_UNWOUND` until every one is confirmed cancelled.
   - Wire `assert_entry_allowed()` into the strategy's entry path. The block is only real
     where it is called.

2. **Confirm cancellations, not cancel requests**
   - `record_order_cancellation(order_id)` — only on a broker-confirmed cancellation.
   - **Decision point — a cancel request is not a cancellation.** An order can fill in the
     window between request and acknowledgement. Confirming on the request produces a book
     that says flat while a fill is in flight; see `broker-api-idempotent-cancel-requests`.

3. **Release one participation-capped wave per symbol**
   - `generate_unwind_liquidation_slices()` → one `LiquidationSliceOrder` per eligible
     symbol, capped at `max_adv_slice_pct` of ADV, floored to a whole lot. Long → `SELL`,
     short → `BUY`.
   - **Decision point — a symbol with an unfilled slice outstanding is skipped, not
     re-sliced.** Regenerating a wave for it would authorise the same shares twice; the
     second fill flips the position. If the child order is dead, call
     `cancel_slice(slice_id)` to release the symbol, then re-slice.
   - **Decision point — check `report.unsliceable_symbols` before looping.** A symbol whose
     participation cap is below one lot has no compliant wave; the engine reports it and
     leaves the position alone rather than rounding up through the cap. It will never shrink
     on its own — it needs a block trade, a broader cap, or a manual liquidation.
   - The last wave for a symbol carries `is_final_slice=True` and deliberately includes the
     odd-lot residual: a residual that is never sent is a position that is never closed.

4. **Record fills against the inventory**
   - `record_slice_execution(slice_id, symbol, executed_qty, executed_price, realized_pnl,
     execution_id=...)`.
   - **Decision point — always pass `execution_id`.** It is the only thing making this call
     idempotent. A replayed webhook without one double-decrements the position and
     double-counts P&L; the engine warns but cannot protect you.
   - **Decision point — an overfill is recorded, not clamped.** Filling 1,500 against a
     1,000 long leaves the position at −500 and raises a `ReconciliationBreak`. That short
     is real and unhedged; the next wave will generate a `BUY` to close it, and treasury
     return stays blocked until the break is acknowledged.
   - Realized P&L is taken from the broker or the accounting system. The engine aggregates
     it and never recomputes it — tax-lot selection, fees and financing live there.

5. **Return capital only against a proven-flat book**
   - `return_capital_to_treasury()` → `DECOMMISSION_COMPLETE`. It raises unless every
     position is flat, no slice is outstanding, every registered working order is confirmed
     cancelled, and every reconciliation break is resolved (or explicitly acknowledged via
     `acknowledge_breaks=True`, which records the human decision rather than hiding it).

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Blocking entries and calling it a decommissioning.** New-entry blocking says nothing
  about resting orders. RTS 6 Art. 12 exists precisely because unexecuted orders are a
  separate exposure; register and confirm them or the "retired" strategy keeps trading.
- **Re-generating a wave before the previous one is filled or dead.** Two waves of 10% ADV
  released against the same 1,000-share position sell 1,000 shares of a position that is
  still 1,000 long on paper — the second fill opens an unintended short.
- **Clamping an overfill to flat.** `max(0, qty - executed)` reports a closed book while an
  unhedged opposite-side position is live in the market. Record the flip and escalate.
- **Recording a fill without the broker's execution id.** Fill webhooks retry. Without an
  idempotency key the same fill decrements the position twice, and the engine then reports
  the strategy flat while half the inventory is still open.
- **Treating a generated slice as a liquidation.** Authorising a wave changes no position.
  Reporting slice notional as liquidated notional tells the desk 45% of the book is gone
  before a single share has traded.
- **Assuming slower is safer.** Cutting the participation cap lengthens the unwind and keeps
  the retired strategy's market risk on the book for more days. That is the Almgren–Chriss
  impact-versus-timing-risk trade-off, not a free improvement; pick the cap deliberately.
- **A zero or unknown ADV.** `cap = pct × 0` is a zero-quantity wave and an unwind loop that
  never terminates. The engine rejects a non-positive ADV at load time.
- **Ignoring what lot flooring does to the horizon.** A 5% cap on 3,000 ADV is 150 shares,
  but at `lot_size=100` the wave is 100 — a third of the intended participation, turning a
  27-wave unwind into 40. Read `participation_pct` on the emitted slice rather than assuming
  the configured cap was achieved.
- **Declaring victory on an empty position dictionary.** A position that was loaded flat, or
  a residual below the lot size, must still be accounted for. Completion is tested on "every
  quantity is flat", never on "the container is empty".
- **Unwinding a hedged pair leg by leg.** Flattening the long side of a spread first leaves
  the remaining leg naked for the rest of the unwind. Sequence the legs, or unwind on a
  ratio — see `cross-asset-hedge-execution-synchronization`.

## Verification

- Load AAPL long 1,000 @ 150 (ADV 5,000) and MSFT short 500 @ 300 (ADV 2,000). Before
  initiation, `new_entries_allowed` is `True`; after `initiate_decommissioning(reason)` it is
  `False` and `assert_entry_allowed()` raises `EntryBlockedError`.
- `generate_unwind_liquidation_slices()` yields AAPL `SELL` 500 (10% of 5,000,
  `remaining_after_slice_quantity` +500) and MSFT `BUY` 200 (10% of 2,000, −300), both with
  `participation_pct` 10.0 and `is_final_slice` `False`.
- Accounting: with slices generated but nothing filled, `initial_total_notional_usd` is
  300,000, `remaining_notional_usd` is 300,000 and `liquidated_notional_usd` is **0**.
  After filling 500 AAPL @ 151 and 200 MSFT @ 299, `liquidated_notional_usd` is 135,300 and
  `initial_total_notional_usd` is still 300,000.
- Negative checks that must each raise or flag: `avg_daily_volume=0`, `market_price<=0`,
  `max_adv_slice_pct` outside `(0, 100]`, a duplicate symbol, slicing while `ACTIVE`, a fill
  before initiation, a fill for an unknown symbol, a repeated `execution_id` (suppressed),
  a 1,500 fill on a 1,000 long (position → −500 plus a reconciliation break), and
  `return_capital_to_treasury()` with a residual position, an unconfirmed working order, or
  an unacknowledged break.
- Run `python -m unittest discover -s skills/strategy-decommissioning-and-position-unwind-procedure/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `strategy-lifecycle-retirement-criteria`
- `strategy-committee-governance-for-capital-allocation-decisions`
- `participation-of-volume-pov-execution`
- `execution-algo-twap-vwap-slicing`
- `execution-algorithm-kill-switch-integration`
- `broker-api-idempotent-cancel-requests`
- `order-placement-idempotency`
- `minimum-fill-size-and-lot-rounding-logic`
