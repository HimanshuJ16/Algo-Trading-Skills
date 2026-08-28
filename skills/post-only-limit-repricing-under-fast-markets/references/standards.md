# Standards — post-only-limit-repricing-under-fast-markets

## "Post-Only" is not one behaviour (verify per venue before you rely on it)

The single most consequential fact for this skill: a Post-Only order that would cross
is **not** universally rejected. At least three distinct behaviours are in production
today, and only one of them is a harmless rejection.

| Venue family | Flag / order type | What happens when the order would cross | Source |
|---|---|---|---|
| Nasdaq, Nasdaq BX, Nasdaq PSX (US equities) | Post-Only order | **Executes as a taker at the resting order's price.** "Post-Only orders that would cross the Exchange book will be executed at the price of the resting order (i.e. the incoming Post-Only order would receive price improvement)." An order that would merely *lock* the book "will be posted on the book one tick away from the best price on the opposite side of the market." | [Nasdaq, *North American Markets — Order Types and Modifiers*](https://www.nasdaqtrader.com/content/ProductsServices/Trading/OrderTypesG.pdf) |
| Binance Spot | `LIMIT_MAKER` | **Rejected.** "This is a `LIMIT` order that will be rejected if the order immediately matches and trades as a taker. This is also known as a POST-ONLY order." | [Binance Spot API — Trading endpoints](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints) |
| Coinbase | `post_only` | **Rejected in full.** If any part of the order would take liquidity, the entire order is rejected and no part executes. Post-only SELL orders are additionally required to be priced strictly above the best bid. | [Coinbase Exchange — Orders API](https://docs.cloud.coinbase.com/exchange/docs/apis/post-orders) |
| Deribit | `post_only` (default) | **Silently re-priced by the matching engine** to one tick inside the spread. Rejection must be opted into with `reject_post_only: true` (FIX `ExecInst=6A` rather than `6`). | [Deribit — Order Management Best Practices](https://docs.deribit.com/articles/order-management-best-practices) |

**Implication.** On a Nasdaq-family venue, submitting a crossing Post-Only order does not
produce a rejection you can retry — it produces a *fill*, at taker fees, removing the
liquidity the strategy meant to provide. On Deribit it produces a fill at a price you did
not choose. Client-side pre-checking is therefore the only venue-portable guarantee, and
it is the reason this skill exists. Treat "post-only protects me from taking" as a claim
to be verified against your venue's rulebook, never as an assumption.

## Price increment (tick size) is per symbol and mutable

| Fact | Source |
|---|---|
| Binance validates the increment arithmetically: the `PRICE_FILTER` requires `price % tickSize == 0`, alongside `minPrice`/`maxPrice` bounds. An off-tick price is rejected before it reaches the book. | [Binance Spot API — Filters](https://developers.binance.com/docs/binance-spot-api-docs/filters) |
| US equities: the SEC adopted amendments to Rule 612 of Reg NMS on 18 September 2024 establishing a **second** minimum increment of $0.005 for NMS stocks priced $1.00+ whose time-weighted average quoted spread is $0.015 or less, with the primary listing exchange assigning each symbol its increment on a periodic basis. The D.C. Circuit upheld the SEC's authority on 14 October 2025, **but implementation timing remains unsettled** — the SEC has indicated compliance timelines are likely to be revisited. | [SEC Release 34-101070 fact sheet](https://www.sec.gov/files/34-101070-fact-sheet.pdf); [Sidley, Oct 2025](https://www.sidley.com/en/insights/newsupdates/2025/10/dc-circuit-upholds-sec-tick-size-fee-cap-rule) |

Because the increment is assigned **per symbol** and is subject to regime change, `tick_size`
is a required runtime input on `MarketState`. Do not hard-code $0.01, and do not cache a
symbol's tick across a reassignment date. See `exchange-tick-size-regime-tracking`.

**Rounding direction is a correctness property, not a formatting choice.** Nearest-rounding
a BUY can move it *up* onto the best ask, and a SELL *down* onto the best bid — manufacturing
exactly the crossing order the engine exists to prevent. `align_to_tick()` therefore floors
BUYs and ceils SELLs, always away from the touch.

## Message churn and order-rate limits

| Fact | Source |
|---|---|
| Binance's unfilled-order rate limit counts new orders per interval; "when an order is filled for the first time (partially or fully), your unfilled order count is decremented by one order for all intervals." | [Binance — Order Count Decrement FAQ](https://developers.binance.com/docs/binance-spot-api-docs/faqs/order_count_decrement) |
| Breaching it returns HTTP `429 Too Many Requests` with error `-1015 "Too many new orders"`. Repeatedly violating rate limits and failing to back off escalates to an automated IP ban (HTTP `418`), scaling from 2 minutes to 3 days. | [Binance — Rate limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) |
| Whether a *rejected* order consumes order-rate quota is **not guaranteed either way**: "Rejected or unsuccessful orders might or might not update the `ORDERS` rate limit type." | [Binance — Rate limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) |
| EU venues: Commission Delegated Regulation (EU) 2017/566 (MiFID II RTS 9) requires trading venues to calculate the ratio of unexecuted orders to transactions per member and per instrument, and permits venues to set maximum ratios to prevent disorderly trading. The specific limits are set by each venue, not by the regulation. | [Delegated Regulation (EU) 2017/566](https://eur-lex.europa.eu/eli/reg_del/2017/566/oj) |

**Why post-only churn is the worst case for an order-rate budget.** The decrement above is
earned by *filling*. A Post-Only order that is rejected for crossing never fills, so it can
never be decremented — a rejection loop consumes quota monotonically with nothing returned.
Combined with the "might or might not" ambiguity, the only safe design is to bound the loop
locally rather than to reason about which rejections are free.

## Configuration defaults (calibrate before use)

These are this library's defaults, **not** venue mandates or regulatory thresholds. No
regulator or exchange publishes a required reprice cap or a numeric definition of a "fast
market". Calibrate against your venue's published rate limits and your own fill statistics.

| Parameter | Default | What it actually does |
|---|---|---|
| `max_reprice_attempts` | `3` | Consecutive reprices allowed per order cycle before the engine returns `action='HOLD'`. An arbitrary, deliberately small budget — the point is that the loop is *bounded*, not that 3 is optimal. |
| `fast_market_velocity_threshold` | `20.0` ticks/sec | Local classification only. Purely a knob for the offset below; it does not gate any safety check. |
| `fast_market_offset_ticks` | `0` | Extra ticks away from the touch when repricing during a fast market. `0` joins the near touch. Raising it trades fill probability for resistance to being re-crossed while the order is in flight. |

## Engineering invariants enforced by this skill

| Invariant | Enforcement |
|---|---|
| A BUY is submitted only at a price strictly **below** `best_ask`; a SELL strictly **above** `best_bid`. | Asserted after tick alignment; violation returns `NO_VALID_PASSIVE_PRICE` with `action='HOLD'`. |
| The submitted price is an exact multiple of the symbol's tick. | `align_to_tick()` quantizes with `Decimal`; unit-tested against `price % tickSize == 0`. |
| Reprices per order cycle are bounded. | `max_reprice_attempts`, carried across submissions by `report.next_attempt(order)`. |
| No order is emitted against a locked or crossed book. | `BOOK_LOCKED_OR_CROSSED`, `action='HOLD'`. |
| Non-finite, non-positive, or malformed market data never reaches price arithmetic. | `__post_init__` validation on `MarketState` / `OrderRequest` / `Config`. |

## Known limitations

- **Single venue, top of book.** The engine reasons about one `best_bid`/`best_ask` pair. It
  does not consult a consolidated NBBO, so it cannot prevent a price that is passive on the
  target venue from locking or crossing a protected quotation elsewhere.
- **Snapshot-based, not latency-aware.** The decision is correct for the BBO handed to it. A
  quote update in flight can still leave the submitted price marketable on arrival;
  `fast_market_offset_ticks` mitigates but cannot eliminate this.
- **Repricing forfeits queue position.** Every reprice is a new order at the back of the
  queue at that price level. See `queue-position-modeling-for-passive-orders`.
- **No venue rejection is modelled.** The engine prevents *client-side* crossing errors. It
  does not classify or handle venue rejections that arrive anyway (self-match prevention,
  price bands, entitlement, credit).

## Category

`execution-algorithms`
