---
name: bybit-derivatives-api-integration
description: >-
  Use when signing Bybit V5 REST requests by hand rather than through the pybit SDK:
  HMAC-SHA256 over the exact bytes transmitted, a timestamp inside Bybit's asymmetric
  acceptance window, and the recv_window semantics.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: bybit, crypto-derivatives, v5-api, hmac-sha256, rate-limiting, request-signing, order-idempotency
  brokers_frameworks: Bybit V5 REST
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system talks to the Bybit V5 REST API without an SDK —
because it needs control over threading, connection reuse, timeout handling and backoff
that a synchronous SDK does not expose. It covers the four things a hand-rolled Bybit
client gets wrong:

1. Signing a string that is not the string actually transmitted (`retCode 10004`).
2. A timestamp outside the acceptance window (`retCode 10002`).
3. Pacing against one rate limit while breaching the other (`retCode 10006` vs. HTTP 403).
4. Retrying an order submission after a timeout and opening a second position.

## When NOT to Use

- **When `pybit` is adequate.** Bybit's official Python SDK signs correctly, retries and
  handles WebSocket sessions. Hand-rolling is justified by a specific control requirement,
  not by default. This skill's signing logic deliberately produces the same canonical
  form as `pybit` so the two can be cross-checked.
- **For WebSocket authentication.** The WS handshake signs `GET/realtime` plus an expiry,
  not `timestamp + api_key + recv_window + payload`. Different scheme; do not reuse this
  one. See `websocket-reconnection-with-state-recovery`.
- **For RSA-signed keys.** Self-generated (RSA) API keys sign with RSA-SHA256 and encode
  the signature as base64, not lowercase hex. This module implements the HMAC path only.
- **As the whole rate-limit story.** `RateLimitSnapshot` reads the per-UID, per-endpoint
  headers. The per-IP limit is invisible to them and needs a process-wide budget shared by
  every strategy on the same egress IP — see `multi-broker-rate-limit-handling`.
- **From a restricted jurisdiction.** Bybit returns HTTP 403 to requests from IP addresses
  in the US or Mainland China regardless of how correctly they are signed.

## Prerequisites

- A Bybit API key with derivatives trading permission, IP-whitelisted. A key bound to a
  different IP fails with `retCode 10010`.
- The key must match the environment it is used against. Mainnet, testnet, mainnet-demo
  and testnet-demo are four separate key domains, and a mismatch fails with
  `retCode 10003` — an error easy to misread as a bad secret.
- A clock kept NTP-synchronised. Bybit's documented recommendation is local device time
  under NTP; `sync_with_server_time()` is a fallback for hosts that cannot run a daemon.
- Python 3.10+. Standard library only — `hmac`, `hashlib`, `json`, `uuid`. No SDK, and no
  HTTP client is bundled: the module signs, the caller sends.

## Workflow

1. **Build the config, and keep the secret out of your logs.**
   `BybitConfig` validates the credentials and `recv_window` up front and masks
   `api_secret` in its `repr`. A plain dataclass prints its secret into every traceback
   and debug log line that touches it.

2. **Render the payload, then sign that exact rendering.**
   `sign_request()` returns `url`, `headers`, `body` and the `payload` that was signed.
   For GET the query string is already appended to `url`; for POST the signed JSON string
   is `body`. **Send those verbatim.** Handing the original dict to a client library that
   re-serialises the JSON or re-encodes the query changes the transmitted bytes and yields
   `retCode 10004`, with a signature that looks perfectly well-formed.

   Alphabetical key ordering is *not* a Bybit requirement — the V5 documentation states no
   ordering rule. This module sorts keys to match `pybit`'s canonical form. What matters is
   only that the signed string equals the sent string.

3. **Reject parameters that cannot survive the wire.**
   `None` is dropped rather than stringified to `"None"`; booleans render lowercase; a
   value containing a space, `&`, `+`, `#`, a malformed `%` escape or non-ASCII raises,
   because an HTTP client rewrites those *after* signing. `=` and well-formed `%XX`
   escapes are allowed through unchanged — Bybit's `nextPageCursor` needs them, and no
   client rewrites them. Bybit's own SDK does not percent-encode, so failing loudly beats
   emitting a signature that cannot match.

4. **Pass quantities and prices as decimal strings.**
   `qty`, `price`, `triggerPrice`, `takeProfit` and `stopLoss` are rejected unless they are
   strings. Letting JSON serialise a float means `0.1 + 0.2` is transmitted as
   `0.30000000000000004`.

5. **Keep the timestamp inside the window — and know which direction is forgiving.**
   Bybit accepts `server_time - recv_window <= ts < server_time + 1000`. A clock that is
   *behind* is covered by `recv_window`; a clock that is *ahead* gets exactly 1000 ms no
   matter how large `recv_window` is. Raising `recv_window` to fix a fast clock does
   nothing except widen your replay window. `is_within_acceptance_window()` asserts the
   rule against a real `GET /v5/market/time` response as a pre-flight check.

6. **Pace against both limits, separately.**
   Read `RateLimitSnapshot.from_headers(response.headers)` after each call and back off
   when `should_throttle()` is true. The test is *relative* to `X-Bapi-Limit` on purpose:
   several order endpoints have a limit of exactly 10/s, so an absolute "back off below 10
   remaining" rule fires on every healthy request. Separately, hold a process-wide budget
   under the per-IP ceiling of 600 requests per 5 seconds — the headers say nothing about it.

7. **Attach an `orderLinkId` to every order, before you need it.**
   Mint it with `new_order_link_id("strat-")` and store it with the order intent *before*
   dispatch. When a submission times out, retry with the **same** id: Bybit rejects the
   duplicate with `retCode 110072` instead of opening a second position. An id generated
   at retry time provides no protection at all.

8. **Classify the failure before retrying.**
   `10002` (timestamp), `10004` (signature) and `10010` (IP) are deterministic — retrying
   them just burns rate limit. `10006` and HTTP 403 need backoff, and the 403 IP ban needs
   all HTTP sessions terminated for at least 10 minutes. Only a network-level timeout on a
   POST is genuinely ambiguous, and that is the case `orderLinkId` exists for.

> Full procedure: see `references/workflows.md`.
> Endpoints, limits, headers and error codes: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Signing one string and sending another.** The single most common V5 failure. It
  happens when the query is rebuilt from a dict by `requests`, when the JSON is
  re-serialised with default separators, or when a value gets percent-encoded after
  signing. `retCode 10004` says "error sign", which sends people hunting for a bad secret.
- **Assuming query parameters must be sorted.** They need not be. Sorting for *signing*
  while sending the dict's insertion order is precisely how a correct-looking
  implementation produces `10004` on every request.
- **Widening `recv_window` to cure clock drift.** It only helps a slow clock. A clock 3
  seconds fast is rejected under a 20-second `recv_window` exactly as it is under 5
  seconds, because the forward tolerance is a fixed 1000 ms. Fix NTP.
- **Retrying an order because the HTTP request timed out.** Bybit may have accepted it
  before the response was lost. Without a reused `orderLinkId`, the retry is a second
  order — in a fast market, at a materially different price.
- **Backing off on the wrong limit.** `X-Bapi-Limit-Status` tracks the per-UID,
  per-endpoint budget only. A fleet of strategies sharing one IP can each stay well inside
  their endpoint budgets and still trip the 600-per-5-second IP ceiling, whose penalty is
  a ~10-minute ban on *every* session from that address, mid-position.
- **Reading `X-Bapi-Limit-Reset-Timestamp` as a reset time.** Bybit documents that it is
  only a reset instant once the limit has been exceeded; otherwise it is just the current
  server timestamp. Sleeping until it unconditionally is a no-op that reads like a control.
- **Attaching a JSON body to a GET.** Bybit returns HTTP 403 for a GET carrying an empty
  JSON body. Send the query string in the URL and no body.
- **Logging the config object.** `BybitConfig` masks the secret; a dict, a `dataclasses.asdict()`
  call or a custom subclass will not. Never put the raw secret in a log record or an
  exception message.
- **Pointing testnet keys at mainnet.** `retCode 10003` is a key/domain mismatch, not an
  invalid key. Verify the base URL before assuming the credentials are wrong.

## Verification

- Confirm the signature equals an independently derived HMAC over
  `timestamp + api_key + recv_window + payload` — not merely that it is 64 hex characters,
  which is true of any SHA-256 digest of anything.
- Confirm the GET `url` ends with exactly the signed `query_string`, and the POST `body`
  is byte-identical to the signed `payload`.
- Confirm `{"cursor": None}` yields no `cursor` key rather than `cursor=None`, that a value
  containing a space is rejected, and that a real `nextPageCursor` with `=` and `%XX`
  escapes passes through byte-for-byte.
- Confirm `qty=0.1` raises and `qty="0.1"` is accepted.
- Confirm the acceptance window is inclusive at `server_time - recv_window`, exclusive at
  `server_time + 1000`, and that the forward bound does not move when `recv_window` grows.
- Confirm `RateLimitSnapshot(limit=10, remaining=9).should_throttle()` is `False` — the
  regression that an absolute "below 10" threshold introduces.
- Confirm `repr()` of a `BybitConfig`, including inside a container, does not contain the
  secret.
- Run `python -m unittest discover -s skills/bybit-derivatives-api-integration/scripts` and confirm a 100% pass rate.
- Against testnet only: sign a `GET /v5/order/realtime`, send it unmodified, and confirm
  `retCode 0`. A signing bug that unit tests cannot see is one where the caller's transport
  mutates the request.

## Related Skills

- `crypto-exchange-api-integration`
- `multi-broker-rate-limit-handling`
- `order-placement-idempotency`
- `broker-side-order-throttle-detection`
- `binance-futures-testnet-to-mainnet-promotion`
- `perpetual-futures-funding-rate-handling`
