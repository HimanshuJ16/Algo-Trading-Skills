---
name: deribit-crypto-options-api
description: >-
  Use when trading Deribit inverse coin-settled BTC and ETH options: JSON-RPC 2.0
  payload construction with post_only and order labels, coin-to-USD premium conversion,
  and portfolio Greeks including the coin-settlement term.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: deribit, crypto-options, json-rpc-2.0, inverse-options, btc-options, eth-options, option-greeks, mark-iv
  brokers_frameworks: "Deribit API v2; JSON-RPC 2.0; Python Dataclasses"
  version: "1.1.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when building crypto options bots, market makers or delta-hedging
systems against Deribit's **inverse** (coin-settled) BTC and ETH options. It
builds the exact JSON-RPC 2.0 payload an order must carry, converts
coin-denominated premiums to USD, aggregates portfolio Greeks, and gates the
order on a pre-trade capital check before anything is sent.

It exists because Deribit's inverse contracts break three assumptions that
carry over from linear venues: the premium is quoted and settled in the
underlying coin, the Greeks are unadjusted for that, and `post_only` defaults to
`true` so a "limit" order meant to cross rests passively instead.

## When NOT to Use

- **Not a client.** No socket, no HTTP, no credential handling. It returns
  payloads and an approve/reject decision; the caller sends them. Approval is a
  statement about the inputs supplied, and is stale as soon as the market moves.
- **Not for linear options.** `BTC_USDC-…` / `BTC_USDT-…` premiums are already
  quoted in the counter currency; the parser rejects them rather than
  mis-converting them.
- **Not for futures or perpetuals.** Their `amount` is in USD, not coin.
- **Not a margin model.** Short-option initial margin comes from
  `private/get_margins`. This module refuses to guess it.
- **Not a risk system.** The utilisation ceiling is a single-order sanity check,
  not portfolio risk management.

## Prerequisites

- Deribit API v2 access, authenticated separately via `public/auth`
  (`client_credentials`, `client_signature` or `refresh_token`). This module
  never sees a secret.
- Per-instrument specs from `public/get_instrument`: `contract_size`,
  `min_trade_amount`, `tick_size`, `maker_commission`, `taker_commission`.
  These are per-instrument and must not be hard-coded.
- A `public/ticker` snapshot for the instrument being traded.
- For any **sell**: a `private/get_margins` result supplying
  `initial_margin_coin` and the fee estimate.

## Workflow

1. **Parse and validate the instrument.** `parse_instrument_name` enforces
   `<CURRENCY>-<DDMMMYY>-<STRIKE>-<C|P>`. An unrecognised option-type suffix
   raises rather than silently becoming a put; linear USDC/USDT symbols are
   rejected outright.
2. **Match the ticker to the order.** A ticker for a different instrument raises
   — otherwise the engine prices and hedges the wrong contract and nothing
   downstream notices.
3. **Convert the premium.** $P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index}}$,
   for inverse options only.
4. **Compute both deltas.** `position_delta_coin` $= \text{amount}_{\text{coin}} \times \delta$
   and its USD form are the standard convention. Then
   `net_coin_delta_after_premium` $= \text{side} \times \text{amount}_{\text{coin}} \times (\delta - P_{\text{coin}})$:
   the premium settles in coin, so a buyer parts with $\text{amount} \times P$
   coins and a seller receives them. Deribit's Greeks are standard Black-Scholes
   *without adjustment* for coin settlement, so this leg has to be added here.
5. **Quote the margin, then gate.** Buy requires premium + fee; sell requires
   initial margin + fee and **is rejected without `initial_margin_coin`**. The
   engine also rejects on venue price-band breach and on the house utilisation
   ceiling, and warns on a crossing `post_only` order or a market order priced
   off mark. Read `rejection_reasons` and `warnings`, not just the boolean.
6. **Build the payload.** `post_only` is always emitted explicitly, the
   `order_id` travels as Deribit's `label`, market types omit `price`, and each
   JSON-RPC `id` is unique per engine.
7. **On a dropped session, query before resending.** Order-rate exhaustion
   returns code 10028 *and terminates the session*. Reconnect, look the order up
   by `label`, and only resend if it is absent.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Approving a short with no margin.** Selling an option is not funded by the
  premium received — it requires initial margin. A balance check written for the
  buy side only will approve a naked short against an empty account. Sells must
  carry a `private/get_margins` figure or be refused.
- **Treating `post_only` as off by default.** Deribit defaults it to `true` and
  **reprices** a crossing order away from the touch instead of filling it (unless
  `reject_post_only=true`, which rejects it). A hedging bot that omits the field
  gets a resting maker order and an unhedged position.
- **Reading premium quotes as USD.** A 0.05 quote is 0.05 BTC per BTC of
  underlying — at a $60,000 index that is $3,000, not $0.05. The same mistake in
  reverse applies to linear USDC options, whose price already *is* the
  settlement currency.
- **Hedging on `delta` alone — the inverse delta drift.** Deribit's Greeks are
  unadjusted Black-Scholes. The coin-settled premium is itself a coin position:
  buying 10 BTC of a 0.05 option leaves $10 \times 0.60 - 10 \times 0.05 = 5.5$
  BTC of coin exposure, not 6.0.
- **Resending an order after a timeout.** Code 10028 terminates the session, so a
  reconnect is expected — and a blind resend is how a duplicate position is
  opened. The label exists precisely so the order can be found first. See
  `order-placement-idempotency`.
- **Sending `price` on a market order,** or reusing JSON-RPC `id` `1` across
  concurrent requests — responses on a multiplexed socket are correlated by `id`
  alone and become unattributable.
- **Confusing `amount` with a contract count.** For options Deribit's `amount` is
  in the underlying base currency coin; `contracts` is a separate parameter.

## Verification

- Instantiate `DeribitCryptoOptionsApiEngine()`. For `BTC-28MAR26-60000-C` with
  index $60,000 and a 0.05 BTC price, buying `amount_coin=10`: expect
  `price_usd_equivalent` $3,000, `total_premium_coin` 0.50, `total_premium_usd`
  $30,000, `position_delta_coin` 6.0, `position_delta_usd` $360,000, and
  `net_coin_delta_after_premium` 5.5.
- Safety check: the same order as a **sell** with no `margin_quote` must return
  `is_approved_for_dispatch=False` even with a large balance.
- Safety check: `parse_instrument_name("BTC-28MAR26-60000-X")` and
  `parse_instrument_name("BTC_USDC-28MAR26-60000-C")` must both raise.
- Payload check: `format_json_rpc_order` output must contain `label` and an
  explicit `post_only`, and successive calls must produce distinct `id` values.
- Run `python -m unittest discover -s skills/deribit-crypto-options-api/scripts`.

## Related Skills

- `options-implied-volatility-surface-construction`
- `crypto-exchange-api-integration`
- `order-placement-idempotency`
- `options-greeks-real-time-portfolio-aggregation`
