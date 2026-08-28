# Pre-Flight Checklist — Post-Only Repricing Under Fast Markets

## Venue semantics (do this first)

- [ ] Confirmed from the venue's rulebook/API docs what a **crossing** Post-Only order does:
      rejected, executed as a taker, or silently re-priced? (All three exist — `references/standards.md`.)
- [ ] If the venue **executes** crossing Post-Only orders (Nasdaq family), is the strategy's
      fee model and liquidity accounting correct for an unintended taker fill?
- [ ] If the venue **silently re-prices** (Deribit default), is `reject_post_only` set, or does
      the quoting model tolerate resting at a price it did not choose?
- [ ] Confirmed whether rejected orders consume order-rate quota on this venue — and designed
      as if they do, since it is not guaranteed either way.

## Market data inputs

- [ ] `tick_size` sourced per symbol from reference data at runtime, never hard-coded.
- [ ] Tick refreshed on the venue's reassignment schedule (US NMS increments are per-symbol
      and subject to regime change).
- [ ] Non-finite / zero / negative bid, ask, or tick rejected before any price arithmetic.
- [ ] Locked (`bid == ask`) and crossed (`bid > ask`) books detected and held, not repriced.
- [ ] Feed staleness / sequence gaps checked before trusting a crossed book as real.

## Pricing correctness

- [ ] Crossing test uses **inclusive** boundaries (BUY crosses at `>= best_ask`, SELL at `<= best_bid`).
- [ ] Orders resting strictly inside the spread are left untouched, not pulled to the touch.
- [ ] Tick alignment **floors BUYs and ceils SELLs** — never rounds to nearest.
- [ ] Alignment uses decimal quantization, not `round(price/tick)*tick` or a fixed decimal count.
- [ ] Submitted price verified on-tick (`price % tickSize == 0`) and strictly passive **after**
      alignment, not merely before.
- [ ] Non-positive aligned prices rejected rather than submitted.

## Churn control

- [ ] Reprice attempts capped, and the cap calibrated against the venue's published rate limits
      (not left at the arbitrary default of 3).
- [ ] Attempt count **carried across resubmissions** via `report.next_attempt(order)` — verified
      the cap actually engages in a live-shaped loop, not just in a single call.
- [ ] `REPRICE_ATTEMPTS_EXCEEDED` cancels or stands the order down; it never resets the counter
      and re-enters.
- [ ] Locked books and already-passive orders do **not** consume attempts.
- [ ] Back-off implemented for HTTP 429 / error `-1015` before an escalating IP ban.

## Caller contract

- [ ] Orders submitted **only** when `report.action == 'SUBMIT'`.
- [ ] `final_limit_price` ignored on every `'HOLD'` status.
- [ ] Queue-position loss from repricing accounted for in the fill model.

## Audit

- [ ] Report persisted with desired price, final price, BBO at decision time, tick, offset,
      velocity, status, and attempt count.
- [ ] `python -m unittest discover -s skills/post-only-limit-repricing-under-fast-markets/scripts`
      passes.
