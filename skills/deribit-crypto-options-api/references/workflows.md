# Workflows for Deribit Crypto Options API

## 1. Resolve the instrument before anything else

`parse_instrument_name` validates the symbol against
`<CURRENCY>-<DDMMMYY>-<STRIKE>-<C|P>`. Two rejections are deliberate:

- **An unrecognised option-type suffix raises.** It must never fall through to
  "put".
- **Linear `BTC_USDC-…` / `BTC_USDT-…` options raise.** Their premium is already
  in the counter currency; the inverse coin→USD conversion does not apply.

Fetch `contract_size`, `min_trade_amount`, `tick_size` and the commission rates
from `public/get_instrument`. They are per-instrument and must not be hard-coded.

## 2. Read the ticker, and check it matches

`public/ticker` supplies `index_price_usd`, `mark_price_coin`, `mark_iv` and the
greeks. `process_option_order` refuses a ticker whose `instrument_name` differs
from the order's — a mismatched ticker prices and hedges the wrong contract, and
nothing downstream would notice.

## 3. Convert the premium

$P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index}}$, for inverse options
only.

## 4. Compute both deltas

- `position_delta_coin = amount_coin × delta` (signed by side), and
  `position_delta_usd = position_delta_coin × index_price`. This is the standard
  USD-exposure convention and matches Deribit's position view.
- `net_coin_delta_after_premium = side × amount_coin × (delta − price_coin)`.
  Because the premium settles in coin, the trade carries a coin leg of
  `amount_coin × price_coin` — paid by the buyer, received by the seller. A hedge
  sized on `delta` alone misses it.

Deribit's greeks are standard Black-Scholes **without adjustment** for coin
settlement, which is exactly why the second figure has to be computed here.

## 5. Get a margin quote — mandatory for sells

Call `private/get_margins` with `instrument_name`, `amount` and `price`. Feed
`buy`/`sell`, the matching fee estimate, and `min_price`/`max_price` into a
`DeribitMarginQuote`.

- **Buy:** required capital = premium + fee.
- **Sell:** required capital = initial margin + fee. **Without
  `initial_margin_coin` the order is rejected.** Short option margin is not
  modelled here and will not be guessed.

## 6. Gate the order

`process_option_order` rejects on any of: insufficient balance, breach of the
house utilisation ceiling (default 80% — a local policy, not an exchange rule),
a price outside the venue band, or a sell without a margin quote. It warns on:
a crossing `post_only` order (Deribit will reprice rather than fill it), a market
order (figures estimated from mark), and a missing margin quote (fees treated as
zero).

Read `rejection_reasons` and `warnings`, not just the boolean.

## 7. Dispatch — and understand what approval means

`is_approved_for_dispatch` is a statement about the inputs supplied. **Nothing in
this module sends anything**, and the approval is stale the moment the market
moves. The caller owns transport, authentication and re-validation.

Every payload carries the caller's `order_id` as Deribit's `label` (≤ 64 chars),
and emits `post_only` explicitly because Deribit's default is `true`.

## 8. Handle a dropped session correctly

Order-rate exhaustion returns `too_many_requests` (code 10028) **and terminates
the session**. After any disconnect with an order in flight:

1. Reconnect and re-authenticate.
2. **Query by `label` first.** Determine whether the order exists.
3. Only resend if it does not.

Never resend on reconnect alone — the timeout says nothing about whether the
matching engine accepted the order.

## 9. Aggregate portfolio risk

`aggregate_portfolio_greeks` sums signed `size_coin × greek` for delta, gamma,
vega and theta, plus the net coin delta. All positions must share one index
price; a single USD delta spanning two underlyings is meaningless and raises.
