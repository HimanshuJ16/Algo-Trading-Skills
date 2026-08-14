# Deep Workflow Reference — broker-api-idempotent-cancel-requests

This file holds the full technical procedure referenced by `SKILL.md`.

## Governing principle

A cancel dispatch produces one of three kinds of answer, and they must not be collapsed:

| Kind | Meaning | Caller action |
|---|---|---|
| **Terminal** | The broker asserted the order is no longer working | Act on it |
| **Acknowledged** | The cancel request was accepted; the order is pending cancel | Wait for the order-state stream |
| **Indeterminate** | Nobody knows whether the cancel landed | Reconcile, then re-dispatch the same id |

Only the first justifies releasing capital, reducing tracked exposure, or placing a
replacement order.

## Full Procedure

### 1. Mint a stable cancel id

One id per cancel *intent*. Every network attempt for that intent reuses it; that reuse is
what makes the retry idempotent.

    CANCEL_{order_id}_{process_token}_{seq}_{epoch_ms}

The `process_token` matters: a per-process sequence counter restarts at 1 after a crash, so
two processes (or one process before and after a restart) can otherwise mint the same id for
different intents. The id is opaque — do not parse it positionally from the left, since order
ids frequently contain the separator.

**Durability limit.** The dedup cache lives in process memory. It does not survive a restart
and is not shared across replicas or hosts. If cancels must be de-duplicated across a restart,
persist the cancel id alongside the order intent in a durable ledger — that pattern belongs to
`order-placement-idempotency`.

### 2. Claim the id under one lock

    with lock:
        cached = history.get(cid)          # already answered?
        if cached: return replay(cached)
        if cid in in_flight: wait_outside_lock()
        in_flight[cid] = Event()           # claim

Checking the cache and dispatching as two separate steps is a check-then-act race: under
concurrency, N threads all miss the cache (nothing is written until the broker answers) and
all dispatch. That is a self-inflicted cancel storm, and the consequences are not merely
cosmetic — Binance escalates from HTTP `429` to a `418` IP auto-ban for continuing to send
after rate-limit responses, at which point the bot cannot cancel anything at all.

A duplicate concurrent caller waits on the in-flight event and returns the first caller's
result. If the wait times out, it returns `UNKNOWN` rather than dispatching its own copy.

### 3. Dispatch with a bounded, jittered retry

**Retryable:** any 5xx, `408 Request Timeout`, `429 Too Many Requests`, `418` (Binance IP
ban), and any transport exception.

**Not retryable:** other 4xx. The broker made a decision; re-sending will not change it.

**Backoff:** `min(base * 2**attempt, cap)`, reduced by a random fraction (`jitter_ratio`).
Jitter is down-only so the cap stays meaningful. Without it, a fleet that all timed out during
the same broker outage re-dispatches in lockstep and re-creates the outage's load spike.

**`Retry-After`:** RFC 9110 Section 10.2.3 permits *either* delay-seconds (`Retry-After: 120`)
*or* an HTTP-date (`Retry-After: Wed, 21 Oct 2026 07:28:00 GMT`). Parse both; assuming seconds
is the standard bug. Binance sends it on both `429` and `418`, in the latter case covering the
remaining ban duration.

**Cap the honoured `Retry-After`.** If the broker asks for longer than the retry budget, do
not sleep — return `UNKNOWN` with `retry_after_s` populated and let the caller decide. A
cancel is risk-reducing; parking that thread for ten minutes because a header suggested so is
worse than surfacing the number to a human or a supervisor.

**Reset per attempt.** Clear the status, payload, and error buffers at the top of each
iteration. Carrying a previous attempt's response body forward means the final classification
runs against a stale error string — e.g. an attempt-1 `502 {"error": "bad gateway"}` followed
by an attempt-2 connection reset gets reported as a gateway error rather than a lost
connection.

### 4. Classify, resolving ambiguity toward "reconcile"

The two misclassification directions are not symmetric:

- **False terminal** ("the order is dead" when it is live): the caller stops retrying, stops
  tracking the exposure, and may place a replacement. Working size sits in the market
  unmanaged. Cost: unbounded.
- **False indeterminate** ("uncertain" when the order is dead): the caller issues one
  order-status query. Cost: one API call.

Every ambiguous case therefore resolves toward `UNKNOWN` / `ORDER_UNKNOWN` / `REJECTED`.

| Response | Status | Terminal? | Notes |
|---|---|---|---|
| 2xx | `PENDING_CANCEL` | No | FIX `OrdStatus` 6. The order can still fill. |
| 2xx, `treat_ack_as_cancelled=True` | `CANCELLED` | Yes | Only for synchronous-cancel brokers |
| 4xx + "too late to cancel" / "already filled" | `FILLED_BEFORE_CANCEL` | Yes | FIX `CxlRejReason` 0 |
| 4xx + "already cancelled" | `ALREADY_CANCELLED` | Yes | Positive assertion required |
| 404, or "unknown order" / "no such order" | `ORDER_UNKNOWN` | No | Also fires on wrong key/symbol/id |
| Other 4xx (incl. bare `422`) | `REJECTED` | No | Order presumed still working |
| Exhausted 5xx / `429` / transport error | `UNKNOWN` | No | May have succeeded |

#### Why "not found" is not "cancelled"

Binance returns `-2011` (`CANCEL_REJECTED`) and `-2013` (`NO_SUCH_ORDER`, "Order does not
exist.") when the order is genuinely gone — and equally when the request carries the wrong API
key, the wrong symbol, or a mistyped order id. In those cases the order is still live and
working. A bare HTTP `404` is worse still: it is also what a misconfigured base URL or a
version-migrated endpoint path returns. `ORDER_UNKNOWN` preserves the signal without making
the unsafe leap.

#### Why "partially filled" is not "filled"

A partial fill does not close the order — the unfilled remainder is normally still cancellable,
and most brokers simply cancel it and return 2xx. If a broker instead *rejects* citing a
partial fill, classifying that as `FILLED_BEFORE_CANCEL` tells the caller the full quantity
executed, which corrupts position and P&L accounting. It routes to `REJECTED` instead.

#### Why substring matching is not enough

`"filled" in detail` matches:

- `"order was not filled, cannot cancel"` → false `FILLED_BEFORE_CANCEL`
- `"order partially filled"` → overstated fill quantity
- `"unfilled quantity is zero"` → false `FILLED_BEFORE_CANCEL`

Use word-boundary regexes with negation guards, and keep the pattern set per-broker
overridable. A broker exposing a machine-readable reason code should be classified on the
code, not the prose.

### 5. Cache only asserted outcomes

| Status | Cached? | Why |
|---|---|---|
| `CANCELLED`, `FILLED_BEFORE_CANCEL`, `ALREADY_CANCELLED` | Yes | Broker asserted a final state |
| `PENDING_CANCEL` | Yes | Broker accepted the request; re-sending is a duplicate cancel |
| `REJECTED` | Yes | Broker made a decision; the same id will get the same answer |
| `ORDER_UNKNOWN`, `UNKNOWN` | **No** | Indeterminate — must stay re-dispatchable |

Binance's REST documentation states the rule for 5xx directly: "It is important to **NOT**
treat this as a failure operation; the execution status is **UNKNOWN** and could have been a
success."

Caching an indeterminate outcome as terminal is the defect that makes this whole pattern
dangerous. The failure sequence:

1. Cancel dispatched; the response is lost to a timeout.
2. Manager classifies the attempt as a terminal failure and writes it to the cache.
3. The supervisor retries with the same `client_cancel_id` — correctly, since that is what
   idempotent retry means.
4. The manager returns the cached failure. The broker is never contacted again.
5. The order is still working, the operator sees a definite-looking failure, and nothing in
   the system will ever try to cancel it again.

Eviction is insertion-ordered (oldest first) and bounded. An evicted id is simply re-dispatched
if it is used again — safe, because the broker's own cancel handling is idempotent for a
repeated cancel of an already-cancelled order.

### 6. Reconcile before acting

For any non-terminal status, read the order's true state before adjusting exposure:

- FIX: the `ExecutionReport` (35=8) stream. `OrdStatus` 4 = Canceled is the confirmation;
  6 = Pending Cancel is not.
- Alpaca: the trade-updates stream or `GET /v2/orders/{id}`; `pending_cancel` is not terminal.
- Kite Connect: postbacks (verify the SHA-256 checksum) or `GET /orders/{order_id}`.

Then:

- After `UNKNOWN` — re-dispatch the **same** cancel id. It was never cached, so it will reach
  the broker.
- After `ORDER_UNKNOWN` — reconcile first. If the order is live, the request was malformed
  (wrong id/symbol/credentials); fix it and dispatch under a **new** id.
- After `REJECTED` — diagnose the reason. Retrying an identical rejected request is a loop;
  dispatch under a **new** id only once the cause is addressed.
- After `PENDING_CANCEL` — wait for the order-state stream. Do not re-dispatch; that is a
  duplicate cancel against an order already in pending-cancel, which FIX `CxlRejReason` 3
  ("Order already in Pending Cancel or Pending Replace status") exists to reject.

## Scope limits

- This module classifies **one dispatch**. It does not reconcile, does not hold order state,
  and does not own a durable ledger.
- It never raises into the caller: transport failures, malformed transport return values, and
  programming errors inside the transport all surface as `UNKNOWN`. It runs on the same thread
  as live risk reduction, where an exception would be worse than an uncertain answer.
- Error-text classification is a heuristic over free-text broker strings, tuned conservatively
  and constructor-overridable per broker.

## Production Implementation Reference

- Reference code: `scripts/cancel_manager.py` (`IdempotentCancelManager`, `CancelStatus`,
  `CancelResult`).
- Automated unit tests: `scripts/test_cancel_manager.py`.
