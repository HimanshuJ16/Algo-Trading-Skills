# Standards — post-only-and-maker-taker-fee-optimization

## There is no portable post-only flag

Every venue spells post-only differently, and the differences are not cosmetic: the
parameter, the *type* of parameter (order type vs. time-in-force vs. a nested boolean vs.
an order flag), and the rejection semantics all differ. Sending the union of every
spelling is not a safe fallback — venues commonly ignore unknown fields, and an ignored
post-only flag does not fail loudly. It submits a plain limit order that crosses and is
billed at the taker rate.

| Venue / protocol | Post-only expression | Where it sits | What happens if it would cross |
|---|---|---|---|
| [Binance Spot](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints) | `type="LIMIT_MAKER"` | Order **type**. Spot `timeInForce` accepts only `GTC`/`IOC`/`FOK`; there is no post-only TIF on spot. | Documented as "rejected if the order immediately matches and trades as a taker" — a synchronous rejection. |
| [Binance USD-M Futures](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api) | `timeInForce="GTX"` on `type="LIMIT"` | **Time-in-force**. GTX = Good-Till-Crossing. | Accepted with status `NEW`, then an asynchronous order update with execution type and status `EXPIRED`. |
| [Bybit v5](https://bybit-exchange.github.io/docs/v5/order/create-order) | `timeInForce="PostOnly"` | **Time-in-force**. `category` (`spot`/`linear`/`inverse`/`option`) is mandatory on every create-order request; `qty` and `price` are **strings**; `side` is `Buy`/`Sell`. | "If the order would be filled immediately when submitted, it will be cancelled." |
| [Coinbase Advanced Trade](https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/orders/create-order) | `post_only: true` | Nested **boolean** inside `order_configuration.limit_limit_gtc` (alongside `base_size`/`quote_size` and `limit_price`). | Per Coinbase's trading rules, a post-only limit order is posted to the book only if it would not be a taker order. |
| [Kraken Spot](https://docs.kraken.com/api/docs/rest-api/add-order/) | `oflags="post"` | **Order flag**, valid only when `ordertype="limit"`. | The order is cancelled if it would take liquidity on arrival. |
| [FIX 4.4](https://www.onixs.biz/fix-dictionary/4.4/tagNum_18.html) | `ExecInst` (tag 18) value `6` — "Participate don't initiate" | **ExecInst**, not TimeInForce. Tag 18 is a `MultipleValueString`, so `6` may appear space-delimited alongside other instructions. | Venue-defined. Confirm against the counterparty's rules of engagement. |

**The wire value is `6`.** `"ParticipateDoNotInitiate"` is the human-readable name of that
enumeration, not a value any FIX engine accepts in tag 18.

**Interactive Brokers has no general post-only attribute.** The TWS API `Order` class
documents exactly one post-only behaviour: `notHeld`, described as tagging orders routed
to IBDARK as "post only", *for IBDARK orders only*
([Order class reference](https://interactivebrokers.github.io/tws-api/classIBApi_1_1Order.html)).
Do not model IBKR as a generic post-only venue; confirm behaviour per destination.

## A maker-taker differential is not a given

The premise "taker costs multiples of maker" is a property of a specific venue at a
specific tier, not a property of maker-taker schedules. Binance's published spot schedule
charges the Regular (VIP 0) tier **0.100% maker and 0.100% taker**
([Binance fee schedule](https://www.binance.com/en/fee/schedule)) — on that venue and tier,
post-only changes the fee bill by exactly zero and only changes fill behaviour.

Specific per-tier rates are deliberately not reproduced beyond that one illustrative
figure: they change by rule filing and promotion, and a stale rate in a reference file is
worse than no rate. Read your own account's schedule and pass it in.

Venue *orientation* also varies. On an inverted (taker-maker) venue the maker side is
charged and the taker side is credited, so post-only is the more expensive side. See
`exchange-fee-tier-and-rebate-structure-analysis` for the current orientation of US equity
venues and for the Reg NMS access-fee-cap position; nothing in this module hard-codes a
rate, an orientation, or a compliance date.

## Engineering standards

| Concern | Standard applied here |
|---|---|
| Sign convention | Every rate and USD amount is signed: `> 0` charged to the desk, `< 0` credited. Consistent with `exchange-fee-tier-and-rebate-structure-analysis` and `market-maker-vs-taker-strategy-classification`. |
| Fee schedule | Required, never defaulted. A plausible default is how a fabricated savings figure reaches a report. |
| Differential | `taker - maker`, signed and **never clamped at zero**, so an inverted schedule reads as the cost it is. |
| Taker counterfactual | Priced at the touch the order would have crossed against (a buy pays the ask), not at the order's own limit. Top-of-book only, so it understates the cost of taking for size beyond displayed depth. |
| Savings accounting | Estimates are conditional and are never accumulated. Realized amounts accrue only from reported fills, on filled quantity. |
| Side handling | An enum. A free-text side that matches neither branch would skip the crossing check entirely. |
| Marketability bounds | Inclusive on both sides: a limit equal to the opposite touch trades against it. |
| Locked/crossed books | `bid >= ask` leaves no passive price at the touch; rejected rather than repriced into a certain venue-side cancel. |
| Payload integrity | One venue's spelling per payload. Caller-supplied fields may not overwrite the post-only instruction, the price, or the quantity. |
| Numerical handling | Non-finite and non-positive prices/quantities rejected at construction; arithmetic carried in full precision and rounded once at the reporting boundary; a quantity that would serialise to `"0"` for a string-typed API is rejected. |

## Known limitations

- **Top-of-book only.** No depth, hidden liquidity, queue position, or fill probability.
- **Snapshot-based.** The book is read once. Between the snapshot and arrival the touch can
  move, and the venue has the final word — that race is
  `post-only-limit-repricing-under-fast-markets`.
- **Fee term only.** Adverse selection is usually the larger term of passive execution
  cost; see `adverse-selection-measurement-for-passive-orders`.
- **One venue per instance.** Realized differentials assume a single fee schedule.
- **Positive prices only.** A negative settlement price would invert every derived figure.
