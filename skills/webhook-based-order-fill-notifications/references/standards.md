# Broker & Framework Coverage — webhook-based-order-fill-notifications

Every row was checked against the publisher's own documentation on
**2 September 2026**. Where a venue does not offer a fill webhook, that is
recorded as a finding rather than omitted, because "does this broker even send
webhooks?" is the first question this skill has to answer and the answer is
usually no.

## Does this venue POST fills to my endpoint?

| Venue | Outbound fill webhook? | How fills actually arrive | Source |
|---|---|---|---|
| Zerodha Kite Connect v3 | **Yes** | `POST` of a JSON body to the registered `postback_url` on order status change | [Kite Connect postbacks](https://kite.trade/docs/connect/v3/postbacks/) |
| DhanHQ v2 | **Yes** | `POST` with JSON payload on each status change (`TRANSIT`, `PENDING`, `REJECTED`, `CANCELLED`, `TRADED`, `EXPIRED`), on modification, and on each partial fill | [DhanHQ v2 Postback](https://dhanhq.co/docs/v2/postback/) |
| Interactive Brokers | **No** | `IBApi.EWrapper.execDetails` + `commissionReport` over the TWS API socket. IBKR's Web API callback-notification service covers client registration, account information changes and funding requests only, and is enabled by written request to IBKR | [TWS API — Executions and Commissions](https://interactivebrokers.github.io/tws-api/executions_commissions.html); [IBKR Web API — Callback Notifications](https://www.interactivebrokers.com/docs/web-api/account-management/callback-notifications) |
| Alpaca | **No** | Server-Sent Events. Broker API exposes `GET /v2/events/trades`, replayable from a point in time via `since_ulid`/`until_ulid` (the integer `since_id`/`until_id` form is legacy) | [Alpaca — SSE Events](https://docs.alpaca.markets/us/docs/sse-events) |
| TradeStation | **No** | Chunked HTTP streaming, `Transfer-Encoding: chunked`, `Content-Type: application/vnd.tradestation.streams.v3+json`. The stream can terminate; the client must end the request and reconnect | [TradeStation — HTTP Streaming](https://api.tradestation.com/docs/fundamentals/http-streaming/) |
| Coinbase Advanced Trade | **No** | Authenticated WebSocket `user` channel, which opens with all OPEN orders batched 50 at a time and then streams updates | [Advanced Trade WebSocket Channels](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-channels) |

One verification caveat: IBKR's callback-notifications page rejected automated
retrieval (HTTP 403) at the time of writing, so the scope stated for that
service — account-management events only, enabled by written request — comes
from indexed copies of IBKR's page rather than a direct read. Confirm it with
IBKR before relying on it. The TWS API row was read directly and is quoted
verbatim below.

Three signature headers are commonly attributed to these venues and none can be
substantiated: `X-Hub-Signature` for TradeStation, `X-Alpaca-Signature` for
Alpaca, and `CB-ACCESS-SIGN` for Coinbase Advanced. `X-Hub-Signature` is a
Meta/GitHub convention; `CB-ACCESS-SIGN` was the *outbound request* auth header
for the legacy Coinbase Exchange/Pro REST API and was retired for Advanced Trade
in favour of JWT bearer tokens (ES256 / EdDSA) when legacy API keys were removed
on 10 June 2024. Use the verified rows above instead.

## What the signature actually covers

This matters more than which algorithm is named. A signature only protects the
bytes inside it.

| Publisher | Mechanism | Covers | Does **not** cover |
|---|---|---|---|
| Zerodha Kite Connect | `checksum = sha256(order_id + order_timestamp + api_secret)`, validated server-side | `order_id`, `order_timestamp` | `status`, `filled_quantity`, `average_price`, and every other body field |
| DhanHQ | None documented — no signature header, no shared secret | nothing | the entire payload |
| Standard Webhooks | `HMAC-SHA256` over `msg_id.timestamp.payload`, sent as `webhook-signature` alongside `webhook-id` and `webhook-timestamp` | id, timestamp and body together | — |

The Kite row is the reason the workflow in `SKILL.md` reconciles against an
authenticated endpoint rather than booking the payload: a captured postback with
its `filled_quantity` edited still carries a valid checksum. The Dhan row is the
same conclusion reached faster.

## Standard Webhooks specification

Source: [`spec/standard-webhooks.md`](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md).
The closest thing to a cross-industry baseline, and worth matching even when
your publisher does not.

| Element | Specification |
|---|---|
| Headers | `webhook-id`, `webhook-timestamp` (integer Unix seconds), `webhook-signature` |
| Signed content | `msg_id.timestamp.payload`, full-stop delimited |
| Signature format | `v1,<base64>` for HMAC-SHA256; `v1a,<base64>` for ed25519. Space-delimited list of tokens |
| Secret format | `whsec_` (symmetric), `whsk_`/`whpk_` (asymmetric), base64-encoded |
| Replay tolerance | 300 seconds |
| Idempotency | "use the `webhook-id` header as an idempotency key to prevent accidentally processing the same webhook more than once" |
| Comparison | "use a constant time comparison function to compare the calculated with the expected signature" |
| Key rotation | Publisher signs with both current and old key; consumer "tr[ies] to verify each signature until one matches" |

Note the base64 encoding and the multi-token header: a verifier that assumes a
single hex digest will reject every Standard Webhooks delivery, and will break
the moment a publisher enters a rotation window.

## OWASP webhook security guidance

Source: OWASP Cheat Sheet Series,
[`cheatsheets_draft/Webhook_Security_Guidelines_Cheat_Sheet.md`](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets_draft/Webhook_Security_Guidelines_Cheat_Sheet.md).
The file sits in the repository's `cheatsheets_draft/` directory rather than the
published set, so treat it as OWASP-hosted working guidance, not a released
OWASP standard. Cited here because it is the closest authoritative statement of
the consumer-side rules, and it agrees with the Standard Webhooks spec on every
overlapping point.

- Raw body: "Read the **raw** request body *before* your framework parses it."
- Comparison: "String equality comparison (`sig == expected`) is vulnerable to
  timing attacks" — use `hmac.compare_digest` or `MessageDigest.isEqual`.
- Replay: include "a Unix timestamp in the signed material" and "**Reject**
  requests whose timestamp differs from server time by more than ±5 minutes",
  caching event IDs for at least the tolerance window.
- Idempotency: "Use the platform-provided **event ID** … as an idempotency key.
  Persist processed event IDs and skip re-processing on a duplicate. Return
  `HTTP 200` immediately for known duplicates."
- Throughput: "Decouple ingestion from processing with an async queue … to
  absorb traffic spikes without dropping events."
- Rotation: dual-secret window — publisher signs with both keys, consumer
  accepts either, old key revoked after the new one is confirmed.
- CSRF: webhook routes "must be **exempted from framework CSRF token checks**",
  and HMAC verification is the replacement, so scope the exemption to the
  webhook route and confirm verification is in place first.

## Execution identity and corrections

Interactive Brokers, [TWS API — Executions and Commissions](https://interactivebrokers.github.io/tws-api/executions_commissions.html):

> "Note if a correction to an execution is published it will be received as an
> additional IBApi.EWrapper.execDetails callback with all parameters identical
> except for the execID in the Execution object. The execID will differ only in
> the digits after the final period."

An `order_id:exec_id` composite key therefore does **not** collapse a correction
onto the original: the correction presents as a new execution and, if booked
naively, adds a phantom fill. This is why the reference implementation stores a
body digest alongside each claim and reports
`DUPLICATE_CONTENT_MISMATCH` — and why a correction must be resolved by
reconciliation against the broker's authoritative fill record, not by arithmetic
on webhook payloads.

## Delivery guarantees — what is *not* documented

Recorded because the absence is the operationally important part. Neither the
Kite Connect postback documentation nor the DhanHQ postback documentation states
a retry schedule, a redelivery policy, an ordering guarantee, or a duplicate
guarantee. Build for at-least-once, unordered, best-effort delivery, and back it
with a periodic authenticated reconciliation sweep. Do not treat the absence of
a postback as evidence that no fill occurred.

DhanHQ additionally notes that postbacks operate at the access-token level — all
orders from one access token deliver to that token's webhook URL — and that no
postback is sent if the URL is set to `localhost` or `127.0.0.1`.

## Regulatory & Operational Notes

No jurisdiction surveyed here mandates a specific webhook-authentication scheme;
the requirements above are vendor and security-guidance obligations, not
regulatory ones. What *is* regulatory is downstream: an execution record derived
from a webhook is subject to the same recordkeeping and audit-trail obligations
as any other, so retain the raw signed body and the verification outcome, not
just the parsed result. Order-state integrity also underpins the pre-trade risk
controls required by SEC Rule 15c3-5 and MiFID II RTS 6 — a position ledger
corrupted by a double-counted fill will size the next order against a position
that does not exist. See `mappings/regulatory-coverage.md`.
