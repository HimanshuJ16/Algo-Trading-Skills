# Pre-Flight Checklist — Kraken WS v2 Auth & Subscriptions

## API key and secret

- [ ] Key has **Other → Access WebSockets API** enabled (otherwise
      `EGeneral:Permission denied`, which reads like a signing bug and is not).
- [ ] Private key is passed as issued, still Base64-encoded — not pre-decoded.
- [ ] A malformed secret raises at signing time rather than silently signing
      with the wrong key.
- [ ] No placeholder or mock credential can reach production config.

## REST signing

- [ ] `url_path` is the path (`/0/private/GetWebSocketsToken`), not a full URL.
- [ ] The body signed is byte-for-byte the body sent — nothing re-serialises it
      in between.
- [ ] The nonce appears in the body and in the signature.
- [ ] Signature reproduces Kraken's published `API-Sign` example.

## Nonce discipline

- [ ] One shared, lock-protected nonce source per API key.
- [ ] A nonce is never reissued, including after an NTP step backwards.
- [ ] No two processes sign with the same key.
- [ ] `EAPI:Invalid nonce` is not retried in a loop — repeats earn a ban.

## Endpoint routing

- [ ] Public channels → `wss://ws.kraken.com/v2`.
- [ ] `executions` / `balances` → `wss://ws-auth.kraken.com/v2`.
- [ ] `level3` → `wss://ws-l3.kraken.com/v2`, **with** a token.
- [ ] Order entry is sent as `{"method": "add_order", ...}`, never as a
      subscription to a channel named `add_order`.

## Subscription parameters

- [ ] Symbols are v2 `BASE/QUOTE` (`BTC/USD`), not REST altnames (`XXBTZUSD`).
- [ ] `book` depth ∈ {10, 25, 100, 500, 1000}; `level3` depth ∈ {10, 100, 1000}.
- [ ] `ohlc` interval ∈ {1, 5, 15, 30, 60, 240, 1440, 10080, 21600}.
- [ ] `req_id` is set so acknowledgements and errors can be correlated.

## Token lifecycle

- [ ] Token age is checked at the moment the token is *used*, not on a timer.
- [ ] No 15-minute resubscribe loop — a maintained private subscription keeps
      the token valid.
- [ ] "Needs refresh" (past 720s), "expired" (past 900s) and "clock skew"
      (future-dated) are handled as three different outcomes.
- [ ] A fresh token is minted on every reconnect before resubscribing.
- [ ] `ping` keeps the socket alive against the ~1-minute inactivity close.

## Credential hygiene

- [ ] No log line, audit record, or ticket contains `params.token` verbatim.
- [ ] The redacted audit view is what gets persisted.
- [ ] Reconnect logic does not re-log the frame on every retry.
