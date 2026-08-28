# Workflows — post-only-limit-repricing-under-fast-markets

## 0. Establish what your venue actually does with a crossing Post-Only order

Do this **once per venue, before writing any repricing logic**, and record the answer.
The three behaviours in `standards.md` demand different handling:

- **Rejects** (Binance `LIMIT_MAKER`, Coinbase `post_only`) — a crossing order costs a
  message and an order-rate slot, and returns nothing.
- **Executes as a taker** (Nasdaq / BX / PSX) — a crossing order costs *money*: taker fees
  plus the forgone maker rebate, and it removes liquidity the strategy meant to provide.
- **Silently re-prices** (Deribit default `post_only`) — the order rests at a price the
  strategy never chose, invalidating any quoting model that assumes its own price is live.

If you cannot confirm the behaviour from the venue's rulebook or API docs, assume the
**executes-as-taker** case: it is the most expensive, and designing for it is safe under
all three.

## 1. Source the symbol's tick size at runtime

Fetch `tick_size` from the venue's instrument/reference-data endpoint per symbol, not from
a constant. US NMS increments are assigned per symbol and are subject to regime change
(`standards.md`); crypto ticks vary by orders of magnitude across pairs. Refresh on the
venue's reassignment schedule — see `exchange-tick-size-regime-tracking`.

## 2. Validate the book snapshot before pricing against it

Reject non-finite, zero, or negative bids/asks/ticks. `MarketState.__post_init__` does this,
and it is deliberately an exception rather than a status: a `NaN` best bid is a feed
defect, not a market condition, and it must not be quietly priced around.

## 3. Detect a locked or crossed book — this is a HOLD, not a reprice

If `best_bid >= best_ask` there is **no passive price on either side**: repricing a BUY to
`best_bid` still leaves it at or above `best_ask`. Fast markets and multi-venue feeds
produce locked and crossed books routinely.

**Decision point:** do not reprice, and do not retry in a tight loop. Return
`BOOK_LOCKED_OR_CROSSED` / `action='HOLD'` and wait for the next quote update. A crossed
book frequently means a stale or gapped feed rather than a real market — validate the feed
before trusting the next snapshot (see `sequence-number-gap-detection-for-feeds`).

## 4. Test for crossing at the correct boundary — it is inclusive

- A **BUY** takes liquidity at `desired_price >= best_ask`.
- A **SELL** takes liquidity at `desired_price <= best_bid`.

Both boundaries are inclusive: an order priced *at* the opposite touch matches it. An order
resting strictly inside the spread is passive and must be left alone — repricing it to the
touch would needlessly surrender price improvement.

## 5. Reprice to the passive boundary, then apply the fast-market offset

- **BUY** → `best_bid`; **SELL** → `best_ask`.
- If the book is classified fast (`market_velocity_ticks_per_sec >=
  fast_market_velocity_threshold`), move a further `fast_market_offset_ticks` **away from
  the touch**.

**Decision point — the offset is a real trade-off, not a free safety margin.** Joining the
touch (`offset = 0`) maximises fill probability but is the price most likely to be crossed
by the next update while the order is in flight. Backing off reduces re-cross risk and
reduces fills. Default is `0`; raise it only with measured evidence of in-flight re-crossing.

The offset applies **only when repricing**, never to an order that was already passive.

## 6. Align to the tick, always away from the touch

Floor a BUY, ceil a SELL. **Never round to nearest**: nearest-rounding moves a BUY up onto
the ask and a SELL down onto the bid, manufacturing the crossing order the whole workflow
exists to prevent.

Quantize with `Decimal`, not float arithmetic. `round(price / tick) * tick` reintroduces
binary representation error, and a fixed `round(price, 4)` silently annihilates prices on
sub-satoshi ticks. Venues validate the increment arithmetically (`price % tickSize == 0`).

## 7. Re-assert the passivity invariant after alignment

Alignment away from the touch *should* preserve passivity, but a coarse tick, an off-tick
quote, or a sub-tick bid can still produce a price that is marketable or non-positive.
Check it explicitly; on violation return `NO_VALID_PASSIVE_PRICE` / `action='HOLD'` rather
than submitting a price you cannot justify.

## 8. Bound the loop, and carry the attempt count forward

**Decision point — the churn cap only works if the caller carries state.** The engine does
not mutate the order it is given. On each resubmission, rebuild the order with
`report.next_attempt(order)`; otherwise `reprice_attempts` stays at zero forever and the cap
never engages.

Only a genuine reprice consumes an attempt. A locked book or an already-passive order does
not — those are not churn.

On `REPRICE_ATTEMPTS_EXCEEDED`, **cancel or stand down**; do not reset the counter and
re-enter. A post-only order that keeps crossing is telling you the price level is moving
faster than your quote loop, and continuing to push messages at it is precisely what
exhausts an order-rate budget: a rejected post-only order never fills, so it never earns
back the rate-limit decrement that a filled order does (`standards.md`).

## 9. Submit only on `action == 'SUBMIT'`

`action` is the caller's contract. `'HOLD'` covers exhausted attempts, locked books, and
unavailable passive prices; in every `'HOLD'` case `final_limit_price` echoes the original
desired price and must **not** be sent.

## 10. Record the audit trail

Persist the report: original desired price, final limit price, BBO at decision time, tick
size, offset applied, velocity, fast-market classification, status, and attempt count. This
is what lets you answer, after the fact, whether a fill was taken deliberately and whether
the repricing loop or the strategy caused a rate-limit breach.
