---
name: webhook-based-order-fill-notifications
description: >-
  Use when a trading bot receives order fill or execution notifications by
  inbound HTTP webhook. The first finding is usually that your broker does not
  send them: Interactive Brokers, Alpaca, TradeStation and Coinbase Advanced
  Trade all push fills over a persistent stream, not a webhook, so check before
  building a receiver. Where webhooks do exist they are weaker than they look --
  DhanHQ postbacks carry no signature at all, and Zerodha Kite's checksum covers
  only order_id + order_timestamp + api_secret, leaving the filled quantity
  unauthenticated. Covers HMAC-SHA256 verification over the raw body, the
  five-minute replay window, the atomic order_id:exec_id idempotency claim that
  must be taken once and never re-applied, out-of-order and missing-sequence
  detection, and the reconcile-before-you-book rule that keeps an unverifiable
  payload out of the position ledger.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- webhooks
- order-fills
- deduplication
- at-least-once-delivery
- idempotency
- replay-protection
- hmac-sha256
- reconciliation
brokers_frameworks:
- Zerodha Kite Connect v3 postbacks
- DhanHQ v2 postbacks
- Standard Webhooks specification
- OWASP Cheat Sheet Series (webhook security guidelines)
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this when a broker or venue delivers order and execution updates by POSTing
to an HTTP endpoint you host, and those updates feed a position ledger.

**Confirm the webhook exists before you build the receiver.** Outbound fill
webhooks are the exception, not the norm. Of the venues checked while writing
this skill:

| Venue | How fills actually arrive |
|---|---|
| Interactive Brokers | `EWrapper.execDetails` over the TWS API socket. The IBKR callback-notification service is account-management only (client registration, account changes, funding) and is enabled by request. |
| Alpaca | Server-Sent Events, `GET /v2/events/trades`, replayable by ULID. |
| TradeStation | Chunked HTTP streaming, `Content-Type: application/vnd.tradestation.streams.v3+json`. |
| Coinbase Advanced Trade | The authenticated WebSocket `user` channel. |
| Zerodha Kite Connect | A real postback: `POST` of a JSON body to the registered `postback_url`. |
| DhanHQ | A real postback, POSTed per order status change and per partial fill. |

If your venue is in the top four rows, you want
`websocket-reconnection-with-state-recovery`, not this skill.

**And settle the trust question before writing a single ledger update.** A
verified signature proves only that the bytes came from someone holding the
secret. It does not prove the payload matches broker state, and two of the two
brokers here that *do* send postbacks fall short of authenticating the payload:

- **DhanHQ** documents no signature, header, or shared secret on its postback.
  Anyone who learns the URL can POST a fill.
- **Zerodha Kite Connect** sends `checksum = sha256(order_id + order_timestamp +
  api_secret)`. The digest covers three fields. `filled_quantity`, `status` and
  `average_price` are outside it, so a captured postback can be edited in those
  fields and still pass verification.

So the webhook is a *hint that something changed*. The broker's authenticated
order or trades endpoint is the authority on *what* changed.

## When NOT to Use

- **Your broker streams fills.** A stream has a connection you can prove is
  alive and a resume cursor. A webhook has neither. Use
  `websocket-reconnection-with-state-recovery`.
- **You need a guarantee that no fill is missed.** No publisher surveyed here
  documents a delivery guarantee, a retry schedule, or an ordering guarantee.
  Webhooks are a latency optimisation over reconciliation, never a replacement
  for it — see `graceful-degradation-to-polling-fallback`.
- **The webhook is the only thing writing your positions.** Pair it with a
  periodic authenticated sweep; see `multi-broker-consolidated-position-view`.
- **You are deduplicating your own outbound orders.** That is
  `order-placement-idempotency`, and its key is client-supplied, not
  broker-supplied.

## Prerequisites

- A publicly reachable HTTPS endpoint. DhanHQ explicitly will not deliver to
  `localhost` or `127.0.0.1`.
- The shared webhook secret, held in a vault and rotatable without downtime
  (`secrets-rotation-without-bot-downtime`).
- Access to the **raw** request body, before the web framework parses it.
  Re-serialising a parsed dict changes key order and whitespace and will not
  reproduce the publisher's digest.
- An idempotency store keyed on `(order_id, exec_id)` whose check-and-set is
  atomic — a unique index or `SET NX`, not an in-process set, if more than one
  worker serves the endpoint.
- An authenticated broker endpoint to reconcile against.
- Host clock discipline. A 5-minute replay window is meaningless on a host whose
  clock has drifted further than that.

## Workflow

1. **Verify the signature over the raw bytes, before parsing.**
   Compute `HMAC-SHA256(raw_body, secret)` and compare with
   `hmac.compare_digest()`. Strip the algorithm label (`sha256=`, `v1,`) from
   the *front* of the token only. Accept a space-delimited list of tokens and a
   list of secrets, so a dual-secret rotation window works without dropping
   deliveries. Reject with **401** and parse nothing.
   *If your broker's signature covers only some fields (Kite) or nothing at all
   (Dhan), record that here and treat every field outside the digest as
   untrusted for the rest of the flow.*

2. **Reject a payload with no timestamp — do not assume "now".**
   Verify the event timestamp is within ±300 s of server time. A missing
   timestamp is a rejection, not a fresh one: defaulting it to the current time
   disables replay defence for precisely the payloads that omit it. Do this
   *before* the idempotency claim, so a stale replay cannot burn the key its
   legitimate twin will need.

3. **Extract fields defensively, and never 500.**
   A JSON `null` order id stringifies to the truthy `"None"`. An unguarded
   `float()` on a non-numeric quantity raises out of the handler. Python's
   `json.loads` accepts the non-standard `NaN` and `Infinity` literals, and a
   NaN quantity poisons a ledger silently because every later comparison
   returns False. Reject each of these with **400**. A 500 is worse than a 400:
   most publishers read a 5xx as "retry", so a malformed payload that crashes
   the handler retries forever.

4. **Claim `order_id:exec_id` atomically, once.**
   Check-and-set in one operation. A separate `if not seen: mark()` lets two
   request threads both conclude "not a duplicate". On a redelivery, return
   **200** so the publisher stops retrying, and return a result the caller
   cannot mistake for an applicable fill — a distinct status *and* a zeroed
   quantity. The historical bug here is a duplicate returning `SUCCESS` with the
   quantity intact and a caller doing `if status == "SUCCESS": position += qty`.
   *Retention must exceed the publisher's retry horizon, or a late retry is
   ingested as new. Bound the store so a runaway publisher cannot exhaust
   memory, and log loudly when the bound evicts anything.*

5. **Treat a same-key redelivery with different bytes as a correction, not a duplicate.**
   Interactive Brokers republishes a corrected execution as a fresh
   `execDetails` whose `execId` differs from the original *only in the digits
   after the final period* — so an IBKR correction is not a duplicate under an
   `exec_id` key, and it is not an additional fill either. Compare the body
   digest against the one stored at claim time; if it differs, flag for
   reconciliation rather than dropping it silently.

6. **Detect out-of-order and missing deliveries; do not silently reorder.**
   Track the highest `sequence_num` per `order_id` and the set of sequences
   seen. A sequence below the high-water mark means a `FILLED` was applied
   before its `PARTIALLY_FILLED`; a gap below the high-water mark means a
   delivery is outstanding. Surface both to the caller. Buffering a fill to wait
   for its predecessor needs a durable queue and a timeout policy, which is a
   risk decision the caller owns — so report the fact rather than hiding it.

7. **Reconcile, then book, then acknowledge.**
   Fetch the order or trades record from the broker's authenticated endpoint and
   book *that*, using the webhook only as the trigger. Return 200 once the event
   is durably recorded; do the reconciliation and ledger work off the request
   path via a queue, so a slow broker call does not stall the acknowledgement
   and trigger a redelivery. Log every rejection with the reason and a payload
   digest — never the secret — for `structured-logging-for-post-incident-forensics`.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Booking a redelivery because the status said SUCCESS.** At-least-once means
  the same fill arrives twice. If the duplicate result carries a non-zero
  quantity and a success-shaped status, some caller will eventually add it
  twice. Return one boolean the caller branches on, and zero the quantity behind
  it.
- **Defaulting a missing timestamp to `time.time()`.** This is a silent removal
  of replay protection that no test notices, because the payload that triggers
  it is the one that omits the field being checked.
- **Verifying the signature against re-serialised JSON.** `json.dumps(parsed)`
  is not the received body. Capture the raw bytes in middleware before the
  framework's body parser runs.
- **Stripping `sha256=` with `replace()`.** It removes the label from anywhere
  in the token, not just the front, silently altering the digest being compared.
- **Trusting a signature that does not cover the payload.** Kite's checksum
  authenticates the order id and timestamp; the quantity and status are not in
  the digest. A valid checksum on an edited body is still a valid checksum.
- **An in-process dedup set behind more than one worker.** Two gunicorn workers
  do not share a Python `set`; a redelivery routed to the other worker is a
  fresh event to it. The claim must live where every worker sees it.
- **An unbounded dedup set.** It is a memory leak in a process meant to run for
  months. An LRU/TTL bound is correct, but only if the window comfortably
  exceeds the publisher's retry horizon.
- **Returning 500 on a malformed payload.** Publishers retry 5xx. A payload your
  parser cannot handle will be redelivered until it ages out — reject it 400 and
  alert instead.
- **Doing the ledger write on the request path.** A slow broker reconciliation
  call delays the 200, the publisher times out and redelivers, and now you have
  concurrent processing of the same event.
- **Assuming an `exec_id` identifies a fill for all time.** An IBKR correction
  reuses everything but the digits after the final period of the `execId`.
- **Treating no webhook as no fill.** Nothing here guarantees delivery. Absence
  of a postback is not evidence an order did not fill.

## Verification

- Post a valid signed payload; confirm `status == "SUCCESS"`,
  `apply_to_ledger is True`, and `http_status == 200`.
- Post the same bytes five times; confirm the quantity is applied exactly once,
  that summing `filled_quantity` over results whose `status == "SUCCESS"` gives
  the single fill quantity, and that each redelivery returns `http_status == 200`.
- Post a tampered signature; confirm `INVALID_SIGNATURE` and `http_status == 401`,
  and that the body was never parsed.
- Post a valid signature over a body edited after signing; confirm rejection.
- Post a payload with **no** `timestamp` field; confirm `MISSING_TIMESTAMP`.
- Post a payload timestamped 3600 s in the past and one 9999 s in the future;
  confirm `TIMESTAMP_DRIFT_EXCEEDED` for both, and that neither consumed the
  idempotency claim.
- Post `filled_qty` values of `"abc"`, `null`, `-5` and the raw JSON literal
  `NaN`; confirm each returns a 400-class rejection and that no exception
  escapes the handler.
- Post `order_id: null`; confirm it is rejected and not accepted as `"None"`.
- Post sequences 3 then 1 for one order; confirm the late fill reports
  `out_of_order` and `requires_reconciliation`, and that `missing_sequences()`
  reports sequence 2 after delivering 1 and 4.
- Post the same execution key twice with different bodies; confirm
  `DUPLICATE_CONTENT_MISMATCH` and `requires_reconciliation`.
- Deliver the same event from 16 concurrent threads; confirm exactly one result
  has `apply_to_ledger is True`.
- Run `python -m unittest discover -s skills/webhook-based-order-fill-notifications/scripts` and
  confirm all tests pass.

## Related Skills

- `zerodha-kite-postback-webhook-verification`
- `order-placement-idempotency`
- `websocket-reconnection-with-state-recovery`
- `websocket-reconnect-without-duplicate-subscriptions`
- `graceful-degradation-to-polling-fallback`
- `multi-broker-consolidated-position-view`
- `secrets-rotation-without-bot-downtime`
- `structured-logging-for-post-incident-forensics`
