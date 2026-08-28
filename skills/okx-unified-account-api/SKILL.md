---
name: okx-unified-account-api
description: >-
  OKX v5 Unified Account integration covering HMAC-SHA256 request signing, live/demo
  environment separation via x-simulated-trading, tiered multi-currency discount and
  margin-ratio arithmetic for multi-currency margin mode, and idempotent, validated
  /api/v5/trade/order payload construction.
domain: Broker & Exchange Integration
subdomain: Crypto Unified Account & Margin Management
tags: ["okx", "unified-account", "v5-api", "hmac-sha256", "multi-currency-margin", "crypto-derivatives", "margin-ratio", "clordid-idempotency"]
brokers_frameworks: ["OKX REST API v5", "Python HMAC & Hashlib", "Python Decimal", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a bot authenticates against OKX v5 private endpoints, sizes or
submits orders on a Unified Account, or monitors cross-margin liquidation risk
between balance polls. It covers four surfaces that are easy to get subtly wrong:

1. **Signing** — the `OK-ACCESS-SIGN` prehash is `timestamp + METHOD + requestPath + body`,
   HMAC-SHA256 over the **raw** secret key, Base64-encoded. GET query parameters are
   part of `requestPath`, not the body.
2. **Environment separation** — OKX demo trading is the `x-simulated-trading` header
   plus a demo-specific API key, not a separate host. The header and the key can be
   mismatched independently, so both are made explicit here.
3. **Margin arithmetic** — in multi-currency margin mode (`acctLv = 3`) collateral is
   haircut by a **tiered** discount rate applied marginally by currency amount, and
   the maintenance margin ratio drives OKX's 300% risk alert and 100% liquidation.
4. **Order payloads** — `clOrdId` is the only idempotency handle OKX offers, `px`
   applies to a specific subset of order types, and `sz` means *contracts* for
   derivatives.

## When NOT to Use

- **As a replacement for `GET /api/v5/account/balance`.** OKX liquidates against its
  own `adjEq` and `mgnRatio`. This engine is a local approximation for pre-trade
  gating and between-poll alerting; when the two disagree, OKX wins.
- **For portfolio margin mode (`acctLv = 4`).** Portfolio margin computes maintenance
  margin per *risk unit* with offsets across instruments; the linear model here does
  not represent it and will overstate available margin.
- **For isolated-margin risk.** Isolated positions carry their own margin and are
  excluded from cross-margin adjusted equity — model them separately.
- **As an HTTP client.** No transport, no retries, no rate limiting, no order-state
  reconciliation. See `order-placement-idempotency` and `multi-broker-rate-limit-handling`.
- **For OKX spread trading, algo orders, or block trades.** Those endpoints take
  different payload shapes that `build_order_payload` deliberately rejects.

## Prerequisites

- An OKX v5 API key, secret key, and passphrase, with the **trade** permission and an
  IP allowlist. Demo trading needs a *separate* demo key — a live key with
  `x-simulated-trading: 1` fails.
- Account mode set to multi-currency margin (`acctLv = 3`) for the margin model to apply.
- A clock synchronised to within **30 seconds** of OKX server time (`GET /api/v5/public/time`),
  or every signed request is rejected with error 50102.
- Live discount tiers from `GET /api/v5/public/discount-rate-interest-free-quota`.
  Tiers are revised periodically; a hard-coded schedule silently over-values collateral.
- `ctVal`, `lotSz`, `minSz`, and `tickSz` from `GET /api/v5/public/instruments` for
  every instrument traded — `sz` is meaningless without `ctVal`.
- Durable storage for `clOrdId` values, written **before** the order is submitted.

## Workflow

1. **Build the timestamp, then sign**: format the timestamp as ISO 8601 UTC with
   exactly three fractional digits (`2020-12-08T09:08:57.715Z`). Epoch seconds or
   milliseconds are the single most common cause of error 50102, so `parse_timestamp`
   rejects them locally rather than letting OKX do it. Sign
   `timestamp + METHOD + requestPath + body` with the secret key as **raw UTF-8** —
   do not Base64-decode it first, as you would for Coinbase.
2. **Sign the exact bytes you will send**: `requestPath` must carry the query string
   for GETs, and `body` must be the same serialised string the HTTP client transmits.
   Re-serialising the dict for the wire (different key order, different separators)
   produces a signature over a body that was never sent, and authentication fails
   with no hint that the body was the problem.
3. **State the environment explicitly**: construct the engine with
   `simulated_trading=True` only alongside demo credentials. The header is emitted on
   every request as `0` or `1`, so a promotion from demo to live is a visible,
   greppable change rather than an omitted header.
4. **Value collateral through the tier schedule, not a flat factor**: discount rates
   are brackets on the **currency amount** and apply marginally. 100 BTC does not get
   the top-tier rate on all 100 BTC; it consumes each bracket in turn. If a holding
   exceeds the schedule, the engine raises rather than valuing the remainder — a
   stale schedule must fail loudly, not quietly inflate equity.
5. **Never discount a liability**: a negative currency equity is a borrowing. A
   haircut applied to it would *shrink* the liability and inflate the margin ratio,
   which is the exact direction that hides a liquidation. Negative equity is counted
   at full USD magnitude.
6. **Compute the ratio against the right denominator**: OKX's maintenance margin
   ratio is `Adjusted equity / (Maintenance margin + Liquidation fees)`, and adjusted
   equity is discounted equity *minus* frozen assets and estimated open-order fees.
   Pass `equity_deductions_usd` and `liquidation_fee_usd`; leaving them at zero yields
   an upper bound, and the report says so in `warnings`.
7. **Classify against OKX's own thresholds**: `> 300%` is `SAFE`, `100% < r <= 300%`
   is `MARGIN_WARNING` (OKX warns to reduce positions), and `<= 100%` is
   `LIQUIDATION_RISK_CALL` (OKX cancels open orders, then force-liquidates). A ratio
   landing exactly on a threshold belongs to the riskier bucket.
8. **Mint and persist a `clOrdId` before submitting**: `build_order_payload` requires
   one. Write it to durable storage *first*, then submit. On a timeout, do not resend
   with a fresh id — reuse the same one, or query
   `GET /api/v5/trade/order?clOrdId=...` to discover whether the original was accepted.
9. **Size in the instrument's own units**: for FUTURES/SWAP/OPTION, `sz` is the
   **number of contracts**. `1` on `BTC-USDT-SWAP` is one contract of `ctVal` 0.01 BTC,
   not 1 BTC. Round to `lotSz` and price to `tickSz` before building the payload.
10. **Attach `posSide` in hedge mode**: long/short position mode requires
    `posSide` on FUTURES/SWAP orders. The builder validates the value but cannot see
    the account's position mode, so it cannot tell you that one was needed.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Epoch timestamps in `OK-ACCESS-TIMESTAMP`**: OKX wants ISO 8601 UTC with
  milliseconds. `1607418537715` is rejected with 50102 — the same error a genuinely
  skewed clock produces, so the format bug is routinely misdiagnosed as a clock bug.
  Check the format first, then compare against `GET /api/v5/public/time`.
- **Base64-decoding the secret key before signing**: correct for Coinbase, wrong for
  OKX. The OKX secret is used as raw UTF-8 bytes. This produces a well-formed 44-character
  signature that is simply never valid.
- **Signing a body you did not send**: the signature covers the exact request-body
  string. Serialise once, sign that string, and post that same string.
- **Dropping the query string from `requestPath`**: OKX counts GET parameters as part
  of the requestPath. Signing `/api/v5/account/balance` and sending
  `/api/v5/account/balance?ccy=BTC` fails authentication.
- **Applying the discount rate to negative equity**: multiplying a $10,000 liability
  by a 0.9 haircut reports it as $9,000, overstating adjusted equity and the margin
  ratio. The error is invisible in a healthy account and appears precisely when the
  account is leveraged.
- **Treating the discount rate as one flat number per currency**: OKX's schedule is
  tiered by currency amount and applied marginally. Using the *first* tier's rate over
  a large holding over-values it; using the *last* tier's rate under-values it. Both
  are wrong, and the first is the dangerous direction.
- **Hard-coding a discount schedule**: OKX revises tiers by announcement. A pinned
  table keeps returning confident numbers after the revision lands.
- **Reporting discounted equity as adjusted equity**: OKX subtracts assets frozen in
  isolated-margin and options-closing orders and estimated open-order fees, and adds
  liquidation fees to the denominator. Omitting both makes every reported ratio
  optimistic — safe to *display*, unsafe to *gate on*.
- **Comparing the API's `mgnRatio` against a 300 threshold**: `mgnRatio` from
  `GET /api/v5/account/balance` is a ratio, not a percentage. `2.5` is 250%, deep in
  warning territory, and reads as safely below 300 if the units are confused.
- **Retrying a timed-out order without a `clOrdId`**: an HTTP timeout says nothing
  about whether OKX accepted the order. Without a stable client order ID, the retry is
  a second order, not a retry.
- **Using a hyphenated UUID as `clOrdId`**: OKX accepts case-sensitive alphanumerics
  up to 32 characters. `uuid4()` in its standard form is 36 characters with hyphens and
  is rejected; `uuid4().hex` is exactly 32 valid characters.
- **Reading `sz` as a base-currency quantity on derivatives**: `sz` is contracts for
  FUTURES/SWAP/OPTION. On `BTC-USDT-SWAP` (`ctVal` 0.01 BTC), `sz=1` is 0.01 BTC —
  and a caller who meant 1 BTC and passed `1` is 100× under-sized, while one who
  "corrected" it to 100 without checking `ctVal` on a different instrument is 10× over.
- **Emitting sizes in scientific notation**: `str(1e-8)` is `'1e-08'`, which OKX will
  not parse. Format quantities as plain fixed-point decimal strings.
- **Sending `px` on a market order, or omitting it on a limit order**: OKX applies `px`
  only to `limit`, `post_only`, `fok`, and `ioc`.
- **Omitting `posSide` in hedge mode**: long/short position mode requires it on
  FUTURES/SWAP, and each side must be configured separately for isolated margin.

## Verification

- Sign OKX's documented example request (`GET /api/v5/account/balance?ccy=BTC` at
  `2020-12-08T09:08:57.715Z`) and confirm the result matches an HMAC-SHA256
  Base64 value derived independently from RFC 2104 primitives, and that permuting the
  prehash fields changes it.
- Confirm `1607418537715`, `2020-12-08T09:08:57Z`, and `2020-12-08 09:08:57.715Z` all
  raise before a request is built, and that a relative `requestPath` is refused.
- Confirm `get_auth_headers` emits `x-simulated-trading: 0` by default and `1` only
  for an engine constructed with `simulated_trading=True`.
- Reproduce OKX's published worked example: 100 BTC at $60,000 under the tiers
  0–20 @ 0.98, 20–25 @ 0.975, 25–30 @ 0.97, 30–50 @ 0.965, 50–70 @ 0.96, 70–90 @ 0.955,
  90–110 @ 0.95 gives **$5,785,500** discounted equity against $6,000,000 gross.
  Confirm a 200 BTC holding against the same schedule raises rather than being valued.
- Confirm a −10,000 USDT balance with `discount_factor=0.9` contributes exactly
  −$10,000, not −$9,000.
- Confirm the status ladder at the exact boundaries: 300% is `MARGIN_WARNING` and
  100% is `LIQUIDATION_RISK_CALL`, not `SAFE` and `MARGIN_WARNING`.
- Confirm a negative `maintenance_margin_usd` raises instead of reporting `SAFE`, and
  that NaN or infinite equity inputs raise rather than being scored.
- Confirm `build_order_payload` cannot be called without `cl_ord_id`, rejects a
  hyphenated UUID, rejects a limit order with no price and a market order with one,
  and renders `1e-8` as `"0.00000001"`.
- Run `python -m unittest discover -s skills/okx-unified-account-api/scripts` and
  confirm a 100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `sandbox-vs-production-endpoint-drift`
- `minimum-fill-size-and-lot-rounding-logic`
- `multi-broker-rate-limit-handling`
- `clock-drift-monitoring-alerting-thresholds`
- `perpetual-futures-funding-rate-handling`
- `kraken-websocket-v2-auth-and-subscriptions`
- `binance-futures-testnet-to-mainnet-promotion`
