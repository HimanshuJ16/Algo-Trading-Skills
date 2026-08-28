# Workflows — post-only-and-maker-taker-fee-optimization

This file holds the full technical procedure referenced by `SKILL.md`.

## 0. Sign convention

Fix this first, because every downstream figure depends on it:

```
rate > 0  ->  fee:    the venue CHARGES the desk
rate < 0  ->  rebate: the venue CREDITS the desk
```

A signed differential (`taker - maker`) is therefore positive when posting is cheaper,
zero when the tier prices both sides the same, and **negative when post-only is the more
expensive side**. It is never clamped. The same convention is used by
`exchange-fee-tier-and-rebate-structure-analysis` and
`market-maker-vs-taker-strategy-classification`.

## 1. Configure the venue and the schedule

```python
from fee_optimizer import (
    CrossingPolicy, FeeSchedule, MakerTakerFeeOptimizer, OrderSide, TopOfBook, Venue,
)

optimizer = MakerTakerFeeOptimizer(
    Venue.COINBASE_ADVANCED,
    FeeSchedule(maker_fee_rate=0.0025, taker_fee_rate=0.0040),  # your tier, not a guess
)
```

Both arguments are required. The payload shape depends on the venue, and there is no fee
schedule that is safe to assume — Binance's spot Regular tier charges 0.100% on both
sides, so a default "5 bps maker / 25 bps taker" would manufacture savings that do not
exist.

One optimizer instance models one venue at one tier. Do not aggregate realized
differentials across venues on a single instance.

## 2. Validate the inputs

Performed on every call, raising `PostOnlyOrderError` (a `ValueError`):

- `side` must be an `OrderSide`. This is the one that matters: the crossing check is a
  two-branch comparison, and a free-text side matching neither branch (`"B"`,
  `"BUY_TO_COVER"`, a config file's `" BUY"`) would skip the check entirely and submit the
  marketable price unchanged.
- `quantity` and `limit_price` must be finite and strictly positive. `bool` is rejected as
  a number — `True` is a valid `int` to Python and would pass as a quantity of 1.
- `book` must be a `TopOfBook`, whose quotes are validated at construction. A raw `dict`
  is rejected rather than silently missing the crossing check.
- If `tick_size` is supplied, an off-grid `limit_price` is rejected here rather than by the
  venue.

## 3. Test marketability — inclusively

```
BUY  is marketable  <=>  limit_price >= best_ask
SELL is marketable  <=>  limit_price <= best_bid
```

Both bounds are **inclusive**: a limit exactly equal to the opposite touch trades against
it. A price strictly inside the spread is not marketable; it rests ahead of the touch and
is submitted unchanged.

A locked or crossed snapshot (`best_bid >= best_ask`) always raises a warning on the
result, whether or not it changes the outcome.

## 4. Apply the crossing policy

| Policy | Behaviour | Choose when |
|---|---|---|
| `REPRICE_PASSIVE` (default) | Move the price to the near touch — `best_bid` for a buy, `best_ask` for a sell. Status `POST_ONLY_PAYLOAD_REPRICED`. | The fill is optional and joining the queue is acceptable. |
| `REJECT` | Emit no payload. Status `POST_ONLY_REJECTED_WOULD_CROSS`. | The trade is only worth doing at the requested price, and the alternative is an explicit taker order. |

Repricing produces a **different order**. It no longer takes the liquidity the original
price was reaching for; it joins the back of the queue at the touch and may never fill.
`spread_capture_if_filled_usd` reports the bid-ask difference it would capture *if* it
fills, which is not the same thing as an expectation.

**Locked/crossed books short-circuit repricing.** With `best_bid >= best_ask`, the near
touch is itself marketable, so there is no passive price to move to. The engine returns
`POST_ONLY_REJECTED_LOCKED_OR_CROSSED_BOOK` with an empty payload rather than emitting one
the venue is certain to cancel.

## 5. Build the venue payload

One venue's spelling per payload — see `references/standards.md` for the table and its
sources. `venue_params` merges caller-supplied fields (Bybit's mandatory `category`, a
client order id, an account id) and **refuses** any key that would overwrite:

- the post-only instruction — overwriting it submits a plain limit order that can cross;
- the price or quantity — overwriting either makes the payload describe a different order
  from the validated, crossing-checked result returned alongside it.

Bybit v5 without `category` raises rather than emitting a request the API rejects.

## 6. Read the fee figures as counterfactuals

$$\text{differential} = Q \times P_{\text{touch}} \times r_{\text{taker}} - Q \times P_{\text{submitted}} \times r_{\text{maker}}$$

where $P_{\text{touch}}$ is the price the order **would have crossed at** — `best_ask` for
a buy, `best_bid` for a sell — not the order's own limit. Crossing a buy pays the ask.

Worked example (maker 5 bps, taker 25 bps; bid 60,000 / ask 60,010; 2.0 units posted at
the bid):

```
maker fee if filled     = 2.0 * 60,000 * 0.0005 = $60.00
counterfactual taker fee= 2.0 * 60,010 * 0.0025 = $300.05
differential if filled  = 300.05 - 60.00        = $240.05
```

Every field named `_if_filled_usd` is conditional on a fill that post-only cannot
guarantee. **Do not accumulate them.** Preparing a payload never moves
`realized_fee_differential_usd`.

## 7. Accrue realized amounts from fills only

```python
optimizer.record_maker_fill(
    filled_quantity=0.30,
    fill_price=59_998.50,
    taker_reference_price=60_001.50,  # the touch captured at decision time
    fill_id="exec-8837412",           # the venue's execution id
)
```

Call once per fill report, on filled quantity — partial fills accrue partially.
`taker_reference_price` defaults to `fill_price`, which understates the taker cost whenever
the spread was non-zero, so the default is a lower bound rather than an estimate.

Supply `fill_id`. Overlapping paginated fill fetches are the ordinary way one fill arrives
twice, and a double-counted fill inflates the realized total with nothing in the output to
show it; a repeated id is rejected. Without an id no deduplication is possible and the
caller owns that risk. A fill that fails validation is not recorded and does not consume
its id.

## 8. Handle the rejection, and do not loop on it

Rejection semantics differ by venue (synchronous rejection on Binance spot, an
asynchronous `EXPIRED` update on Binance USD-M futures, a cancel on Bybit), so **a
successful submission response is not evidence that an order is resting.** Confirm from the
order-state stream.

The correct follow-up to a post-only rejection is a classification, not a resubmission:

1. Is the trade still worth doing at the current touch? Send an explicit taker order and
   book the taker fee deliberately.
2. Is it not? Drop it and re-evaluate on the next signal.

Blind resubmission at a crossing price is the cancellation loop, and it inflates the
order-to-trade ratio into venue message-rate penalties — see
`order-to-trade-ratio-fee-penalty-avoidance`. Under sustained fast markets the repricing
cadence itself is the problem being solved in
`post-only-limit-repricing-under-fast-markets`. Never re-send on an ambiguous submission
result without an idempotency key (`order-placement-idempotency`).

## Production Implementation Reference

- Reference code: `scripts/fee_optimizer.py` (`MakerTakerFeeOptimizer`, `FeeSchedule`,
  `TopOfBook`, `Venue`, `OrderSide`, `CrossingPolicy`, `PostOnlyStatus`,
  `PostOnlyOrderResult`, `PostOnlyOrderError`).
- Automated unit tests: `scripts/test_fee_optimizer.py`.
