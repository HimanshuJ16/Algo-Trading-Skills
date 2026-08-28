# Standards for OKX Unified Account API

All values below were verified against OKX / OKCoin v5 documentation, the official
`okxapi/python-okx` SDK, and the OKX Help Center in August 2026. OKX revises discount
tiers, order types, and rate limits by announcement — re-verify before relying on a
pinned value.

## Authentication

| Item | Requirement | Source |
|---|---|---|
| Signature algorithm | `Base64(HMAC-SHA256(secret_key, prehash))` | OKX/OKCoin v5 docs, "Making Requests" |
| Prehash string | `timestamp + METHOD + requestPath + body` | OKX/OKCoin v5 docs |
| Secret key encoding | Raw UTF-8 bytes; **not** Base64-decoded first | `okxapi/python-okx`, `okx/utils.py` |
| `requestPath` | Includes the query string — "GET request parameters are counted as requestpath, not body" | OKCoin v5 docs |
| `body` | The exact serialised request-body string; empty string when absent | OKX/OKCoin v5 docs |
| Required headers | `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP`, `OK-ACCESS-PASSPHRASE`, `Content-Type: application/json` | OKX/OKCoin v5 docs |
| Timestamp format | ISO 8601 UTC with milliseconds, e.g. `2020-12-08T09:08:57.715Z` | OKX/OKCoin v5 docs |
| Clock tolerance | Rejected beyond ~30 s from server time with error **50102** ("Timestamp request expired"); sync via `GET /api/v5/public/time` | OKX API FAQ; widely reproduced in SDK issue trackers |
| Demo trading | `x-simulated-trading: 1` header **plus** a demo-specific API key; `0` for live | `okxapi/python-okx`, `okx/utils.py`; OKX v5 docs |

## Multi-currency margin mode (`acctLv = 3`)

| Quantity | Definition | Source |
|---|---|---|
| Discounted equity | `SUM over currencies [ Currency equity × Discount rate × USD price ]` | OKX Help Center, "IV. Multi-currency margin mode: cross margin trading" |
| Adjusted equity | Discounted equity + spot/spot-with-margin order loss − assets frozen in options buy orders for closing positions − assets frozen in isolated margin orders − estimated trading fees from all open orders | ibid. |
| Maintenance margin ratio | `Adjusted equity / (Maintenance margin + Liquidation fees)` | ibid. |
| Risk alert | Warning to reduce positions at ratio **≤ 300 %** | ibid. |
| Pre-liquidation | At ratio **≤ 100 %** open orders are cancelled; if still ≤ 100 %, forced liquidation follows | ibid. |
| Discount rate scope | The value attributed to an asset **when used as collateral**; published worked examples apply it to positive holdings | ibid.; OKX Help Center, discount-rate announcements |
| Discount tier basis | Brackets on **currency amount**, applied **marginally** per bracket | ibid. |
| Live tier source | `GET /api/v5/public/discount-rate-interest-free-quota` (`discountInfo`, `discountRate`, `minAmt`, `maxAmt`, `discountLv`) | OKX v5 docs |
| API fields | `adjEq` and `mgnRatio` on `GET /api/v5/account/balance`; `mgnRatio` is a **ratio**, not a percentage | OKX v5 docs |

**Not verifiable from published OKX material:** how a *negative* currency equity is
valued inside `adjEq`. OKX documents the discount rate as a collateral valuation and
its examples cover positive holdings only. This skill values negative equity at full
USD magnitude — the conservative direction — and flags the modelling gap rather than
asserting OKX's internal formula.

### OKX's published worked example (used as a test vector)

100 BTC at $60,000, tiers by BTC amount:

| Bracket (BTC) | Rate | Contribution |
|---|---|---|
| 0 – 20 | 0.98 | 20 × 0.98 × $60,000 = $1,176,000 |
| 20 – 25 | 0.975 | 5 × 0.975 × $60,000 = $292,500 |
| 25 – 30 | 0.97 | 5 × 0.97 × $60,000 = $291,000 |
| 30 – 50 | 0.965 | 20 × 0.965 × $60,000 = $1,158,000 |
| 50 – 70 | 0.96 | 20 × 0.96 × $60,000 = $1,152,000 |
| 70 – 90 | 0.955 | 20 × 0.955 × $60,000 = $1,146,000 |
| 90 – 110 | 0.95 | 10 × 0.95 × $60,000 = $570,000 |
| **Total** | | **$5,785,500** vs $6,000,000 gross |

These specific tiers are illustrative of the mechanism, not a current schedule.

## `POST /api/v5/trade/order`

| Parameter | Requirement | Source |
|---|---|---|
| `tdMode` | Margin mode `cross`, `isolated`; non-margin mode `cash` | OKCoin/OKX v5 docs |
| `side` | `buy`, `sell` | OKX v5 docs |
| `ordType` | `px` applies **only** to `limit`, `post_only`, `fok`, `ioc` | OKCoin v5 docs |
| `sz` (SPOT limit) | Base currency | OKCoin v5 docs |
| `sz` (SPOT market) | Follows `tgtCcy`; default `quote_ccy` for buy, `base_ccy` for sell | OKCoin v5 docs |
| `sz` (FUTURES/SWAP/OPTION) | **Number of contracts**; read `ctVal` from `GET /api/v5/public/instruments` | OKX v5 docs; OKX `agent-trade-kit` |
| `posSide` | Required in long/short (hedge) position mode for FUTURES/SWAP; omitted or `net` in net mode | OKX v5 docs |
| `clOrdId` | Case-sensitive alphanumerics, up to 32 characters; unique among pending orders | OKCoin/OKX v5 docs |
| Rate limit | 60 requests per 2 seconds (UserID + instrument ID) | OKCoin v5 docs; OKX `agent-trade-kit` (60 per 2 s per UID) |

## Account modes

`acctLv` 1–4 remain the API-level identifiers (1 Simple/Spot, 2 Single-currency/Futures,
3 Multi-currency margin, 4 Portfolio margin) while the user-facing names have been
revised over time. The margin model in this skill applies to `acctLv = 3` only;
portfolio margin computes maintenance margin per risk unit and is out of scope.
