# Standards for Deribit Crypto Options API

Every row below is drawn from Deribit's own API reference. Where Deribit does
not document a value (per-instrument minimums, current fee rates), this skill
treats it as an **input fetched from the exchange**, not as a constant to
hard-code.

## 1. Units and denomination

| Field | Documented meaning | Source |
|---|---|---|
| `amount` (options) | "For perpetual and inverse futures the amount is in USD units. For **options and linear futures it is the underlying base currency coin**." | `private/buy`, `private/get_margins` |
| `contracts` | Order size in contract units; an alternative to `amount`. One of the two is mandatory. | `private/buy` |
| `contract_size` | "Contract size for the instrument, expressed in the same unit as the order amount." USD for inverse futures/perpetuals; **base currency coin** for options, spots and linear instruments. | `public/get_instrument` |
| `min_trade_amount` | Minimum tradable size — USD for perpetual/inverse futures, **base currency** for options and linear futures. Per-instrument; not a global constant. | `public/get_instrument` |
| `tick_size`, `tick_size_steps` | Minimum price increment, with variable finer steps above configured price thresholds. Per-instrument. | `public/get_instrument` |
| `price` (options) | Order price **in base currency**. With `advanced=usd` the price is given in USD instead; with `advanced=implv`, as an implied-volatility percentage. | `private/buy` |

**Inverse premium rule.** For an inverse (coin-settled) option the quoted price
is a ratio of one coin of underlying, so
$P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index}}$.

**This does not apply to linear options.** `BTC_USDC-…` and `BTC_USDT-…`
instruments are quoted and settled in the counter currency (`counter_currency`,
`price_index` e.g. `btc_usdc`). Multiplying their premium by the index inflates
it by the index price. `parse_instrument_name` rejects them explicitly.

## 2. Greeks

`public/ticker` states the option greeks are **"calculated using standard Black
Scholes without adjustments"**. They are therefore ordinary dimensionless BS
greeks, not adjusted for coin settlement. Two consequences:

- `position_delta_coin = amount_coin × delta`, and
  `position_delta_usd = position_delta_coin × index_price` — the standard USD
  exposure convention.
- The premium is **paid and received in coin**, so the trade moves the coin
  balance by `amount_coin × price_coin` in addition to the option exposure. Coin
  exposure net of the premium leg is
  `side × amount_coin × (delta − price_coin)`.

  *Derivation:* a buyer of `N` coin-units at `P` coin parts with `N·P` coin, so
  coin exposure is `N·δ − N·P`; a seller receives it, giving `−N·δ + N·P`. Both
  are `side · N · (δ − P)`.

`mark_iv` is a percentage (65.4 means 65.4%). `underlying_price` and
`interest_rate` are the inputs Deribit used for the IV calculation.

## 3. Order semantics that change behaviour silently

| Parameter | Documented default | Why it matters |
|---|---|---|
| `post_only` | **`true`** | A post-only order that would cross is **repriced below the spread**, not filled. A "limit" order built without setting this rests passively and never takes liquidity. |
| `reject_post_only` | `false` | With `post_only=true`, rejects the order instead of repricing it. The correct choice when a missed fill is worse than no order. |
| `time_in_force` | `good_til_cancelled` | Others: `good_til_day`, `fill_or_kill`, `immediate_or_cancel`. |
| `type` | `limit` | Also `stop_limit`, `take_limit`, `market`, `stop_market`, `take_market`, `market_limit`, `trailing_stop`. `price` applies to limit/stop_limit only. |
| `label` | — | "user defined label for the order (maximum 64 characters)". The only client-supplied identifier that survives a dropped session. |
| `reduce_only` | `false` | Order may only reduce the current position. |
| `valid_until` | — | Server timestamp; the request is rejected if processing would occur after it. |

Source: `private/buy` / `private/sell` API reference.

## 4. Rate limits — exhaustion is session-fatal

Deribit uses a leaky-bucket credit system.

| Class | Limit |
|---|---|
| Non-matching-engine requests | 500 credits/request, 50,000 max pool, 10,000 credits/s refill (~20 req/s sustained, burst 100) |
| Matching-engine (order) requests | Tiered on 7-day volume, recalculated hourly: >$25M → 30 req/s (burst 100); >$5M → 20 req/s (burst 50); >$1M → 10 req/s (burst 30); ≤$1M → **5 req/s (burst 20)** |

On exhaustion Deribit returns `too_many_requests` (**code 10028**) **and
terminates the session**. Public unauthenticated requests are limited per IP,
outside the sub-account pool. `private/move_positions` is separately capped
(100,000 credits, 6/minute, 100/week).

**Consequence for order safety.** A terminated session forces a reconnect, and a
reconnect tempts a resend. Because the client never learns whether the in-flight
order was accepted, resending without first querying by `label` is how a
duplicate position is created. Set a `label` on every order.

Source: Rate Limits article, `https://docs.deribit.com/articles/rate-limits.md`.

## 5. Margin

`private/get_margins` takes `instrument_name`, `amount` and `price` and returns
`buy` and `sell` margin values plus `buy_maker_fee`, `buy_taker_fee`,
`sell_maker_fee`, `sell_taker_fee`, `min_price` and `max_price`. This is the
authoritative pre-trade margin and fee source.

This skill deliberately does **not** reimplement Deribit's short-option margin
formula. It depends on moneyness and on the account's margin model
(`private/change_margin_model`, `private/simulate_portfolio`), and a fabricated
formula on a live short-selling path is worse than no formula. A sell without a
supplied `initial_margin_coin` is refused.

**The 80% utilisation ceiling in `assets/checklist.md` is a house risk policy,
not a Deribit rule.** The exchange enforces its own margin requirements
independently and does not cap order size at a fraction of equity.

Fee rates are not restated here: Deribit's option commission is a percentage of
the **underlying**, subject to a cap expressed as a percentage of the option
premium, and both the rate and the cap have changed over time and vary by volume
tier. Read them from `maker_commission` / `taker_commission` on
`public/get_instrument`, or from the fee fields of `private/get_margins`.

## 6. Authentication

`public/auth` supports three grant types:

| `grant_type` | Required parameters |
|---|---|
| `client_credentials` | `client_id`, `client_secret` |
| `client_signature` | `client_id`, `timestamp` (ms), `signature` (HMAC-SHA256), optional `nonce`, `data` |
| `refresh_token` | `refresh_token` |

The response carries `access_token`, `token_type` (`bearer`), `expires_in`,
`refresh_token` and `scope`. This module handles no credentials and builds no
auth payloads by design — nothing here should ever hold a secret.

Sources:
- `https://docs.deribit.com/api-reference/trading/private-buy.md`
- `https://docs.deribit.com/api-reference/trading/private-get_margins.md`
- `https://docs.deribit.com/api-reference/market-data/public-get_instrument.md`
- `https://docs.deribit.com/api-reference/market-data/public-ticker.md`
- `https://docs.deribit.com/api-reference/authentication/public-auth.md`
- `https://docs.deribit.com/articles/rate-limits.md`
