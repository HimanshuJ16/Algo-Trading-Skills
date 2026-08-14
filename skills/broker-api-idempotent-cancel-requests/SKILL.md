---
name: broker-api-idempotent-cancel-requests
description: Use when cancelling live orders to de-duplicate cancel retries, classify
  Cancel-vs-Fill race responses, and distinguish a cancel the broker *acknowledged*
  from one it *completed* — so a timeout, 5xx, 429, or "order not found" is never
  mistaken for proof that a working order is dead.
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- idempotency
- order-cancellation
- race-condition
- cancel-vs-fill
- resilience
- concurrency
brokers_frameworks:
- FIX 4.4 (OrderCancelRequest 35=F / OrdStatus 39)
- Alpaca Trading API
- Binance Spot REST API
- Zerodha Kite Connect v3
- Python Trading Engine
version: "3.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this skill when a bot issues order cancel requests over an unreliable network, from
concurrent threads, or in a retry loop. Cancellation carries race conditions that order
placement does not: the matching engine may fill the order microseconds before the cancel
arrives, and a lost response leaves the client unable to tell whether the cancel landed.

The skill exists to enforce one distinction that most cancel code gets wrong:

> **A cancel request is a request, not an outcome.**

Every broker consulted says so in its own words. FIX 4.4 defines `OrdStatus` (tag 39) value
`6` — "Pending Cancel (e.g. result of Order Cancel Request `<F>`)" — as a state *distinct*
from `4` = "Canceled"; only a subsequent `ExecutionReport` (35=8) moves the order to `4`.
Alpaca's `pending_cancel` status is documented as "The order is waiting to be canceled",
and its docs state orders "will remain in `pending_cancel` until canceled by the execution
venue that Alpaca routed the order to for execution" — the `DELETE /v2/orders/{id}` returns
`204` long before that. Zerodha Kite Connect states plainly that "Successful placement of an
order via the API does not imply its successful execution" and directs clients to postbacks
for the actual transition.

So an HTTP 2xx on a cancel means **pending cancel**, and the order can still fill. Code that
frees capital, decrements exposure, or fires a replacement order on that 2xx is acting on an
order that is still working.

## When NOT to Use

- **As confirmation that an order is dead.** This classifies one cancel dispatch. Only the
  broker's order-state stream — `ExecutionReport`, postback, or an order-status query —
  settles the outcome. Use `webhook-based-order-fill-notifications` for that stream.
- **As a cross-restart idempotency guarantee.** The dedup cache is in-memory and
  per-process. It does not survive a restart and is not shared across replicas. Durable
  cancel dedup needs an intent ledger — see `order-placement-idempotency`.
- **As a substitute for a kill switch.** Cancelling orders one id at a time through a
  retrying client is not a flatten-everything control. Use
  `kill-switch-and-drawdown-circuit-breakers` and the broker's mass-cancel endpoint.
- **As a rate-limit strategy.** It backs off and honours `Retry-After`, but sizing cancel
  traffic against a broker's quota belongs to `multi-broker-rate-limit-handling` and
  `order-to-trade-ratio-fee-penalty-avoidance`.

## Prerequisites

- The broker's order id or `ClOrdID` for the order being cancelled.
- A cancel id (`client_cancel_id`) that is stable across retries of the *same* cancel
  intent — reusing it is what makes the retry idempotent.
- Access to the broker's order-state stream or order-status endpoint, for the
  reconciliation step. Without it, every non-terminal outcome below is a dead end.
- Documented cancel semantics for your specific broker: which HTTP codes it returns, and
  whether its cancel endpoint is asynchronous (Alpaca, Kite) or synchronous (Binance
  `DELETE /api/v3/order` returns the order already in state `CANCELED`).

## Workflow

1. **Mint a stable cancel id.** One id per cancel *intent*, not per network attempt. Retries
   of the same intent reuse it; a genuinely new cancel decision gets a new one. Include a
   process-unique component — a per-process counter alone collides across restarts.

2. **Claim the id before dispatching.** Check the dedup cache and the in-flight set under a
   single lock. Checking the cache and dispatching as two separate steps lets two threads
   both miss and both hit the broker — Binance auto-bans an IP with HTTP `418` for
   continuing to send after `429`s, so a cancel storm can cost you the ability to cancel at
   all. A duplicate concurrent caller waits on the first dispatch; it does not add to it.

3. **Dispatch with a bounded, jittered retry.** Retry on 5xx, `408`, `429`, and transport
   errors. Honour `Retry-After` when present — RFC 9110 allows either delay-seconds *or* an
   HTTP-date, so parse both. Cap it: if the broker asks for longer than your retry budget,
   stop and hand the number back to the caller rather than sleeping a risk-reducing cancel
   thread through a ban. Reset the response buffer each attempt, or the final
   classification runs against a stale earlier body.

4. **Classify — and let ambiguity resolve toward "reconcile".** The two error directions are
   not symmetric. Reporting a live order as dead stops the caller from ever retrying and
   strands working exposure; reporting a dead order as uncertain costs one status query.

   | Broker response | Status | Order state |
   |---|---|---|
   | 2xx | `PENDING_CANCEL` | **Still working** — can fill until an execution report says otherwise |
   | 2xx, synchronous-cancel broker (opt-in) | `CANCELLED` | Dead |
   | 4xx "too late to cancel" / "already filled" | `FILLED_BEFORE_CANCEL` | Dead — it filled |
   | 4xx "already cancelled" | `ALREADY_CANCELLED` | Dead |
   | 404, or "unknown order" / "no such order" | `ORDER_UNKNOWN` | **Unknown** — reconcile |
   | Other 4xx (incl. bare Alpaca `422`) | `REJECTED` | Presumed still working — reconcile |
   | Exhausted 5xx / `429` / transport error | `UNKNOWN` | **Unknown** — reconcile |

   Two classifications deserve their reasoning spelled out, because the obvious shortcut is
   wrong in both:

   - **"Order not found" is not proof of cancellation.** Binance returns `-2011`
     (`CANCEL_REJECTED`) and `-2013` (`NO_SUCH_ORDER`, "Order does not exist.") for an order
     that is genuinely gone *and* for a request carrying the wrong API key, symbol, or order
     id — where the order is still live and working. Treat it as `ORDER_UNKNOWN` and query
     the order book.
   - **"Partially filled" is not "filled".** A partial fill does not close the order; the
     remainder is usually still cancellable. Classifying it as `FILLED_BEFORE_CANCEL`
     overstates the executed quantity and corrupts position accounting.

   Match error text with word boundaries and negation guards, not bare substrings — `"filled"
   in detail` matches "order was **not filled**, cannot cancel" and hands the caller a false
   fill.

5. **Cache only what the broker asserted.** `PENDING_CANCEL`, the three terminal statuses,
   and `REJECTED` are cacheable: the broker answered, so re-sending the same id is a
   duplicate. `UNKNOWN` and `ORDER_UNKNOWN` must **not** be cached. Binance's REST
   documentation is explicit for the 5xx case: "It is important to **NOT** treat this as a
   failure operation; the execution status is **UNKNOWN** and could have been a success."
   Caching an indeterminate outcome as terminal makes one lost response permanent — every
   later retry under that cancel id replays the cached failure and never reaches the broker,
   leaving a live order that can no longer be cancelled.

6. **Reconcile before acting.** Anything that is not terminal means: read the order's state
   from the broker before adjusting exposure, freeing capital, or re-cancelling. Re-dispatch
   the *same* cancel id after an `UNKNOWN`; use a *new* one after a `REJECTED` you have
   diagnosed and fixed.

> Full procedure and per-broker response tables: see `references/workflows.md`.
> Standards, FIX mappings, and cited sources: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating an HTTP 2xx as a completed cancellation.** It is an acknowledgement. FIX models
  this explicitly as `OrdStatus` 6 (Pending Cancel) preceding 4 (Canceled), and Alpaca orders
  sit in `pending_cancel` until the execution venue confirms. The order can still fill.
- **Caching an indeterminate outcome under the cancel id.** The single most damaging bug in
  this pattern: a timeout or exhausted 5xx stored as a terminal failure means every
  subsequent retry of that cancel id short-circuits to the cache, and the order is never
  cancelled again.
- **Concluding "order not found" means "order cancelled".** The same broker error covers a
  wrong id, wrong symbol, and wrong API key, all of which leave the order live.
- **Substring matching on broker error text.** `"filled" in detail` fires on "not filled"
  and on "partially filled". Use anchored patterns with negation guards.
- **Retrying a cancel on a hot loop without deduplication or jitter.** Cancel storms trip
  broker throttles and order-to-trade-ratio penalties; Binance escalates from `429` to a
  `418` IP ban, at which point you cannot cancel anything.
- **Checking the dedup cache and dispatching as two separate steps.** Under concurrency both
  threads miss and both dispatch. The check and the claim must happen under one lock.
- **Sleeping through a long `Retry-After` on a cancel path.** A cancel reduces risk; blocking
  it for ten minutes because the broker suggested so is worse than returning control and
  letting the desk decide.
- **Assuming in-memory idempotency survives a restart.** After a process restart the cache is
  empty and the sequence counter has reset. Persist the cancel id with the order intent.

## Verification

- Run `python -m unittest discover -s skills/broker-api-idempotent-cancel-requests/scripts`
  and confirm all tests pass.
- Confirm an HTTP `200`, `202`, and `204` each yield `PENDING_CANCEL` with
  `requires_reconciliation` true, and that `treat_ack_as_cancelled=True` is the only way to
  get `CANCELLED`.
- Drop the response on the first attempt, then retry under the same `client_cancel_id`, and
  confirm the retry **actually reaches the broker** — a cached indeterminate result would
  silently skip it.
- Feed `{"detail": "order was not filled, cannot cancel"}` and `{"detail": "order partially
  filled"}` and confirm neither is classified `FILLED_BEFORE_CANCEL`.
- Feed a `404` "Order not found" and confirm `ORDER_UNKNOWN`, not `ALREADY_CANCELLED`.
- Feed a bare Alpaca-style `422` "The order status is not cancelable." and confirm `REJECTED`
  with `is_terminal` false.
- Fire eight concurrent cancels of one `client_cancel_id` against a slow transport and
  confirm exactly one broker dispatch and one non-replayed result.
- Return `429` with `Retry-After: 600` against a 5 s retry budget and confirm no sleep
  occurs, the call returns `UNKNOWN`, and `retry_after_s` is surfaced to the caller.

## Related Skills

- `order-placement-idempotency`
- `webhook-based-order-fill-notifications`
- `broker-agnostic-adapter-interface`
- `multi-broker-rate-limit-handling`
- `broker-side-order-throttle-detection`
- `order-to-trade-ratio-fee-penalty-avoidance`
