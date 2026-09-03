# Pre-Flight / Sign-off Checklist — execution-algo-twap-vwap-slicing

Use this before routing a parent order through the slicer in a live environment.

## Benchmark & scope
- [ ] Benchmark chosen deliberately (TWAP vs VWAP) and matched to the instrument's intraday volume profile — not defaulted.
- [ ] VWAP runs supply a real volume curve with **one weight per interval**; a flat curve is a TWAP and is not labelled VWAP.
- [ ] The order is not alpha-urgent (else `implementation-shortfall-minimization` / `implementation-shortfall-minimization`) and not an outsized share of window volume (else `participation-of-volume-pov-execution`).

## Sizing & schedule
- [ ] `lot_size` is the instrument's real quantity increment, and `total_qty` is a whole multiple of it.
- [ ] $\sum \text{target\_qty} = \text{total\_qty}$ exactly at build time, with no negative or fractional-lot clip.
- [ ] Fractional-quantity instruments (crypto, FX) verified end-to-end — not rounded to an all-zero schedule.
- [ ] The "actionable slices" warning is clear: the parent is at least as many lots as intervals.
- [ ] `jitter_pct` is in $[0, 0.5)$, applied to **both** size and timing, and child timestamps are confirmed strictly increasing.
- [ ] `seed` is set and recorded, so the schedule is reproducible and the global RNG is untouched.

## Lifecycle & catch-up
- [ ] Fill accounting (`on_child_fill`) is separated from child-order closure (`on_child_expired` / `on_child_reject` / `on_child_cancel`).
- [ ] `quantity_invariant_ok()` is asserted after every state transition in the live loop.
- [ ] Catch-up policy chosen deliberately; `max_child_multiple` is set for any non-passive policy.
- [ ] `GIVE_UP_AT_DEADLINE` runs have a `deadline` that is meaningful (venue close, not the default window end).
- [ ] Rejections are **classified** before rescheduling — min-notional, tick size, buying power, halt each get a different response.
- [ ] A timed-out placement is reconciled against the broker before being treated as a rejection.
- [ ] `AGGRESSIVE_CATCHUP` on a VWAP schedule is confirmed to preserve the curve, not flatten it.

## Order plumbing
- [ ] **Every child order** — not just the parent concept — goes through `order-placement-idempotency` and `multi-broker-rate-limit-handling`.
- [ ] Pre-trade risk limits (exposure, capital, price collars) sit **outside** the slicer and cannot be disabled by a scheduling bug.
- [ ] All outstanding child orders can be cancelled as a unit by the kill switch (RTS 6 Art. 12 where applicable).
- [ ] Halt/auction behaviour is handled by `execution-algo-behavior-under-halted-instrument`.

## Reporting
- [ ] `side` is set correctly; a SELL filled above benchmark reports **negative** `slippage_bps` (price improvement).
- [ ] `final_price` is supplied so `opportunity_cost_bps` and `implementation_shortfall_bps` are populated — a fill-only slippage number flatters every incomplete execution.
- [ ] `unfilled_qty`, `overfill_qty`, and `status_counts` are reviewed, not just the headline slippage.
- [ ] The report is actually read after each run, and a divergence trend is tracked.

## Compliance & records
- [ ] Applicable jurisdiction identified. EU firms: RTS 6 (Reg (EU) 2017/589) testing, kill functionality, pre-trade controls, real-time monitoring.
- [ ] No RTS 27/28 reporting obligation has been built in — both were removed by Directive (EU) 2024/790.
- [ ] Parameters (`jitter_pct`, interval count, catch-up policy, `max_child_multiple`) are recorded as calibrated choices with rationale — they are library defaults, not standards.
- [ ] Venue algo limits re-verified against current vendor documentation if native TWAP/VWAP is used instead.

## Testing
- [ ] `python -m unittest discover -s skills/execution-algo-twap-vwap-slicing/scripts` — 100% pass rate.
- [ ] Quantity invariant verified under a randomised fill/expiry/rejection/cancel sweep across all three catch-up policies.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
