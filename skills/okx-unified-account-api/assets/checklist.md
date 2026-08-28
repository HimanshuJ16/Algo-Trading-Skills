# Pre-Flight Checklist — OKX Unified Account

## Authentication
- [ ] Prehash is `timestamp + METHOD + requestPath + body`, in that order.
- [ ] Secret key is signed as raw UTF-8 — **not** Base64-decoded first.
- [ ] `requestPath` includes the query string on GET requests.
- [ ] The signed body string is byte-identical to the body actually transmitted.
- [ ] `OK-ACCESS-TIMESTAMP` is ISO 8601 UTC with exactly milliseconds (`...715Z`),
      never epoch seconds or milliseconds.
- [ ] Host clock is within 30 s of `GET /api/v5/public/time` (error 50102 otherwise).
- [ ] All four `OK-ACCESS-*` headers plus `Content-Type: application/json` are sent.

## Environment
- [ ] `x-simulated-trading` is emitted on every request (`0` live, `1` demo).
- [ ] Demo runs use demo-issued API keys, not live keys with the header flipped.
- [ ] `simulated_trading` is set explicitly at construction, not defaulted by accident.
- [ ] API key has an IP allowlist and only the permissions the bot needs.

## Margin & collateral
- [ ] Account mode is multi-currency margin (`acctLv = 3`) — the model does not cover
      portfolio margin.
- [ ] Discount tiers were fetched from
      `GET /api/v5/public/discount-rate-interest-free-quota`, not hard-coded.
- [ ] Tiers are applied **marginally** by currency amount, not as one flat rate.
- [ ] A holding exceeding the tier schedule raises rather than being valued.
- [ ] Negative (borrowed) equity is counted at full USD value with no haircut.
- [ ] `equity_deductions_usd` and `liquidation_fee_usd` are supplied, or the reported
      ratio is knowingly treated as an upper bound.
- [ ] Status thresholds match OKX: `> 300%` safe, `<= 300%` alert, `<= 100%` liquidation.
- [ ] The engine's output is reconciled against `adjEq` / `mgnRatio` from
      `GET /api/v5/account/balance`, remembering `mgnRatio` is a ratio, not a percent.

## Orders
- [ ] Every order carries a `clOrdId`: alphanumeric, ≤ 32 characters.
- [ ] The `clOrdId` is persisted **before** the request is sent.
- [ ] A timed-out submission is reconciled by `clOrdId`, never resent with a new one.
- [ ] `sz` is in contracts for FUTURES/SWAP/OPTION, with `ctVal` read from
      `GET /api/v5/public/instruments`.
- [ ] Size is rounded to `lotSz` and price to `tickSz`, above `minSz`.
- [ ] Sizes and prices are plain decimal strings — no scientific notation.
- [ ] `px` is present for `limit`/`post_only`/`fok`/`ioc` and absent otherwise.
- [ ] `posSide` is supplied when the account is in long/short (hedge) position mode.
- [ ] Submission rate stays within 60 requests per 2 s per UID and instrument.
