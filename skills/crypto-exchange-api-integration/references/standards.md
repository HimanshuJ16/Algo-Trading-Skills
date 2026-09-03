# Broker & Framework Coverage — crypto-exchange-api-integration

| Exchange / Platform | Relevance to this skill |
|---|---|
| Binance (Spot & USD-M Futures) | REQUEST_WEIGHT metered per IP over a **fixed** 1-minute clock window (`X-MBX-USED-WEIGHT-1M`); STP modes `EXPIRE_MAKER` / `EXPIRE_TAKER` / `EXPIRE_BOTH` / `DECREMENT` / `NONE`, allowed per symbol; distinct Spot vs. Futures endpoints, budgets, balances and order grammars. |
| Coinbase Advanced Trade | REST & WebSocket API throttled by **requests per second** per IP, with usage in `CB-RATE-LIMIT-*` headers — not a weight pool. |
| Kraken Spot REST & WebSocket v2 | **Decaying call counter per API key** with tier-dependent maximum and decay rate; nonce management; post-only and immediate-or-cancel execution flags. |

## Verified figures

| Claim | Source | Status |
|---|---|---|
| Binance Spot REQUEST_WEIGHT = 6,000/min, raised from 1,200/min effective 2023-08-25 00:00 UTC | [Binance announcement: Spot API to Increase Request Weight Limits](https://www.binance.com/en/support/announcement/binance-spot-api-to-increase-request-weight-limits-9820396bf54644c39e666b4780622846) | Verified |
| Binance USD-M Futures REQUEST_WEIGHT = 2,400/min | [Binance USD-M Futures REST API](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api) | Verified |
| Rate-limit interval is a fixed clock window; 429 carries `Retry-After`; repeated violation escalates to an automated IP ban (HTTP 418) scaling from 2 minutes to 3 days | [Binance Spot REST API — LIMITS](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) | Verified |
| Live limits are queryable from `GET /api/v3/exchangeInfo` `rateLimits[]` | [Binance Spot REST API — LIMITS](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits) | Verified |
| Binance STP modes include `DECREMENT`; omitting the parameter yields `NONE`; allowed modes are per-symbol via `allowedSelfTradePreventionModes` | [Binance STP FAQ](https://developers.binance.com/docs/binance-spot-api-docs/faqs/stp_faq) | Verified |
| Spot `LIMIT_MAKER` mandatory params are quantity and price only; supported `timeInForce` values are GTC, IOC, FOK; there is **no** `execInst` parameter | [Binance Spot REST API — trading endpoints](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints) | Verified |
| USD-M Futures does not support `LIMIT_MAKER`; post-only is `timeInForce=GTX`, rejected outright if it would take | [Binance USD-M Futures REST API](https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api) | Verified |
| Kraken Spot REST counter is per API key; Starter 15 max / −0.33 per sec, Intermediate 20 / −0.5, Pro 20 / −1; standard call +1, ledger & trade-history +2; `AddOrder`/`CancelOrder` use a separate limiter | [Kraken Spot REST Rate Limits](https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits/) | Verified |
| Coinbase Advanced Trade is throttled per second per IP with `CB-RATE-LIMIT-*` headers | [Coinbase Advanced Trade REST API rate limits](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/rest-api) | Model verified; **exact figures not confirmed** — different Coinbase pages report different numbers, so this skill ships no Coinbase preset. Read the current limit from Coinbase's documentation and register a limiter explicitly. |

## Regulatory & Operational Notes

Crypto exchange integration intersects with jurisdiction-specific frameworks (e.g. the EU's
Markets in Crypto-Assets Regulation, US FinCEN money-transmission rules) whose applicability
depends on the operating entity's domicile and activity. This skill does **not** establish which
apply to a given operator — treat that as a compliance question, not an engineering default. What
is in scope here is operational hygiene the exchange itself enforces or exposes: API permission
scoping (never grant withdrawal rights to a trading key), IP allowlisting, and key custody
isolation, which is covered in `crypto-wallet-key-custody-security`.
