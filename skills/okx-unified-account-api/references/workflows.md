# Workflows for OKX Unified Account API

## 1. Signing a private request

1. Build the timestamp with `OKXUnifiedAccountEngine.build_timestamp()` — ISO 8601 UTC,
   exactly three fractional digits, `Z` suffix. A naive `datetime` is treated as UTC;
   an aware one is converted.
2. Serialise the request body **once**. Keep that string; it is both what you sign and
   what you send. Re-serialising for the wire (different separators, different key
   order) yields a signature over bytes that were never transmitted.
3. Assemble `requestPath` including the query string for GETs.
4. `generate_signature(timestamp, method, request_path, body)` →
   `Base64(HMAC-SHA256(secret_key_utf8, timestamp + METHOD + requestPath + body))`.
5. `get_auth_headers(...)` returns all four `OK-ACCESS-*` headers, `Content-Type`, and
   `x-simulated-trading`.

**Failure triage.** Error 50102 means the timestamp was rejected. Rule out the format
first (`parse_timestamp` does this locally), then measure skew with
`clock_skew_seconds(server_time_ms, timestamp)` against `GET /api/v5/public/time`.
A signature error with a well-formed timestamp usually means one of: the secret was
Base64-decoded before signing, the query string was dropped from `requestPath`, or the
signed body differs from the transmitted body.

## 2. Separating demo from live

OKX demo trading is a header and a key pair, not a hostname. Construct one engine per
environment:

```python
live = OKXUnifiedAccountEngine(live_key, live_secret, live_pass)                       # x-simulated-trading: 0
demo = OKXUnifiedAccountEngine(demo_key, demo_secret, demo_pass, simulated_trading=True)  # x-simulated-trading: 1
```

The two failure modes are symmetric and both are loud at OKX, which is the point of
emitting the header on every request rather than only when simulating: a live key sent
with `1`, or a demo key sent with `0`, fails rather than trading in the wrong place.
`simulated_trading` defaults to `False`, so a forgotten flag means live behaviour with
live keys — never a silent paper-trading no-op that looks like a working strategy.

## 3. Multi-currency margin and liquidation risk

1. Pull balances and prices, keeping the **sign** of each currency's equity. A negative
   equity is a borrowing.
2. Pull the current discount schedule from
   `GET /api/v5/public/discount-rate-interest-free-quota` and build `OKXDiscountTier`
   brackets in ascending order of currency amount. Use the flat `discount_factor` only
   when a real schedule is unavailable, and know that it is an approximation.
3. Value each currency:
   - positive equity, tiers present → marginal bracket walk;
   - positive equity, no tiers → `balance × price × discount_factor`;
   - negative equity → `balance × price`, no haircut.
   A holding that exceeds the schedule raises rather than being valued — a stale
   schedule must fail loudly.
4. `compute_multi_currency_margin(balances, maintenance_margin_usd,
   equity_deductions_usd=..., liquidation_fee_usd=...)`:
   - `discounted_usd_equity` = the sum from step 3;
   - `adjusted_usd_equity` = discounted equity − deductions;
   - `margin_ratio_pct` = adjusted equity / (maintenance margin + liquidation fees) × 100.
5. Read the status: `> 300%` `SAFE`; `100% < r <= 300%` `MARGIN_WARNING`; `<= 100%`
   `LIQUIDATION_RISK_CALL`. Thresholds are OKX's own risk-alert and pre-liquidation
   parameters, and a ratio exactly on a threshold falls to the riskier side.
6. Read `warnings`. An entry is emitted when deductions and liquidation fees are both
   zero (the ratio is then an upper bound), when there are no balances, and when there
   is no maintenance margin requirement (the ratio is reported as infinity rather than
   a magic sentinel).

`margin_ratio_pct` is rounded to two decimals for reporting while the status is derived
from the unrounded value. Rounding can therefore only make a consumer that re-derives
status from the rounded field *more* cautious, never less.

## 4. Building an order payload

1. Fetch `ctVal`, `lotSz`, `minSz`, `tickSz` for the instrument from
   `GET /api/v5/public/instruments`. For derivatives, convert your intended exposure to
   **contracts**: `contracts = notional_base / ctVal`.
2. Round size to `lotSz` and price to `tickSz` before calling the builder — this engine
   validates shape, not exchange-specific increments. See
   `minimum-fill-size-and-lot-rounding-logic`.
3. Mint `cl_ord_id = new_client_order_id("mybot")` and **persist it before sending**.
4. `build_order_payload(inst_id, td_mode, side, ord_type, size, price, cl_ord_id=...,
   pos_side=..., tgt_ccy=...)`. The builder rejects: a limit-family order without a
   price, a market-family order carrying one, a non-alphanumeric or over-length
   `clOrdId`, a non-positive or non-finite size, and any `tdMode` / `side` / `posSide` /
   `tgtCcy` outside the documented enums.
5. Sizes and prices are rendered as plain fixed-point decimal strings. Pass `str` or
   `Decimal` when exact digits matter — a `float` can only carry the value it holds,
   and the builder reproduces that faithfully rather than rounding silently.
6. Submit at most 60 order operations per 2 seconds per UID and instrument.

## 5. Retry after a timeout

An HTTP timeout carries no information about whether OKX accepted the order.

1. Do **not** resend with a new `clOrdId` — that is a second order.
2. Query `GET /api/v5/trade/order?instId=...&clOrdId=<the persisted id>`.
3. If it exists, reconcile against its state. If it does not, resubmit **the same
   payload with the same `clOrdId`**; OKX rejects a duplicate id among pending orders,
   which is the property that makes the retry safe.

See `order-placement-idempotency` for the general pattern and for the persistence
ordering that makes step 2 possible.
