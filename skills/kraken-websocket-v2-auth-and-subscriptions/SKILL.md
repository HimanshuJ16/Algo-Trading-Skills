---
name: kraken-websocket-v2-auth-and-subscriptions
description: >-
  Crypto exchange API client for Kraken WebSocket v2 API, computing HMAC-SHA512 signatures for REST token retrieval, building public/private v2 subscription frames, and managing 15-minute token lifecycles.
domain: Crypto Custody Security
subdomain: Kraken Exchange Connectivity & WS v2 API
tags: ["kraken", "websocket-v2", "hmac-sha512", "ws-token", "executions-channel", "crypto-api", "order-book-v2"]
brokers_frameworks: ["Kraken WS v2 API Specification", "Kraken REST Private API", "Python Dataclasses"]
version: "1.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when integrating algorithmic trading bots with Kraken cryptocurrency exchange using their **WebSocket v2 API** (`wss://ws.kraken.com/v2` and `wss://ws-auth.kraken.com/v2`). Subscribing to private channels (`executions` for trades/fills, `balances`) requires generating a short-lived **WebSocket Token** via REST (`/0/private/GetWebSocketsToken`) signed with HMAC-SHA512. Tokens expire in 15 minutes ($900\text{ seconds}$) and require auto-refresh management.

## Prerequisites

- Kraken API Key & API Secret (Base64 encoded).
- Channel subscription spec (`channel`: `book`/`ticker`/`executions`, `symbols`: `["BTC/USD"]`).

## Workflow

1. **REST HMAC-SHA512 Signature & WS Token Generation**:
   - Post request to `/0/private/GetWebSocketsToken` with nonce.
   - Compute signature: $\text{Base64}(\text{HMAC-SHA512}(\text{Path} + \text{SHA256}(\text{nonce} + \text{postData}), \text{DecodedSecret}))$.
   - Extract 15-minute `token` string.
2. **WebSocket v2 Subscription Frame Construction**:
   - Public Subscription: `{"method": "subscribe", "params": {"channel": "book", "symbol": ["BTC/USD"], "depth": 10}}`.
   - Private Subscription: `{"method": "subscribe", "params": {"channel": "executions", "token": "<ws_token>", "snap_orders": true}}`.
3. **Token Lifecycle & Refresh Management**:
   - Monitor token age; initiate auto-refresh at 12 minutes ($720\text{ seconds}$).
4. **Audit Report Generation**: Output structured `KrakenWsV2Report`.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using Deprecated v1 Endpoints**: Connecting to `wss://ws.kraken.com` with v1 payload schemas instead of `wss://ws-auth.kraken.com/v2`.
- **Failing to Auto-Refresh Token**: Allowing the 15-minute WebSocket token to expire without refreshing, causing disconnects during re-subscriptions.
- **Incorrect HMAC Base64 Secret Decoding**: Passing raw API secret string into HMAC instead of `base64.b64decode(secret)`, causing HTTP 401 `EAPI:Invalid signature` rejections.

## Verification

- Instantiate `KrakenWsV2ManagerEngine`. Generate REST HMAC-SHA512 signature for `/0/private/GetWebSocketsToken` $\implies$ verify signature structure. Construct Public `book` subscription request for `BTC/USD` and Private `executions` subscription with active WS token $\implies$ verify valid v2 JSON frames. Audit 14-minute old token $\implies$ verify engine flags `TOKEN_REFRESH_REQUIRED`.
- Run `python scripts/test_kraken_websocket_v2_auth_and_subscriptions.py`.

## Related Skills

- `binance-futures-testnet-to-mainnet-promotion`
- `key-rotation-schedule-for-hot-wallet-keys`
---
