---
name: kraken-websocket-v2-auth-and-subscriptions
description: >-
  Use when a bot connects to Kraken Spot WebSocket v2: signs the REST call that mints
  the token, builds validated subscribe frames, routes each channel to the right
  endpoint and respects the token's 900-second use window.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: kraken, websocket-v2, hmac-sha512, ws-token, executions-channel, crypto-api, order-book-v2
  brokers_frameworks: "Kraken Spot WebSocket v2; Kraken Spot REST Private API; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a bot connects to Kraken Spot **WebSocket v2** and needs the
frame it sends to be right the first time. It covers two things that fail
silently and cost real money to debug against a live venue:

1. **REST signing for `/0/private/GetWebSocketsToken`** — the HMAC-SHA512
   `API-Sign` computation, plus the nonce discipline that keeps Kraken from
   answering `EAPI:Invalid nonce` and temporarily banning the key.
2. **Subscribe-frame construction** — validating channel, endpoint, symbol,
   depth and token freshness *before* the frame leaves your process, so a
   malformed subscription surfaces as a named local rejection instead of an
   opaque `error` message on the socket.

The engine is offline and does no network I/O, which is what makes every check
here deterministic and unit-testable.

## When NOT to Use

- **As a WebSocket client.** It builds frames; it does not connect, reconnect,
  ping, or track which subscriptions are live. Pair it with
  `websocket-subscription-reconciliation-after-reconnect` and
  `websocket-reconnection-with-state-recovery`.
- **For order entry.** `add_order` and `cancel_order` are v2 *request methods*,
  not channels. This engine deliberately refuses to wrap them in a subscribe
  frame; it will tell you so rather than emit one Kraken rejects.
- **For Kraken Futures or the NFT/Custody APIs.** Those are separate products
  with their own hosts and auth. This is Spot WS v2 only.
- **For WS v1.** The v1 schema (`{"event": "subscribe", "pair": [...]}`) is a
  different shape entirely, and none of the validation here applies to it.

## Prerequisites

- A Kraken API key with the **"Access WebSockets API"** permission enabled under
  the key's *Other* permissions. Without it `GetWebSocketsToken` returns
  `EGeneral:Permission denied` no matter how correct the signature is.
- The **Base64 private key exactly as Kraken issued it**. Do not pre-decode it;
  the engine decodes it, and rejects a secret that is not valid Base64 rather
  than signing with the wrong key.
- A single shared `KrakenNonceGenerator` per API key. Two processes signing with
  one key cannot be ordered by any client-side counter — give each its own key.
- A trusted clock. Token age is measured against `current_time_epoch`; pass it
  explicitly for reproducible audits.
- Symbols in v2 `BASE/QUOTE` form (`"BTC/USD"`), not REST altnames
  (`"XXBTZUSD"`), which subscribe without error and then deliver nothing.

## Workflow

1. **Mint the Token, Signing the Body You Actually Send**: Take a nonce from the
   shared generator, build the body, and sign that exact byte sequence with
   `generate_kraken_rest_hmac_signature(url_path, nonce, post_data)`. `url_path`
   is the path (`/0/private/GetWebSocketsToken`), never the full URL. Re-encoding
   the body between signing and sending — different key order, different float
   formatting — yields a signature Kraken cannot reproduce. The engine rejects a
   body that does not contain the nonce it is signing, because that combination
   is always a 401 waiting to happen.
2. **Treat 900 Seconds as a Use-By Window, Not a Session Timer**: Kraken's
   `expires: 900` is the window in which the token must be *used* to establish a
   private subscription. Once the connection and private subscription exist and
   are maintained, the token does not expire. Do **not** build a 15-minute
   resubscribe loop: it drops your feed for nothing and pushes you toward the
   connection rate limit. The freshness check belongs at the moment you build a
   subscribe frame, which is exactly where this engine puts it.
3. **Route by Channel, Not by a Public/Private Guess**: There are three
   endpoints. `wss://ws.kraken.com/v2` for public channels;
   `wss://ws-auth.kraken.com/v2` for `executions` and `balances`; and
   `wss://ws-l3.kraken.com/v2` for `level3` — which is order book data yet
   requires a token *and* its own host. A binary public/private split sends
   `level3` to the public endpoint with no token and it fails every time.
4. **Validate the Frame Against the Channel Registry**: `build_v2_subscription_frame`
   rejects an unknown channel, a request method dressed as a channel, a
   symbol-required channel with no symbols, and a `depth` outside the channel's
   allowed set — `book` accepts 10/25/100/500/1000, `level3` only 10/100/1000.
   Anything not in the registry is rejected rather than forwarded on the guess
   that the venue might accept it.
5. **Distinguish "Refresh Now" From "This Token Cannot Work"**: A token past the
   720-second margin but inside 900s returns `TOKEN_REFRESH_REQUIRED` — mint a
   new one and proceed. Past 900s it returns `TOKEN_EXPIRED`. A token dated in
   the future returns `TOKEN_CLOCK_SKEW` rather than being read as fresh. Route
   these differently: the first is routine, the last two mean something is wrong
   with your clock or your refresh path.
6. **Send the Frame; Log the Redacted View**: `subscription_json_frame` carries
   the live token and goes on the wire. `audit_notes` is the same frame with the
   token replaced by a stable fingerprint, and is the only version safe to log,
   store, or paste into a ticket.
7. **Correlate Acks With `req_id`**: Set `req_id` on the spec. Kraken echoes it
   on the acknowledgement, which is the only reliable way to tie an error
   response back to the subscription that caused it when several are in flight.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Building a 15-Minute Resubscribe Loop**: The most common misreading of
  Kraken's docs. The token "does not expire once a successful Websockets
  connection and private subscription has been made and is maintained" — a
  healthy connection needs no periodic token refresh, and tearing it down every
  15 minutes creates the outage it was meant to prevent. Refresh the token
  before *using* it, not on a timer against a live session.
- **Subscribing to `add_order` as a Channel**: `{"method": "subscribe",
  "params": {"channel": "add_order", "token": "..."}}` is not a valid frame.
  Order entry is a top-level method: `{"method": "add_order", "params": {...}}`.
- **Sending `level3` to the Public Endpoint**: It looks like market data, so a
  public/private split routes it to `wss://ws.kraken.com/v2` with no token. It
  is authenticated and lives on `wss://ws-l3.kraken.com/v2`. Its depth set is
  also narrower than `book`'s — 25 and 500 are valid for `book` and invalid here.
- **Falling Back to the Raw Secret When Base64 Decoding Fails**: A `try/except`
  that signs with the undecoded string produces a perfectly well-formed
  signature computed with the wrong key. The only symptom is HTTP 401
  `EAPI:Invalid signature`, with nothing pointing at the secret. Fail at signing
  time instead.
- **Logging the Frame**: `params.token` is a bearer credential for the account's
  `executions` and order-entry surface. `json.dumps(frame)` in a log line, an
  audit record, or a support ticket hands it to anyone with log access. Log the
  redacted view.
- **A Wall-Clock Nonce**: `int(time.time() * 1000)` regresses on an NTP step
  backwards and collides when two requests land in the same millisecond. Kraken
  requires strictly increasing nonces per key and temporarily bans on repeated
  `EAPI:Invalid nonce`. Latch the counter so it never reissues a value.
- **Trusting a Future-Dated Token**: A negative age passes every `age >= limit`
  comparison, so a clock-skewed token reads as permanently fresh — the freshness
  check silently stops working. So does a NaN timestamp, since every NaN
  comparison is `False`. Both are rejected here rather than scored.
- **Forgetting the "Access WebSockets API" Key Permission**: A key with trade
  and query permissions still cannot mint a WS token. The failure is
  `EGeneral:Permission denied`, which reads like a signing problem and is not.
- **Passing REST Altnames as v2 Symbols**: `"XXBTZUSD"` is the REST pair name.
  The v2 feed wants `"BTC/USD"`. The wrong form does not always error — it can
  simply deliver no data, which is far harder to notice.

## Verification

- Sign Kraken's published example (`/0/private/AddOrder`, nonce
  `1616492376594`, the documented payload) and confirm the output matches the
  documented `API-Sign` byte for byte — this is the vendor's own vector, not a
  restatement of the formula.
- Pass a secret containing a non-Base64 character and confirm
  `KrakenWsV2Error`; pass the same secret wrapped across lines with whitespace
  and confirm it still signs correctly.
- Sign a body that does not carry the nonce being signed, and pass a full URL
  instead of a path; confirm each raises rather than returning a signature.
- Build `book` for `["BTC/USD","ETH/USD"]` at depth 25 and confirm the public
  endpoint with no `token` key; build `executions` with a 300-second-old token
  and confirm `wss://ws-auth.kraken.com/v2` with `token` in `params`.
- Build `level3` with no token and confirm `MISSING_WS_TOKEN`; build it with a
  token and confirm `wss://ws-l3.kraken.com/v2`, then confirm depth 25 is
  rejected as `INVALID_DEPTH` while 100 is accepted.
- Request `add_order` and `cancel_order` as channels and confirm
  `INVALID_CHANNEL` with an empty frame in both cases.
- Request `ticker` with no symbols and with only blank strings; confirm
  `MISSING_SYMBOL` both times.
- Audit tokens aged 719.9s, 720s, 780s and 901s and confirm
  `SUBSCRIPTION_FRAME_CREATED`, `TOKEN_REFRESH_REQUIRED`,
  `TOKEN_REFRESH_REQUIRED`, `TOKEN_EXPIRED`; audit one dated an hour in the
  future and confirm `TOKEN_CLOCK_SKEW`; audit a NaN creation timestamp and
  confirm `KrakenWsV2Error`.
- Build a private frame and confirm the token appears in
  `subscription_json_frame` but nowhere in `audit_notes`.
- Draw 1,600 nonces from one generator across 8 threads and confirm no
  duplicates and strict monotonicity.
- Run `python -m unittest discover -s skills/kraken-websocket-v2-auth-and-subscriptions/scripts`
  and confirm a 100% pass rate.

## Related Skills

- `websocket-subscription-reconciliation-after-reconnect`
- `websocket-reconnection-with-state-recovery`
- `crypto-exchange-api-integration`
- `token-lifecycle-live-probing`
- `api-key-least-privilege-audit-tool`
- `multi-broker-rate-limit-handling`
- `key-rotation-schedule-for-hot-wallet-keys`
