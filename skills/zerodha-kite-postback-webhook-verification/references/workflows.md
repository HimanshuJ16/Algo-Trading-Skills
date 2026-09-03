# Deep Workflow Reference — zerodha-kite-postback-webhook-verification

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## The trust model, stated once

Kite's postback checksum is `SHA-256(order_id + order_timestamp + api_secret)`. Work
outward from what that string contains:

- The `api_secret` in the pre-image means a matching checksum could only be produced by
  someone holding the secret. **Sender authenticity: established.**
- `order_id` and `order_timestamp` are in the pre-image, so neither can be altered
  without breaking the digest. **Those two fields: integrity-protected.**
- Nothing else is in the pre-image. `status`, `filled_quantity`, `average_price`,
  `pending_quantity` and the rest can be rewritten freely by anyone who can modify the
  request in flight, and the checksum still matches. **Body contents: unauthenticated.**

So the strongest true statement about a verified postback is: *Zerodha is telling us
something changed on order X at instant T.* Everything downstream follows from refusing
to claim more than that.

## Full Procedure

### 1. Receive the raw body

Kite POSTs JSON as the raw request body; there are no signature headers to read. Capture
the body bytes before any framework middleware re-serializes them, and pull:
`order_id`, `order_timestamp`, `checksum`, `status`, `filled_quantity`, `average_price`.

The field is `order_timestamp`. `timestamp` is not a Kite postback field; accept it only
as a fallback if something in your own pipeline renamed it.

Keep `order_timestamp` as the **exact string received**. It is a digest input. Parsing it
into a `datetime` and formatting it back — even to an identical-looking string — is how
this breaks: one normalised separator or stripped leading zero changes the pre-image and
every subsequent checksum fails.

### 2. Structural validation

If `order_id` or `order_timestamp` is absent or empty, there is no digest to compute.
Classify as malformed, log, return 2xx, drop. The forum records postbacks arriving with
fields missing, so this path is reached in practice and must not raise.

Coerce `filled_quantity` to a non-negative integer. Kite quantities are integral; a
fractional, negative, or unparseable value is malformed input, not something to truncate
silently. `float(payload["filled_quantity"])` on attacker-controlled text raises
`ValueError` straight out of the handler.

### 3. Checksum verification — fail closed

```python
expected = hashlib.sha256(
    f"{order_id}{order_timestamp}{api_secret}".encode("utf-8")
).hexdigest()
```

Then, before comparing:

- **A missing or empty `checksum` field is a rejection, not a skip.** Guarding the
  comparison with `if received_checksum:` is a fail-open authentication bypass — an
  attacker omits the field and the payload is treated as verified.
- **Validate shape first:** exactly 64 characters, all hex. This rejects junk before it
  reaches the comparison and makes the next point safe.
- **Compare bytes, not `str`.** `hmac.compare_digest` on `str` arguments raises
  `TypeError: comparing strings with non-ASCII characters is not supported`, and the
  received checksum is fully attacker-controlled. `bytes.fromhex()` both sides.
- Case-fold the received value; hex digests are case-insensitive.
- An empty `api_secret` must raise loudly. It is a deployment defect: with an empty
  secret the digest is derivable by anyone who can read the public docs.

Run this **before** the freshness check. Two reasons: an unauthenticated payload should
never reach code that logs or times on its `order_id`, and once ordering is fixed the
outcome classes stay meaningful — an authentic-but-stale message is an operations
problem (clock skew, delayed delivery), a checksum mismatch is a security event. Merging
them into one "rejected" bucket loses the distinction that decides who gets paged.

### 4. Freshness — bounded both ways, in IST

`order_timestamp` is `"YYYY-MM-DD HH:MM:SS"` with no timezone marker. It is
exchange-local IST (UTC+05:30, no DST). Parse with an explicit offset:

```python
IST = timezone(timedelta(hours=5, minutes=30))
moment = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
```

Reading it as server-local time makes every postback look 5.5 hours old on a UTC host —
100% rejection, and a symptom easily misread as "Zerodha stopped sending postbacks."

Bound past and future separately:

- **Past** (default 300s): a delayed or replayed delivery.
- **Future** (small, e.g. 60s): absorbs genuine clock skew between Zerodha's host and
  yours. A payload dated far ahead is skew worth alerting on, or tampering — not the
  same event as a stale one, and `abs(drift)` conflates the two.

**Be honest about what this buys.** The checksum for a given order update never changes,
so an attacker replaying a captured postback *within* the window passes both the
signature and the freshness check. Freshness bounds how long a captured message stays
useful; it is not the replay defense. Deduplication is.

### 5. Idempotency — fingerprint the whole state change

Do not key on `order_id` + `status`. Kite emits an `UPDATE` postback for every partial
fill, and successive partial fills of one order all carry `status == "OPEN"`. A
status-keyed dedup accepts the first fill and silently discards the rest, so the ledger
under-reports quantity on precisely the orders that fill in pieces — and it does so
without an error anywhere.

Fingerprint the fields that determine the mutation:

```
order_id | order_timestamp | status | filled_quantity | average_price
```

Claim it **atomically** — test-and-insert under a single lock, or as a unique-constraint
insert in the database. A check followed by a separate insert lets two concurrent
redeliveries both observe "not seen" and both proceed; webhook servers are concurrent by
default, so this is a live race, not a theoretical one.

Bound the store. An unbounded `set` in a process that runs for weeks is a memory leak;
an LRU with an explicit cap trades a small chance of re-accepting a very old event for a
fixed memory ceiling. If idempotency must survive a restart, the store has to be durable
— an in-process cache resets to empty on deploy, and the first redelivery after that
looks new.

### 6. Reconcile before mutating

On acceptance, call `GET /orders/:order_id` and apply **that** response to the position
and order ledger. The postback's own `status` and `filled_quantity` are unauthenticated
(section "The trust model"); their legitimate use is to tell you *which* order to re-read
and to let you skip a fetch you don't need.

Hand the reconciled state to the order ledger described in
`order-placement-idempotency` rather than mutating positions from the handler.

### 7. Respond and alert

Return 2xx promptly whatever the outcome. Kite documents no retry, so a non-2xx response
does not buy a redelivery — it only slows the handler. Do the reconciliation fetch off
the request path if it risks a timeout.

Log per delivery: outcome class, `order_id`, event fingerprint, source IP, and a
truncated fragment of the received checksum. Never log the `api_secret`, and never log a
full received checksum. Alerting policy follows the outcome class — checksum mismatches
and missing-checksum rejections are security signals; staleness and duplicates are
operational noise until they spike.

### 8. Do not rely on postbacks alone

No delivery guarantee is documented, and the forum records postbacks lost to unreachable
endpoints and to non-443 ports. Keep a periodic `GET /orders` sweep that reconciles the
full open-order set, so a missed postback surfaces as a reconciliation difference rather
than as a position you find out about at settlement.

## Known Failure Modes

- **Fail-open signature check.** Verifying only `if a checksum field is present` — an
  attacker omits it and forged fills are accepted as verified.
- **Wire-format mismatch.** A timestamp parser that only understands epoch seconds
  rejects every genuine Kite postback, and a test suite that feeds it epoch strings
  passes anyway. The tests looked green while nothing worked.
- **Naive timestamp read as UTC.** A constant 5.5-hour false drift; total rejection on a
  UTC host, silent success on a developer's IST laptop.
- **Status-keyed deduplication.** Partial fills after the first are dropped; the ledger's
  quantity is quietly wrong for exactly the orders that matter most.
- **Duplicate reported as valid.** A handler written `if result.valid: apply()`
  double-applies the redelivery that dedup had already identified.
- **`TypeError` on a non-ASCII checksum.** Attacker-controlled input crashes the handler
  through `hmac.compare_digest`'s `str` mode.
- **Concurrent redelivery race.** Non-atomic check-then-insert lets two workers both
  apply the same fill.
- **Unbounded processed-event set.** Memory grows for the life of the process.
- **Trusting body fields after a passing checksum.** The failure this skill's trust model
  exists to prevent: a rewritten `filled_quantity` on a replayed postback verifies
  perfectly and posts phantom inventory to the ledger.

## Production Implementation Reference

- Reference code: `scripts/postback_verifier.py` (`KitePostbackVerifier`,
  `PostbackVerificationResult`, `PostbackOutcome`).
- Automated unit tests: `scripts/test_postback_verifier.py` — built on the verbatim
  example payload from the Kite Connect v3 postback docs, with the expected digest
  derived externally rather than by re-running the module's own formula.
