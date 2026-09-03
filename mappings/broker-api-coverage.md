# Broker & Exchange API Coverage Index

This is the comprehensive cross-cutting matrix of broker APIs, exchange protocols, market data feeds, and trading infrastructure software referenced across the 504 skills in this repository.

> **Disclaimer**: Broker APIs and exchange specifications change over time. This index serves as an engineering discovery guide. Always verify parameter requirements, rate limits, and endpoint definitions against your broker/exchange's live documentation before deploying production code.

---

## 1. US Equities, Options & Derivatives Brokers
| Broker / Platform | Relevant Skills | Operational Focus |
|---|---|---|
| **Alpaca Trading API** | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `alpaca-trading-api-integration` | Paper/live OAuth & API keys, sub-second order placement, polygon/alpaca tick streaming |
| **Interactive Brokers (IBKR TWS/Gateway API)** | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `interactive-brokers-ib-kr-api`, `multi-account-same-strategy-fan-out` | ib_insync / ibapi async order routing, margin monitoring, contract resolution, FA allocation groups (`faGroup`/`faMethod`: NetLiq, AvailableEquity, EqualQuantity, PctChange) |
| **Tastytrade API** | `tastytrade-api-integration`, `options-greeks-real-time-portfolio-aggregation` | Options chain streaming, multi-leg order execution, DXFeed websocket integration |
| **TradeStation API** | `tradestation-websocket-order-updates`, `execution-algo-twap-vwap-slicing` | Streaming order execution, execution quality scorecards, streaming tick feeds |
| **Charles Schwab API** | `broker-api-versioning-migration-playbook`, `order-placement-idempotency` | OAuth2 refresh token rotation, equity & options REST endpoints |

---

## 2. Indian Equity & Derivatives Brokers (NSE / BSE)
| Broker / Platform | Relevant Skills | Operational Focus |
|---|---|---|
| **Fyers API v3** | `headless-broker-auth-patterns`, `token-lifecycle-live-probing`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` | Checksum generation (`app_id + secret_key`), WebSocket binary tick parsing, TOTP safety window |
| **Zerodha Kite Connect** | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `websocket-reconnect-without-duplicate-subscriptions`, `zerodha-kite-postback-webhook-verification` | SHA-256 `api_key + request_token + api_secret` checksum, postback checksum = plain SHA-256 of `order_id + order_timestamp + api_secret` (not an HMAC; authenticates those two fields only) |
| **ICICI Breeze API** | `headless-broker-auth-patterns`, `token-lifecycle-live-probing`, `order-placement-idempotency`, `multi-broker-rate-limit-handling` | Session token validation, checksum hashing, customer session lifecycle |
| **Upstox API v2** | `headless-broker-auth-patterns`, `order-placement-idempotency`, `multi-broker-rate-limit-handling`, `upstox-oauth-refresh-token-rotation` | OAuth 2.0 token rotation, protobuf WebSocket feeds, GTT order handling |

---

## 3. Global Equity, Futures & Options Exchanges
| Venue / Exchange | Relevant Skills | Protocol / Interface |
|---|---|---|
| **CME Globex** | `cme-globex-futures-api-integration`, `futures-contract-roll-automation` | iLink 3 binary protocol, MDP 3.0 market data, SPAN margin risk |
| **Eurex Exchange** | `eurex-market-data-and-order-api`, `options-margin-span-calculation-global` | ETI (Enhanced Trading Interface), MDI market data, Prisma margin |
| **Hong Kong Exchange (HKEX)** | `hong-kong-exchange-hkex-orion-api`, `shanghai-shenzhen-connect-programs` | OCG-C order entry (Binary / FIX), OMD-C market data, Second Schedule spread tables, Stock Connect Northbound |
| **Singapore Exchange (SGX)** | `singapore-exchange-sgx-api-integration`, `multi-currency-pnl-and-fx-conversion` | Titan-DT derivatives engine (OUCH / FIX order entry, ITCH/GLIMPSE data), Reach-ST securities engine (Iris-ST from H2 2027), per-contract and per-trade-type minimum price fluctuations, price-tiered SGX-ST minimum bid size |
| **Australian Securities Exchange (ASX)** | `australian-securities-exchange-asx-api`, `asic-market-integrity-rules-automated-trading` | ASX Trade matching engine, ITCH/OUCH, Market Integrity Rules (MIR) |
| **Japan Exchange Group (JPX / TSE)** | `japan-exchange-group-jpx-api-integration`, `japan-fsa-high-speed-trading-registration` | arrowhead4.0 matching engine, SICC alphanumeric securities codes, absolute-yen daily price limits, High-Speed Trading (HST) registration |
| **Korea Exchange (KRX / KOSPI, KOSDAQ)** | `korea-exchange-krx-api-integration`, `exchange-tick-size-regime-tracking` | EXTURE 3.0 matching engine, six-character short codes (단축코드) with alphanumeric sixth character, 2023 tick size schedule, truncated-amount daily price limit band |
| **Tel Aviv Stock Exchange (TASE)** | `tase-israel-exchange-api`, `vat-gst-treatment-of-trading-related-services` | TASE binary FIX/ITCH protocol, NIS currency settlement, regulatory reporting |
| **Borsa Istanbul (BIST)** | `borsa-istanbul-api-integration`, `dma-direct-market-access-gateways` | BISTECH ITCH/OUCH protocol, TRY settlement, circuit breaker filters |
| **Bursa Malaysia** | `bursa-malaysia-api-integration`, `shariah-compliant-screening-for-equities` | BTS trading engine, Shariah compliance screening, Islamic derivatives |
| **Taiwan Stock Exchange (TWSE)** | `taiwan-stock-exchange-twse-api`, `unicode-and-encoding-issues-in-global-instrument-names` | TMP binary protocol, odd-lot trading, Big5/UTF-8 character encoding |
| **London Stock Exchange (LSE)** | `order-to-trade-ratio-fee-penalty-avoidance`, `mifid-ii-algo-trading-compliance-eu` | MillenniumIT matching engine, MITCH market data, OTR fee tiers |

---

## 4. Crypto Exchanges & Digital Asset Custody
| Platform / Provider | Relevant Skills | Operational Focus |
|---|---|---|
| **Binance (Spot & Futures)** | `crypto-exchange-api-integration`, `binance-futures-testnet-to-mainnet-promotion`, `perpetual-futures-funding-rate-arbitrage` | SBE WebSocket streams, HMAC-SHA256 REST signatures, testnet promotion |
| **Coinbase Advanced Trade** | `crypto-exchange-api-integration`, `crypto-tax-lot-tracking-fifo-lifo-hifo` | JWT auth, WebSocket user order feeds, tax lot identification |
| **Kraken REST & WebSocket v2** | `kraken-websocket-v2-auth-and-subscriptions`, `crypto-exchange-api-integration`, `crypto-staking-yield-and-slashing-risk` | HMAC-SHA512 auth signatures, WebSocket v2 private/public feeds |
| **Deribit Options & Perpetual Futures** | `deribit-options-and-perpetuals-api`, `options-greeks-real-time-portfolio-aggregation` | JSON-RPC WebSocket API, BTC/ETH volatility surface & Greeks |
| **Fireblocks / BitGo / HSM Custody** | `hardware-security-module-hsm-for-signing-keys`, `multi-signature-approval-for-large-transfers`, `withdrawal-velocity-limits-and-anomaly-detection` | PKCS#11 HSM key signing, MPC threshold signatures, withdrawal velocity limits |

---

## 5. Foreign Exchange (FX) & Protocol Bridges
| Protocol / System | Relevant Skills | Protocol / Infrastructure Focus |
|---|---|---|
| **FIX Protocol (v4.2 / v4.4 / v5.0)** | `fix-protocol-engine-implementation`, `fix-protocol-session-management-and-logon-handshake` | Session logon/heartbeat, tag 35 message routing, Sequence Reset |
| **OANDA v20 REST API** | `forex-broker-integration-oanda-mt5`, `multi-currency-pnl-and-fx-conversion` | Bearer token auth, v20 streaming pricing & position management |
| **MetaTrader 5 (MT5 Python Bridge)** | `mt5-python-bridge-for-forex-bots`, `forex-broker-integration-oanda-mt5` | `MetaTrader5` Python module IPC, `MqlTradeRequest` construction, `TRADE_RETCODE` triage, symbol-metadata-driven volume/stop/filling validation |
| **Saxo Bank OpenAPI** | `saxo-bank-openapi-integration`, `multi-asset-backtest-currency-normalization` | OAuth2 PKCE auth, SignalR WebSocket streaming, multi-asset order routing |
