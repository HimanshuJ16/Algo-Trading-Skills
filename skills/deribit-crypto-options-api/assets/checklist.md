# Pre-Flight Checklist

## Instrument and units
- [ ] Is the symbol parsed and validated, with an unrecognised option-type suffix
      raising rather than defaulting to "put"?
- [ ] Is the instrument confirmed to be an **inverse** option? Linear
      `BTC_USDC-…` / `BTC_USDT-…` premiums must not be multiplied by the index.
- [ ] Is `amount` expressed in the **underlying base currency coin** (Deribit's
      unit for options), not in a contract count?
- [ ] Are `contract_size`, `min_trade_amount` and `tick_size` read from
      `public/get_instrument` rather than hard-coded?
- [ ] Does the ticker's `instrument_name` match the order's?

## Pricing and Greeks
- [ ] Is $P_{\text{USD}} = P_{\text{coin}} \times S_{\text{index}}$ applied for
      inverse options only?
- [ ] Are `position_delta_coin` and `position_delta_usd` computed from the
      unadjusted Black-Scholes delta Deribit returns?
- [ ] Is the coin-settled premium leg accounted for —
      `side × amount_coin × (delta − price_coin)` — and not just `delta`?
- [ ] Are gamma, vega and theta aggregated, not captured and discarded?
- [ ] Do all positions in a portfolio aggregation share one index price?

## Order construction
- [ ] Is `post_only` set **explicitly**? Deribit defaults it to `true` and
      reprices a crossing order instead of filling it.
- [ ] Is `reject_post_only` set when a missed fill is worse than no order?
- [ ] Does every order carry a `label` (≤ 64 chars) so it can be found after a
      dropped session?
- [ ] Do market order types omit `price` entirely?
- [ ] Is each JSON-RPC `id` unique per connection, so responses can be correlated?
- [ ] Is `time_in_force` one of the four documented values?

## Capital and margin
- [ ] Does a **buy** check premium **plus commission** against available balance?
- [ ] Does a **sell** carry an `initial_margin_coin` from `private/get_margins`,
      and get rejected without one? Short option margin must never be guessed.
- [ ] Are commission rates read from `public/get_instrument` or
      `private/get_margins` rather than hard-coded?
- [ ] Is the price inside the `min_price` / `max_price` band?
- [ ] Is the house utilisation ceiling applied — and understood as a local policy
      rather than a Deribit rule?

## Session and dispatch safety
- [ ] Is it understood that approval is **not** dispatch, and that it goes stale
      as the market moves?
- [ ] On `too_many_requests` (code 10028) the session is **terminated** — is the
      reconnect path querying by `label` before any resend?
- [ ] Is the order request rate inside the account's volume tier (5 req/s at the
      base tier, burst 20)?
- [ ] Is `is_testnet` deliberately set, with production opted into rather than
      defaulted to?
- [ ] Are credentials handled outside this module, via `public/auth`?
