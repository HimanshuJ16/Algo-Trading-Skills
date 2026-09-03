---
name: zerodha-kite-postback-webhook-verification
description: >-
  Use when consuming Zerodha Kite Connect order postbacks. The checksum Kite sends is
  SHA-256(order_id + order_timestamp + api_secret) — it authenticates those two fields
  and nothing else, so status, filled_quantity and average_price arrive unauthenticated
  and a verified postback is a trigger to reconcile, never a fact to post to a ledger.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- zerodha-kite-connect
- postback-webhooks
- webhook-security
- idempotency
brokers_frameworks:
- Zerodha Kite Connect v3 API
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this whenever a bot exposes an HTTPS endpoint as its Kite Connect app's postback
URL and consumes order updates from it. Kite sends the update as a **raw JSON POST body**
— there are no signature headers — and anyone on the internet who learns that URL can
POST to it. Without verification, a fabricated `"status": "COMPLETE"` postback can drive
a strategy to hedge against a fill that never happened, or a position ledger to record
inventory the account does not hold.

**Read the checksum's scope before you design around it.** Kite documents the
`checksum` field as `SHA-256(order_id + order_timestamp + api_secret)`. That covers the
order identifier and the update instant. It does **not** cover `status`,
`filled_quantity`, `average_price`, `pending_quantity`, or anything else in the body.
An attacker who captures one postback in transit can rewrite those fields and the
checksum still matches. So the correct reading of a verified postback is *"Zerodha says
order X changed at instant T"* — the **what** must come from `GET /orders/:order_id`.
This is the single most consequential thing to get right here, and it is a property of
Kite's scheme, not something a verifier can fix.

## When NOT to Use

- **As the only way you learn about fills.** Kite publishes no retry, ordering, or
  delivery guarantee for postbacks, and its own forum documents deliveries lost to
  unreachable endpoints and non-443 ports. Silence is not evidence that nothing
  happened — keep a periodic `GET /orders` reconciliation sweep regardless.
- **For non-Kite webhooks.** The scheme here is broker-specific. Do not port this
  verifier to another broker's postback without reading that broker's own docs; see
  `references/standards.md` and `webhook-based-order-fill-notifications` for the
  generic HMAC-over-raw-body pattern most other venues use.
- **As a substitute for order-submission idempotency.** This deduplicates *inbound*
  notifications. Guaranteeing you never double-*submit* is `order-placement-idempotency`.
- **As a risk control.** Nothing here bounds exposure or drawdown. See
  `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Kite Connect `api_secret` loaded from an environment variable or vault — never from
  source. The digest is forgeable by anyone who holds it.
- An HTTPS endpoint on **port 443**: Zerodha's forum records that outbound ports other
  than 80/443 are blocked, so a postback URL on any other port silently never fires.
- A durable store of processed-event fingerprints if order state must survive a restart.
  The in-process LRU in `scripts/postback_verifier.py` is bounded and per-process.
- The ability to call `GET /orders/:order_id` from the handler, for reconciliation.

## Workflow

1. **Read the raw body, do not re-serialize it.**
   Kite posts JSON with no signature headers. Parse `order_id`, `order_timestamp`,
   `checksum`, `status`, `filled_quantity`, `average_price` from the body. The field is
   `order_timestamp` — not `timestamp` — and it is the exact string that must be fed to
   the digest. Reformatting it (normalising the separator, attaching a timezone) changes
   the pre-image and every checksum will then fail.

2. **Reject structurally unusable payloads first.**
   Both `order_id` and `order_timestamp` are checksum inputs; if either is absent there
   is nothing to verify. Return 200 and drop it — do not raise out of the handler.

3. **Verify the checksum, failing closed.**
   Compute `SHA-256(order_id + order_timestamp + api_secret)` and compare with
   `hmac.compare_digest` **on bytes**, after checking the received value is 64 hex
   characters. If the `checksum` field is missing or empty, **reject** — an absent
   signature is not a pass. Comparing `str` values directly raises `TypeError` on
   non-ASCII input that the attacker controls.
   Verify *before* the freshness check, so an unauthenticated payload cannot influence
   `order_id`-keyed logging or timing, and so "stale" and "forged" stay distinguishable.

4. **Bound freshness in both directions, in the right timezone.**
   `order_timestamp` is `YYYY-MM-DD HH:MM:SS` with **no timezone marker** and is
   exchange-local IST (UTC+05:30). Parsing it as server-local time on a UTC host makes
   every postback look 5.5 hours stale. Bound the past (default 300s) and the future
   (small, for clock skew) separately: an old payload is a delayed or replayed delivery;
   a post-dated one is skew or tampering, and they warrant different responses.
   Note the limit of this check — because the checksum is static for a given order
   update, an attacker replaying *inside* the window still verifies. Freshness is a
   staleness guard; deduplication is the actual replay defense.

5. **Deduplicate on the full state fingerprint, not on `order_id` + `status`.**
   Kite emits an `UPDATE` postback for *every* partial fill, and successive partial
   fills of the same order all carry `status == "OPEN"`. Keying dedup on the status
   silently discards every fill after the first and leaves the ledger short. Fingerprint
   `order_id`, `order_timestamp`, `status`, `filled_quantity` and `average_price`
   together, and claim it atomically — test-and-insert under one lock, or a unique
   constraint in the database — so two concurrent redeliveries cannot both win.

6. **Reconcile before mutating state.**
   On an accepted postback, call `GET /orders/:order_id` and apply *that* response to
   the ledger. The postback's own `status`/`filled_quantity` are unauthenticated
   (step 3's scope) and are safe only as a hint about which order to re-read.

7. **Acknowledge and alert.**
   Return 2xx quickly regardless of outcome — a non-2xx buys you nothing, since there is
   no documented retry. Log rejections with the outcome class, order id, source IP and a
   truncated checksum fragment. Never log the `api_secret` or a full received checksum.
   A checksum mismatch is a security event; staleness usually is not.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table for this skill: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Verifying only when a checksum is present.** `if received_checksum:` around the
  comparison is a fail-open authentication bypass: an attacker simply omits the field
  and the payload sails through as verified.
- **Trusting the body because the checksum matched.** The digest covers `order_id` and
  `order_timestamp` only. A replayed postback with `filled_quantity` rewritten verifies
  perfectly. Reconcile against `GET /orders/:order_id`.
- **Deduplicating on `order_id:status`.** Every partial fill after the first shares its
  predecessor's `OPEN` status and gets thrown away, so the ledger under-reports quantity
  on exactly the orders that fill in pieces.
- **Reporting a duplicate as "valid".** A handler written `if result.valid: apply()`
  then double-applies the redelivery the dedup step just caught. "Authentic" and "safe
  to apply" are different answers and need different fields.
- **Treating `order_timestamp` as UTC or as epoch seconds.** It is naive IST text. A
  parser that only understands epoch numbers rejects 100% of genuine postbacks — and a
  test suite that feeds it epoch strings will never notice.
- **Comparing checksums as `str`.** `hmac.compare_digest` raises `TypeError` on
  non-ASCII `str`, turning an attacker-supplied checksum into an unhandled 500.
- **An unbounded `set` of processed events.** A long-lived receiver leaks memory for the
  life of the process. Bound it, and persist it if restarts must stay idempotent.
- **Assuming a missed postback means nothing happened.** No delivery guarantee is
  documented; a blocked port or a brief outage loses the update silently.

## Verification

- A verbatim copy of the example payload in the Kite Connect v3 postback docs, with a
  checksum derived independently of this code, verifies as `ACCEPTED`.
- A payload with `checksum` absent, empty, or null is rejected as
  `REJECTED_MISSING_CHECKSUM` — never accepted.
- A checksum that is non-hex, wrong-length, or non-ASCII is rejected without raising.
- Three successive partial fills of one order (same `OPEN` status, rising
  `filled_quantity`) are all accepted; a byte-identical redelivery returns `DUPLICATE`
  with `valid is False`.
- The same naive timestamp string is fresh when read as IST and stale when read as UTC,
  proving the timezone is applied rather than assumed.
- Sixteen threads submitting one payload simultaneously yield exactly one `ACCEPTED`.
- Run `python -m unittest discover -s skills/zerodha-kite-postback-webhook-verification/scripts` and confirm a
  100% pass rate.

## Related Skills

- `order-placement-idempotency`
- `webhook-based-order-fill-notifications`
- `headless-broker-auth-patterns`
- `multi-broker-rate-limit-handling`
- `structured-logging-for-post-incident-forensics`
