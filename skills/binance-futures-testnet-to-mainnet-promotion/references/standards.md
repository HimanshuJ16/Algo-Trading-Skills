# Binance Futures API Surface — Cited Reference

Every endpoint, parameter, and error code below was verified against Binance's official
developer documentation (`developers.binance.com`). Anything Binance does not state, or
states inconsistently, is flagged as such rather than asserted.

## 1. REST base endpoints

| Environment | Product | Host | Source |
|---|---|---|---|
| Production | USDⓈ-M futures | `https://fapi.binance.com` | [USDⓈ-M General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) |
| Production | COIN-M futures | `https://dapi.binance.com` | [COIN-M General Info](https://developers.binance.com/docs/derivatives/coin-margined-futures/general-info) |
| Testnet | USDⓈ-M futures | `https://demo-fapi.binance.com` (WS `wss://demo-fstream.binance.com`) | [USDⓈ-M General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info) |
| Testnet | COIN-M futures | `https://demo-dapi.binance.com` | [COIN-M General Info](https://developers.binance.com/docs/derivatives/coin-margined-futures/general-info) |

**Documented inconsistency — read before hardcoding a testnet host.** The USDⓈ-M *General
Info* page states the testnet REST base is `https://demo-fapi.binance.com`, while pages
within the same *Market Data* section still show `https://testnet.binancefuture.com`, the
long-standing legacy testnet host. Binance separately announced that Futures Mock Trading and
website testnet access would be "gradually made unavailable" during an upgrade, without
publishing endpoint migration detail
([announcement](https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4)).

Consequence for this skill: the testnet hostname is treated as *configuration*, not a
constant. `DEFAULT_TESTNET_HOSTS` accepts both the current and legacy hosts, and the
allowlist is a constructor parameter so an operator can pin the host their account actually
works against without weakening the HTTPS/exact-host check.

## 2. Connectivity and time

| Endpoint | Method | Weight | Purpose |
|---|---|---|---|
| `/fapi/v1/ping` | GET | 1 | Test connectivity; returns `{}`. Security type NONE. |
| `/fapi/v1/time` | GET | 1 | Server time in ms — use to measure clock skew before signing. |

Signed requests carry `timestamp` (ms) and optional `recvWindow` (default 5000 ms, max
60000 ms), with the API key in the `X-MBX-APIKEY` header. Local clock drift beyond the
recvWindow produces `-1021`
([General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)).

## 3. Rate limiting

Two independent budgets, reported in response headers
([General Info](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)):

- Request weight per IP — `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`
- Order count per account — `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`

`429` signals a rate-limit violation; `418` signals an automatic IP ban, whose duration
"scale[s] in duration for repeat offenders, from 2 minutes to 3 days". Read the limits from
`GET /fapi/v1/exchangeInfo` at startup rather than hardcoding numeric limits, which Binance
has changed over time.

## 4. Account-level settings that do NOT transfer from testnet

These are per-account and per-environment. They are the most common cause of a promotion
that "worked on testnet" failing on the first live order.

| Setting | Endpoint | Notes |
|---|---|---|
| Position mode (one-way / hedge) | `GET`/`POST /fapi/v1/positionSide/dual` | `"true"` = hedge, `"false"` = one-way. Rejected with `-4067` if open orders exist, `-4068` if a position exists — so set it *before* trading. Docs note that after CM migration, UM and CM share the same `dualSidePosition` setting and one call flips both. |
| Multi-assets margin mode | `POST /fapi/v1/multiAssetsMargin` | Toggles Multi-Assets vs Single-Asset mode. |
| Margin type (isolated/cross) | `POST /fapi/v1/marginType` | `-4046` when already set to the requested value. |
| Initial leverage | `POST /fapi/v1/leverage` | Params `symbol`, `leverage`, `timestamp`, optional `recvWindow` (max 60000). Response returns `leverage`, `maxNotionalValue`, `symbol` — **read it back** rather than assuming the request was honoured. |
| Permitted leverage brackets | `GET /fapi/v1/leverageBracket` | Returns per-bracket `initialLeverage` (max initial leverage for the bracket), `notionalFloor`, `notionalCap`, `maintMarginRatio`, and `cum`. Max leverage is a function of notional tier, not a single account-wide number. |

**Leverage caps for new accounts — qualified.** Binance has published successive
announcements restricting leverage on newly opened futures accounts (the 2021-07-27 update
extended the restriction window for new accounts to 60 days; later announcements state
different windows). The current numbers are not stable enough to hardcode. Determine the
account's actual ceiling from `GET /fapi/v1/leverageBracket` and from the `POST
/fapi/v1/leverage` response, not from a documented constant.

Sources: [Change Position Mode](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Position-Mode),
[Change Initial Leverage](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Change-Initial-Leverage),
[Notional and Leverage Brackets](https://developers.binance.com/docs/derivatives/usds-margined-futures/account/rest-api/Notional-and-Leverage-Brackets),
[Updates on Rules of Binance Futures Leverage for New Accounts (2021-07-27)](https://www.binance.com/en/support/announcement/updates-on-rules-of-binance-futures-leverage-for-new-accounts-2021-07-27-d6457e23eb2e42f2b9c3ce44f46f9a6d).

## 5. Account and position endpoints — version currency

`GET /fapi/v3/account`, `/fapi/v3/balance`, and `/fapi/v3/positionRisk` were introduced
2024-07-24; `v3/account` returns only symbols with positions or open orders and omits
configuration fields now served by `GET /fapi/v1/symbolConfig` and `GET
/fapi/v1/accountConfig`. The same changelog entry issued a deprecation notice for
`GET /fapi/v2/account`, `/fapi/v2/balance`, and `/fapi/v2/positionRisk`, with the removal
date "to be announced". Use v3 for new integrations
([Change Log](https://developers.binance.com/docs/derivatives/change-log)).

## 6. Symbol filters — re-read on mainnet

`GET /fapi/v1/exchangeInfo` (weight 1) returns per-symbol `status`, `pricePrecision`,
`quantityPrecision`, `contractType`, `marginAsset`, and filters:

| Filter | Constrains |
|---|---|
| `PRICE_FILTER` | `minPrice`, `maxPrice`, `tickSize` |
| `LOT_SIZE` | `minQty`, `maxQty`, `stepSize` |
| `MARKET_LOT_SIZE` | Quantity bounds specific to MARKET orders |
| `MIN_NOTIONAL` | Minimum order value (`notional`) |
| `PERCENT_PRICE` | `multiplierUp` / `multiplierDown` band around the mark |
| `MAX_NUM_ORDERS` / `MAX_NUM_ALGO_ORDERS` | Open order and algo order counts |

The docs warn that `pricePrecision` is not a substitute for `tickSize`. Symbol availability
and filter values are not guaranteed to be identical between testnet and mainnet, so filters
must be refreshed against the mainnet host before sizing the first live order
([Exchange Information](https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Exchange-Information)).

## 7. Order placement — exchange-resident stop-loss and idempotency

`POST /fapi/v1/order` supports LIMIT, MARKET, STOP, STOP_MARKET, TAKE_PROFIT,
TAKE_PROFIT_MARKET, and TRAILING_STOP_MARKET.

- **Exchange-resident hard stop**: `STOP_MARKET` (or `TAKE_PROFIT_MARKET`) with
  `closePosition=true` closes the whole position and cannot be combined with `quantity`.
  This is what survives loss of connectivity to your own host — a stop enforced only in local
  strategy code does not.
- `workingType` selects `MARK_PRICE` or `CONTRACT_PRICE` (default) as the trigger basis;
  `priceProtect` additionally constrains the mark-vs-contract divergence at trigger time.
- `reduceOnly` cannot be sent in Hedge Mode.
- `newClientOrderId` must match `^[\.A-Z\:/a-z0-9_-]{1,36}$` and is auto-generated if omitted.
  Binance describes it as "a unique id among open orders"; the documentation does **not**
  state that resubmitting the same id is a safe no-op, so it must not be relied on as an
  idempotency key on its own. Reconcile via order query before resubmitting an ambiguous order.

Source: [New Order](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order).

## 8. Error codes to handle explicitly during promotion

| Code | Name | Message |
|---|---|---|
| `-1003` | TOO_MANY_REQUESTS | "Too many requests; current limit is %s requests per minute" / "Way too many requests; IP banned until %s." |
| `-1015` | TOO_MANY_ORDERS | "Too many new orders; current limit is %s orders per %s." |
| `-1021` | INVALID_TIMESTAMP | "Timestamp for this request is outside of the recvWindow." |
| `-1022` | INVALID_SIGNATURE | "Signature for this request is not valid." |
| `-2010` | NEW_ORDER_REJECTED | Typical when mainnet symbol filters differ from testnet. |
| `-2011` | CANCEL_REJECTED | "Unknown order sent." |
| `-2019` | MARGIN_NOT_SUFFICIENT | "Margin is insufficient." |
| `-4003` | QTY_LESS_THAN_ZERO | "Quantity less than zero." |
| `-4028` | INVALID_LEVERAGE | "Leverage %s is not valid." |
| `-4046` | NO_NEED_TO_CHANGE_MARGIN_TYPE | "No need to change margin type." |
| `-4067` | POSITION_SIDE_CHANGE_EXISTS_OPEN_ORDERS | "Position side cannot be changed if there exists open orders." |
| `-4068` | POSITION_SIDE_CHANGE_EXISTS_QUANTITY | "Position side cannot be changed if there exists position." |

Source: [Error Codes](https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code).

## 9. API key handling — what is and is not verifiable

- Keys are case-sensitive and passed in the `X-MBX-APIKEY` header; testnet keys are issued
  through a separate testnet registration and are not valid against production hosts.
- Binance recommends IP allowlisting for API keys, and has at various times tied permission
  expiry to whether a key is IP-restricted. The widely cited "90-day expiry for keys without
  an IP allowlist" comes from the 2021-07-26 permission-rules announcement, which now carries
  the notice that those rules "are no longer applicable as of 2023-10-24". **Do not encode a
  specific expiry window.** Check current behaviour in the Binance API management UI for the
  account in question, and treat unexpected `-2015`/permission errors on a previously working
  key as a possible permission reset.

## 10. Internal risk standards applied by this skill

These are the skill's own conservative defaults for a pilot promotion, not Binance rules:

- Leverage ceiling default `5`, well inside any bracket Binance permits, because early live
  trading is about validating execution assumptions, not maximising notional.
- Capital-at-risk ceiling default `0.02` (2% of equity per trade), expressed as a fraction.
- An exchange-resident hard stop (`STOP_MARKET` + `closePosition=true`) is mandatory, so the
  position is protected when the strategy host is unreachable.
- Promotion is default-deny: `allow_live_promotion=False` until an operator sets it.
