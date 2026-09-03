# Deep Workflow Reference — order-placement-idempotency

This file holds the full technical procedure referenced by `SKILL.md`. Load this when
actually implementing the skill, not just when deciding whether it applies.

## The invariants the implementation must hold

`scripts/order_ledger.py` is built around five invariants, each covered by a test in
`scripts/test_order_ledger.py`. If you write your own implementation, these are the parts
worth copying:

1. The `PENDING` intent row is committed to durable storage **before** the broker call.
2. One `place_order` call issues **at most one** broker call — and none at all while an
   earlier intent for the same key is unresolved and reconciliation has not proved the order
   absent.
3. Any outcome the broker did not state unambiguously is `UNKNOWN` — never `REJECTED`, never
   `PLACED`. A failure *word* is not a refusal: only an explicit rejection status or a
   documented refusal `error_type` reaches `REJECTED`.
4. `UNKNOWN` resolves to `ABSENT` (safe to re-send) **only** when the broker echoes the client
   key back in its order book, so that absence is evidence — and an `ABSENT` verdict releases
   the ledger claim, which is what makes the re-send it authorises possible.
5. The intent state machine refuses illegal transitions; `PLACED` and `REJECTED` are terminal.

## Full Procedure

### 1. Derive a stable idempotency key

`make_idempotency_key(strategy_id, symbol, side, signal_ts, qty, price, sequence, max_len)`
canonicalises before hashing:

- symbol and side are trimmed and upper-cased;
- numbers render at fixed precision, so `50` and `50.0` hash identically;
- `datetime` values normalise to UTC (naive values are *assumed* UTC and logged), so a
  `datetime`, its ISO string and an equivalent value in another timezone all agree;
- `sequence` distinguishes orders that are legitimately identical — two child slices of one
  parent at one signal timestamp — which would otherwise collapse onto one key and lose the
  second order.

Set `max_len` to the broker's tag limit. Kite Connect's `tag` is capped at 20 alphanumeric
characters; the 24-character default does not fit, and a truncated key cannot be matched on.

### 2. Claim the intent before the network call

`OrderLedger.record_intent()` inserts a `PENDING` row whose primary key is the idempotency
key, and **returns whether this caller created it**. That boolean is the claim:

- `True` → this caller owns the send;
- `False` → an intent already exists; the caller must reconcile, and must not send.

The uniqueness constraint is what makes the sequence safe across threads and across processes
sharing one database file. A `SELECT` followed by an `INSERT` is a check-then-act race, and
under a retry storm both racers reach the broker.

Open the ledger on a real file path with `durable=True` (`journal_mode=WAL`,
`synchronous=FULL`). An in-memory ledger cannot survive the crash it exists to protect
against; the module logs a warning when one is opened.

### 3. Send once, then classify into three outcomes

| Broker behaviour | Ledger status | Next step |
|---|---|---|
| Response carries an order id and a success token | `PLACED` | Store the id (all of them, if the broker sliced the request) |
| Response carries an explicit rejection status (`rejected`, `cancelled`) | `REJECTED` | Classify the reason before deciding whether a *new* order is warranted |
| Response carries a refusal `error_type` — `InputException`, `OrderException`, `MarginException`, `PermissionException`, `TokenException` | `REJECTED` | As above; the broker evaluated the request and declined it |
| Response carries a transport/gateway `error_type` (`NetworkException`, `GatewayTimeout`, `DataException`, `GeneralException`), or any 5xx | `UNKNOWN` | Reconcile (step 4) |
| Response is ambiguous, unparseable, or a success with no order id | `UNKNOWN` | Reconcile (step 4) |
| The call raised — timeout, connection reset, TLS error, library error | `UNKNOWN` | Reconcile (step 4) |

`classify_broker_response()` recognises the flat shape, the Kite Connect
`{"status": "success", "data": {"order_id": …}}` shape, and the Kite auto-slice shape where
`data` is a list of order ids. Everything unrecognised falls through to `UNKNOWN`.

The `status` word is not the classifier's evidence, because Kite spends `{"status": "error"}`
on both a flat refusal and a gateway fault; `error_type` is. An unrecognised error class is
`UNKNOWN` by design — the conservative direction, since a refusal misread as unknown costs one
order-book query, while a gateway fault misread as a refusal costs a duplicate order. Only
`status_code`/`http_status` are read as HTTP statuses; Alpaca's numeric `code` is an error code
and reading it as one would misfile `40310000` as a 5xx.

The exception handler is deliberately broad. A client library can raise almost anything on a
lost response, and every one of those cases means "outcome unknown", not "order failed".
Narrowing it to `TimeoutError` silently mishandles the rest.

### 4. Reconcile `UNKNOWN` as a tri-state

`ReconcileOutcome` has four values and the distinction between the last two is the whole
point:

| Outcome | Meaning | Router action |
|---|---|---|
| `FOUND_PLACED` | The key (or a strict attribute match) is in the order book | Link the broker order id, mark `PLACED` |
| `FOUND_REJECTED` | The matched book entry is in a rejected state | Mark `REJECTED` with the broker's reason |
| `ABSENT` | The broker echoes client keys and this key is not among them | Release the claim (archiving the row) and re-send under the same key |
| `INCONCLUSIVE` | Cannot tell | Park, alert, **never re-send** |

Cases that must return `INCONCLUSIVE` rather than `ABSENT`:

- the broker does not echo the client key, so a miss carries no information;
- the order-book query itself failed — most likely for the same reason the response was lost;
- a book entry matched but carries no usable broker order id;
- the matched entry's status is a bare `error`/`failed` — it is in the book, so the order
  exists and must not be re-sent, but calling it `REJECTED` asserts more than the broker said;
- attribute matching found more than one equally plausible candidate.

### 4a. Releasing the claim after `ABSENT`

The ledger row is what blocks a re-send, so "safe to re-send" is only true once the row is
gone. `OrderLedger.release_intent(key, reason)` copies the row into a `released_intents`
archive and deletes it from `orders`; `record_intent` can then claim the key again. Three
properties make that safe rather than a back door:

- it is reached **only** from an `ABSENT` verdict, which requires `broker_echoes_key=True` and
  a successful order-book query that did not contain the key;
- it refuses a `PLACED`/`REJECTED` row outright — releasing a settled outcome would license a
  second send of a live order;
- `UNKNOWN -> PENDING` is *not* in the state machine, so there is no quiet way to re-arm an
  unresolved intent that skips the release and its archived record.

Where the re-send happens depends on whether this call has already used its one broker call:

- the claim was left by an **earlier** call or a crash → `place_order` reconciles, releases,
  re-claims and sends, all inside the one invocation, and returns that send's outcome;
- the claim was made by **this** call, whose send then proved not to have landed →
  `ABSENT_SAFE_TO_RESEND` is returned and the claim is released; the caller's next
  `place_order` with the same arguments claims afresh and sends exactly once.

### 5. Attribute matching — only when the key cannot be echoed

For brokers that accept a tag but do not return it, matching falls back to order attributes.
Keep it strict, because a loose match produces a *phantom* position — the ledger says an order
exists that never landed, which is the mirror image of a duplicate and harder to notice:

- symbol, side, quantity **and** price must all agree;
- the book entry must be timestamped within `fuzzy_window_s` (default 300s) of the intent;
  an entry with no usable timestamp is skipped rather than accepted;
- any broker order already linked to another ledger row is excluded, so one broker order is
  never credited to two intents;
- more than one qualifying candidate escalates instead of picking one.

This is strictly weaker than a broker-echoed key. Document the residual risk wherever it is
relied on.

### 6. Startup crash recovery

`IdempotentOrderRouter.recover_unresolved(broker_order_book_fn, stale_after_s=0)` sweeps every
`PENDING`/`UNKNOWN` row and reconciles it, returning `{key: ReconcileResult}`. Run it after any
restart or reconnect, **before** the strategy is permitted to emit a signal.

Any result whose `resolved` is `False` fires `alert_fn` and must block the strategy. An
unresolved intent is an order that may or may not be live; generating fresh signals on top of
it is exactly how a restart doubles a position.

## Known Failure Modes

- **Blind retries on timeout.** Retrying on a lost response without reconciling. The canonical
  double fill.
- **Retrying past the guard.** The first call handles the timeout correctly; the caller's own
  retry loop then calls `place_order` again with identical arguments, and a guard that only
  checks for `PLACED` lets the send through. An unresolved intent must *block*.
- **"Not a success" read as a rejection.** A strict equality check against one success shape
  files Kite's real acknowledgement as a rejection, so the ledger reports no order while one
  works, and the next signal duplicates it.
- **Fabricated order ids.** Defaulting a missing order id to a placeholder makes the ledger
  look complete and makes the order unreconcilable.
- **Loose attribute reconciliation.** Matching on `(symbol, side, quantity)` adopts an earlier
  identical order — a phantom fill for any strategy that scales into a position.
- **Post-network intent logging.** Recording intent after the HTTP call, losing the record
  when the process dies mid-call.
- **In-memory-only order tracking.** Order state in RAM, gone on restart.
- **Check-then-act races.** Two threads or two replicas both observing "no intent yet".
- **Type-unstable keys.** `50` versus `50.0`, naive versus aware datetimes, `"buy"` versus
  `"BUY"` — each pair derives two keys for one order.
- **Keys longer than the broker's tag field.** Truncated at the broker, unmatchable at
  reconciliation.
- **Terminal states overwritten.** A late status write moving `PLACED` back to `PENDING`, or
  clearing the broker order id, re-arms a live order for re-sending.
- **A failure word read as a refusal.** `{"status": "error", "error_type": "NetworkException"}`
  and a 502 from the broker's edge both filed as `REJECTED`, during precisely the network event
  most likely to have left an order working.
- **"Safe to re-send" that never re-sends.** The router reports absence and hands the caller
  back a status telling it to try again, but nothing releases the ledger claim, so the retry
  reconciles to absence again and loops. The order silently never goes out — the mirror image
  of the duplicate, and harder to spot because nothing is wrong at the broker.

## Production Implementation Reference

- Reference code: `scripts/order_ledger.py` — `OrderLedger`, `IdempotentOrderRouter`,
  `OrderIntentStatus`, `ReconcileOutcome`, `ReconcileResult`, `make_idempotency_key()`,
  `classify_broker_response()`, `BROKER_KEY_MAX_LEN`.
- Automated unit tests: `scripts/test_order_ledger.py` — regression tests are named
  `test_regression_r*` and each one fails against the 1.0.0 implementation.

### Upgrading from 1.0.0

`make_idempotency_key()` now canonicalises its inputs, so **keys derived by 2.0.0 differ from
keys derived by 1.0.0 for the same order**. Do not point a 2.0.0 router at a ledger written by
1.0.0 while orders from that ledger are still in flight; drain or reconcile the old ledger
first. `place_order()` also no longer re-sends on an unresolved intent — callers that relied on
calling it again to retry must now handle `UNRESOLVED_REQUIRES_RECONCILIATION` and
`ABSENT_SAFE_TO_RESEND` explicitly.

`OrderLedger.record_intent()` requires `strategy_id` and `symbol`; they previously defaulted to
`"default"`/`"NIFTY"`, which wrote a row describing an order nobody placed and quietly made the
intent unmatchable by attribute reconciliation.
