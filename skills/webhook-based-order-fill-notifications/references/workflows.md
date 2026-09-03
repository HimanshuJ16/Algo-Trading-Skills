# Deep Workflow Reference — webhook-based-order-fill-notifications

This file holds the full technical procedure referenced by `SKILL.md`. Load this
when actually implementing the skill, not just when deciding whether it applies.

Sources for every external claim are in `references/standards.md`.

## Step 0 — Establish that a webhook exists, and what it proves

Before writing a receiver, answer two questions from the broker's own docs:

1. **Does the broker POST fills to me at all?** Interactive Brokers, Alpaca,
   TradeStation and Coinbase Advanced Trade do not; they push over a socket,
   SSE, chunked HTTP and WebSocket respectively. Building a webhook receiver for
   those is building the wrong thing.
2. **What does the signature cover?** Not "is it HMAC-SHA256" — *which bytes*.
   Zerodha Kite's postback checksum is `sha256(order_id + order_timestamp +
   api_secret)`, so the quantity and status are unauthenticated. DhanHQ
   documents no signature at all.

Write the answer down in the integration's own README. Everything downstream
depends on it, and it is the fact most likely to be wrong in an LLM-generated
integration.

## Step 1 — Capture the raw body

The digest is over the exact bytes on the wire. `json.dumps(parsed_dict)` is not
those bytes: key order, whitespace and float repr all differ.

- Flask: `request.get_data()` before touching `request.json`.
- FastAPI/Starlette: `await request.body()` in the endpoint, or a middleware
  that caches it.
- Django: `request.body` before any parser runs.

Webhook routes must also be exempted from the framework's CSRF token check —
the publisher is a server and cannot supply a token. Scope the exemption to the
webhook route only, and confirm HMAC verification is in place first: the
signature check *is* the replacement control.

## Step 2 — Verify the signature

```
expected = HMAC-SHA256(raw_body, secret)
compare with hmac.compare_digest()
```

Four details that are routinely wrong:

- **Strip the algorithm label from the front only.** `sha256=` (GitHub style)
  and `v1,` (Standard Webhooks). A `signature.replace("sha256=", "")` rewrites
  the token anywhere the literal appears.
- **Accept hex *and* base64.** Standard Webhooks emits base64
  (`v1,K5oZfzN95Z9UVu1EsfQmfVNQhnkZ2pj9o9NDN/H/pI4=`); GitHub-style publishers
  emit hex. A verifier that assumes one rejects the other outright.
- **Accept multiple tokens.** During a key rotation the publisher sends a
  space-delimited list, one token per active key. Verify against a list of
  secrets and accept if any pair matches; this is the dual-secret window that
  lets a secret rotate with no dropped deliveries.
- **Constant-time compare, no early exit on the outer loop either.** Compare
  every candidate so total work does not leak which one matched.
- **Bound the work.** Verification runs on wholly unauthenticated input, so cap
  the number of tokens you will examine and the length of each. A rotation
  window needs two or three tokens; a header carrying twenty thousand is an
  attempt to make the endpoint do unbounded HMAC and hex-decode work for free.

On failure: log, return **401**, and do not parse the body. Unauthenticated
bytes should never reach a JSON parser.

## Step 3 — Replay defence

Reject if `|T_now − T_event| > 300 s`. Both the Standard Webhooks specification
and OWASP's webhook guidance put the tolerance at five minutes.

**A missing timestamp is a rejection.** The single most damaging line this skill
previously contained was

```python
ts_val = payload.get("timestamp", payload.get("time", time.time()))   # WRONG
```

which substitutes the current time for an absent timestamp — so every payload
lacking the field passes freshness by construction, and the check silently
protects nothing. There is no safe default here.

Practical parsing notes:

- Accept epoch seconds, epoch milliseconds (normalise anything above ~1e11), and
  ISO-8601. Treat a naive ISO-8601 stamp as UTC; assuming host-local time
  rejects valid events on any non-UTC host.
- Reject booleans explicitly. `isinstance(True, int)` is `True` in Python, so
  `True` otherwise parses as epoch second 1.
- Consider a tighter *future* bound than the past bound. A late delivery is
  routine; an event stamped ten minutes in the future is a clock fault or a
  forgery.
- The window is only as good as the host clock. A drifted host either rejects
  everything or accepts stale replays.

Do the freshness check **before** the idempotency claim, so a stale replay of
`ORD:EXEC` cannot consume the key that the legitimate delivery of `ORD:EXEC`
will need.

## Step 4 — Extract fields without crashing

The payload is authenticated but not trusted, and a handler that raises returns
a 500, which most publishers read as "retry" — so a malformed payload retries
until it ages out.

| Hazard | What goes wrong | Handling |
|---|---|---|
| `{"order_id": null}` | `str(None)` is `"None"`, which is truthy and passes an emptiness check as a real order id | Reject `None`, `bool`, `dict`, `list` before stringifying; reject blank after stripping |
| `{"filled_qty": "abc"}` | `float()` raises `ValueError` out of the handler → 500 → infinite retry | Guarded coercion → **400** |
| `{"filled_qty": null}` | `float(None)` raises `TypeError` | Same |
| `NaN` / `Infinity` literals | Python's `json.loads` accepts these non-standard JSON constants by default. A NaN quantity propagates silently: every subsequent comparison against it is False, so no threshold, limit or reconciliation check fires | `json.loads(..., parse_constant=<raise>)`, then `math.isfinite` |
| Negative quantity | A fill quantity is a magnitude; direction belongs in a side field | Reject |
| Top-level JSON array | `payload.get` raises `AttributeError` | Reject `PAYLOAD_NOT_OBJECT` |
| Invalid UTF-8 | `.decode()` raises | Reject `INVALID_ENCODING` |

Return 400 for all of these, and alert — a publisher sending payloads your
parser rejects is an integration defect, not noise.

## Step 5 — The idempotency claim

Key: `order_id:exec_id`. Both halves are required — an `exec_id` alone can
collide across orders in some schemas, and an `order_id` alone collapses every
partial fill of an order into one event.

**The check and the set must be one atomic operation.**

```python
# WRONG - two operations; two threads can both take the "not seen" branch
if key not in seen:
    seen.add(key)
    apply(fill)
```

The reference implementation takes both under one lock in
`claim_execution()`, which is correct within a process. It is *not* correct
across processes: two gunicorn workers do not share a Python `set`, so a
redelivery routed to the other worker is a brand-new event to it. For any
multi-worker or multi-host deployment, replace `claim_execution()` with:

- a database `INSERT` against a `UNIQUE (order_id, exec_id)` constraint, where
  the duplicate-key violation *is* the duplicate detection; or
- `SET <key> <value> NX EX <retention>` in Redis, where a falsey reply is the
  duplicate.

Both are single round-trips and both are atomic. `claim_execution()` is
deliberately the only seam you need to replace.

**Retention.** Remember a claimed key for longer than the publisher's retry
horizon, or a late retry is ingested as a new fill. No publisher surveyed
documents its retry horizon, so the reference default is 24 hours. Bound the
store as well — an unbounded set is a memory leak in a process meant to run for
months — and log loudly when the bound evicts, because an eviction means later
redeliveries of that key will no longer be recognised.

Decide expiry **per key at lookup**, and let the whole-store scan run on an
interval. Sweeping every claim on every request makes ingestion quadratic in the
number of live claims — the sweep is memory reclamation, not correctness, and the
per-key check is what keeps an expired claim from being reported as a duplicate.

**Responding to a duplicate.** Return **200**. A duplicate is not an error, and a
non-2xx tells the publisher to retry the thing you just told it you already
have. And return a result the caller cannot misread:

```python
# The failure this design exists to prevent:
if result.status == "SUCCESS":        # duplicates also returned SUCCESS
    position += result.filled_quantity  # ... with the quantity intact
```

One boolean (`apply_to_ledger`) is the caller's branch, and the quantity is
zeroed behind it, so even a caller that ignores the flag cannot double-count.

## Step 6 — Corrections vs duplicates

A redelivery of the same key with *different bytes* is not an ordinary
duplicate. Two things produce it:

1. **A publisher correction.** Interactive Brokers documents that a correction
   arrives as an additional `execDetails` "with all parameters identical except
   for the execID … [which] will differ only in the digits after the final
   period." That variant does not even collide on the key — it presents as a new
   execution, so naive accumulation books a phantom fill.
2. **A replay of a mutated payload**, which is exactly what a weak signature
   scope (Kite) or an absent one (Dhan) permits.

Store a SHA-256 digest of the raw body at claim time. On a redelivery, compare.
If it differs, do not silently drop it: flag it for reconciliation and resolve
it against the broker's authoritative fill record. Never resolve a correction by
arithmetic on webhook payloads.

## Step 7 — Sequencing

Track, per `order_id`, the highest `sequence_num` seen and the set of sequences
seen.

- **A sequence below the high-water mark** means a later event was processed
  first — a `FILLED` applied before its `PARTIALLY_FILLED`, corrupting the order
  state machine.
- **A gap below the high-water mark** means a delivery is outstanding. This is
  the one detector that catches a *lost* webhook, which no signature or dedup
  check can.

Treat sequence `0` as a real sequence number. A guard of `if seq_num > 0`
silently ignores the first event of any zero-based publisher.

This module reports; it does not reorder. Holding a fill back until its
predecessor arrives requires a durable buffer, a timeout, and a decision about
what to do when the predecessor never comes — a risk decision belonging to the
caller. Surfacing `out_of_order` and `missing_sequences()` lets the caller make
it explicitly instead of inheriting a silent default. Do not claim in
documentation that events are "queued for re-ordering" unless a queue exists.

## Step 8 — Acknowledge fast, reconcile off the request path

```
receive → verify → claim → 200 → enqueue → reconcile → book
```

Acknowledge once the event is *durably recorded*, not once it is fully
processed. Reconciliation calls the broker, and a slow broker call on the
request path delays the 200 past the publisher's timeout, triggering a
redelivery and concurrent processing of the same event. OWASP's guidance is to
"decouple ingestion from processing with an async queue."

Reconciliation means fetching the order or trades record from the broker's
authenticated endpoint and booking *that*. The webhook supplies the trigger and
the timing; the authenticated endpoint supplies the truth. This is not optional
belt-and-braces for a publisher whose signature does not cover the quantity.

Pair it with a periodic authenticated sweep on a timer. Webhooks are a latency
optimisation over reconciliation, never a substitute: with no documented
delivery guarantee, absence of a postback is not evidence that no fill occurred.

## Failure Modes Observed in Production

- **Double-counted position from a redelivered webhook.** The duplicate branch
  returned a success-shaped status with the quantity populated, and the caller
  accumulated on status.
- **Replay protection that protected nothing**, because a missing timestamp
  defaulted to the current time.
- **Handler 500 → infinite retry loop.** An unguarded `float()` on a
  non-numeric quantity, redelivered indefinitely because the publisher reads 5xx
  as retryable.
- **Silent NaN in the position ledger.** `json.loads` accepted the `NaN`
  literal; every downstream risk comparison against NaN returned False, so no
  limit fired.
- **Duplicate processing after horizontal scaling.** The dedup set was correct
  for months on one worker and broke the day a second was added.
- **Spoofed fills on an unauthenticated endpoint.** A postback URL with no
  signature (Dhan's shape) accepts a fill from anyone who learns the URL.
- **Phantom fill from a broker correction** booked as an additional execution.

## Production Implementation Reference

- Reference code: `scripts/webhook_consumer.py` — `WebhookConsumerManager`,
  `WebhookIngestionResult`, `WebhookStatus`, `ClaimRecord`.
- Automated unit tests: `scripts/test_webhook_consumer.py`. The tests marked
  `REGRESSION` each pin a defect that shipped in v1.0.0 of this skill and fail
  against that implementation.
- The module verifies and classifies. It deliberately does not mutate a ledger
  or call a broker: `apply_to_ledger` and `requires_reconciliation` are the two
  flags the caller acts on.
