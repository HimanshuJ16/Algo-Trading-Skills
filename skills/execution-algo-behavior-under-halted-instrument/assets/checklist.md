# Pre-Flight Checklist — Execution Algo Behavior Under Halted Instrument

## Status feed

- [ ] Is every venue status code your feed can emit **explicitly mapped** onto the engine's token set — with no prefix-matching on `"HALTED"`?
- [ ] Does an unrecognised status suspend slicing and alert, rather than falling through to "keep trading"?
- [ ] Are `LIMIT_STATE` and `STRADDLE_STATE` handled as *still trading, marketable orders rejected* rather than as halts?
- [ ] Is every call supplied an explicit `event_ts`, so replay reproduces live decisions?

## Cancellation

- [ ] Are child orders moved to `PENDING_CANCEL` on request and only to `CANCELLED` on a **venue acknowledgement**?
- [ ] Does downstream risk logic gate on `orders_still_live_count`, not `cancelled_child_orders_count`?
- [ ] Is `cancel_permitted` checked before attempting a cancel, and is a no-cancel phase (CME `Pre-Open - No Cancel`, Eurex extended-VI freeze) escalated rather than retried?
- [ ] Is a `CANCEL_REJECTED` acknowledgement treated as **the order is still live on the book**?
- [ ] Is a fill that raced the cancel reflected in `executed_qty` before any remaining-quantity or participation figure is recomputed?
- [ ] Are cancels never re-requested for an order already `PENDING_CANCEL`?
- [ ] Do you know your venue's **port-level setting** for whether resting orders are cancelled or preserved through a pause?

## Timers and re-benchmarking

- [ ] Is the halt clock stamped once and left unrestamped by duplicate halt messages?
- [ ] Does the halt clock keep running through the reopening auction?
- [ ] Are slice timers frozen for the halt duration rather than decrementing through it?
- [ ] Is `hard_end_ts` (session close) supplied, so the schedule is never extended past the close?
- [ ] Does the post-halt catch-up rate face an explicit cap, with a documented calibration for `max_rate_multiple`?
- [ ] When the cap binds, does the algo **hold and escalate** rather than resuming and dumping the backlog?

## Auction and resumption

- [ ] Is continuous slicing suppressed for `AUCTION_REOPENING` / `PRE_OPEN` (auction, not continuous matching)?
- [ ] Is `orders_still_live_count` re-checked before the first post-halt slice is dispatched?
- [ ] Is a halt that runs into the close handled — including the venue cancelling DAY/LOC/MOC/IO orders back to you?

## Integrity and audit

- [ ] Is `executed_qty > total_target_qty` surfaced as a reconciliation breach that forces slicing off, rather than clamped to zero remaining?
- [ ] Is an `AlgoHaltAuditReport` persisted for **every** transition, including no-ops?
- [ ] Are the engine's parameters documented as firm-calibrated values, not as regulatory or exchange standards?
