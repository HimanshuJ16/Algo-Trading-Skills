---
name: order-placement-idempotency
description: Use whenever a bot places, modifies, or cancels live orders and must
  guarantee it never double-executes an order due to retries, timeouts, or reconnects
domain: algorithmic-trading
subdomain: broker-integration
tags:
- broker-integration
- fyers-api-v3
- zerodha-kite-connect
- icici-breeze-api
brokers_frameworks:
- Fyers API v3
- Zerodha Kite Connect
- ICICI Breeze API
- Upstox API v2
- Alpaca Trading API
- IBKR API
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Invoke this any time order-placement code includes retry logic, runs over an unreliable
network, or could be re-triggered by a reconnect/restart. A naive retry-on-timeout ("call
failed, try again") is the single most common cause of accidental duplicate live orders,
because a timeout does not mean the order failed — it may mean the order succeeded and only
the response was lost.

The premise worth stating plainly: **not one broker in this skill's coverage table documents
an idempotency guarantee for order placement.** Kite Connect, Upstox and Alpaca all document
a client-supplied tag field, and none of the three documents duplicate suppression on it
(see `references/standards.md` for what each one actually says). So the client-side tag is
not a safety mechanism supplied by the broker — it is a *correlation handle* that lets you
answer one question after a lost response:

> **Did the order I cannot see a response for actually land?**

Everything in this skill exists to keep that question answerable. The order becomes
idempotent because *you* refuse to send it twice, not because the broker refuses to accept
it twice.

This is also the pre-trade duplicate control that SEC Rule 15c3-5(c)(1)(ii) expects a
broker-dealer with market access to have: controls reasonably designed to "[p]revent the
entry of erroneous orders, by rejecting orders that exceed appropriate price or size
parameters, on an order-by-order basis or over a short period of time, **or that indicate
duplicative orders**."

## When NOT to Use

- **For cancels and modifies.** Cancellation carries a race placement does not — the order
  can fill microseconds before the cancel lands, and a 2xx on a cancel means *pending*
  cancel, not cancelled. Use `broker-api-idempotent-cancel-requests`.
- **As a fill or position tracker.** `PLACED` means the broker acknowledged the order, not
  that it filled, and a working order can still be rejected, cancelled, or partially filled
  afterwards. The ledger here tracks *placement intent*; the order-state stream tracks
  outcomes — see `webhook-based-order-fill-notifications` and
  `zerodha-kite-postback-webhook-verification`.
- **As the exchange-mandated algo identifier.** The unique algo ID SEBI's 4 Feb 2025
  circular requires on Indian algo orders identifies the *algorithm*, not the individual
  order, and is issued by the exchange rather than derived by you. It is a separate field
  with separate rules — see `india-sebi-algo-trading-tagging-requirements`. Do not put an
  idempotency key where the algo ID belongs.
- **As a substitute for a risk control.** Preventing one duplicate does not bound exposure,
  and a ledger that has correctly recorded 400 distinct orders has still let 400 orders
  through. Pair it with `kill-switch-and-drawdown-circuit-breakers`.
- **On IBKR's TWS API as described here.** IBKR does not take a free-form client string.
  `orderId` is a client-assigned **integer** seeded from the `nextValidId` callback and must
  increase; the broker-assigned `permId` is the identifier that is unique across sessions.
  The workflow below still applies — the *key* becomes an integer you allocate and store in
  the ledger, and reconciliation matches on `permId`.

## Prerequisites

- A broker field that carries a client-supplied identifier, **and its verified constraints**:
  Kite Connect caps `tag` at 20 alphanumeric characters, so this skill's 24-character default
  key does not fit it. Check the length limit before deriving keys — see
  `references/standards.md`.
- **Knowledge of whether the broker echoes that field back in its order book.** This single
  fact decides whether "not in the order book" is evidence of absence or means nothing. It
  is the `broker_echoes_key` flag in `scripts/order_ledger.py`.
- A durable local order-intent ledger — a DB file or WAL, not a dict. It must live in the
  same failure domain as the bot and be fsync'd, or the crash it exists to survive takes it
  with the process.
- An order book / order-status endpoint you can query independently of the placement call.
- Canonical inputs for key derivation: a stable `strategy_id`, one representation of the
  signal timestamp, and one numeric type per field. `qty=50` and `qty=50.0` must not derive
  two keys for one order.

## Workflow

1. **Derive a stable idempotency key.** Hash the order identity — strategy, symbol, side,
   signal timestamp, quantity, price — after canonicalising it, and truncate to the broker's
   tag length. `make_idempotency_key()` upper-cases symbol/side, renders numbers at fixed
   precision and normalises datetimes to UTC, so a restart that passes an `int` where the hot
   path passed a `float` still derives the same key.
   - **Decision point — two legitimately identical orders.** Child slices of one parent at
     one signal timestamp collapse onto a single key and the second is silently suppressed as
     a duplicate. Pass an explicit `sequence` discriminator when repeat orders are intended.
     Silent suppression of a wanted order is as expensive as a duplicate.

2. **Claim the intent before the network call.** Insert a `PENDING` row keyed by the
   idempotency key *before* sending. The primary-key insert is the claim: it either succeeds
   (this caller owns the send) or raises an integrity error (someone else owns it). That is
   what makes the sequence safe across threads and across processes sharing the ledger — a
   check-then-act on a `SELECT` is not.

3. **Send once.** Attach the key to the broker's client-tag field. One `place_order` call
   issues at most one broker call, ever.

4. **Classify the response into three outcomes, and be stingy with the terminal two.**
   - **Confirmed success** — broker returned an order id → `PLACED`, store the id.
   - **Confirmed rejection** — broker explicitly refused → `REJECTED`, store the reason.
   - **Everything else** → `UNKNOWN`.
   - **Decision point — "not a success" is not a rejection.** `{"status": "success", "data":
     {"order_id": …}}` is Kite Connect's actual acknowledgement body; code that only accepts
     `{"status": "SUCCESS"}` files it as a rejection, and the ledger then says no order exists
     while one is working. The next signal duplicates it. Interim states such as `PUT ORDER
     REQ RECEIVED` or `VALIDATION PENDING` are likewise not rejections. Anything the broker
     did not state unambiguously is `UNKNOWN`.
   - **Decision point — a success with no order id is not a success.** An acknowledgement you
     cannot reconcile later is an order you have lost track of. Mark it `UNKNOWN`; never
     fabricate a placeholder id.
   - **Decision point — classify the rejection before deciding whether to retry.** Retrying a
     margin rejection after a top-up is legitimate; retrying an invalid-symbol rejection is
     not. A retry after a rejection is a *new* order and needs a *new* key.

5. **Reconcile `UNKNOWN` against the broker order book — as a tri-state, not a boolean.**
   - **Found** → link the broker order id; the intent is settled.
   - **Absent** → safe to re-send under the same key.
   - **Inconclusive** → park it and alert. Do not re-send.
   - **Decision point — absence is only evidence when the broker echoes your key.** If the
     broker returns your tag in its order book, a miss means the order is not there. If it
     does not (ICICI Breeze's `user_remark` is reported not to survive into responses), a miss
     means nothing at all, and treating it as absence re-sends a live order.
   - **Decision point — a failed order-book query is `INCONCLUSIVE`, not absence.** The most
     likely reason you cannot reach the order book is the same outage that ate your response.

6. **Fall back to attribute matching only when the broker cannot echo the key — and keep it
   strict.** Match on symbol, side, quantity *and* price, restricted to a time window around
   the intent, excluding any broker order already linked to another ledger row, and refuse to
   choose when more than one candidate qualifies. Matching on `(symbol, side, quantity)` alone
   will happily adopt an earlier identical order and report a position the account does not
   hold. Document the residual risk: this is strictly weaker than a broker-echoed key.

7. **Sweep every unresolved intent at startup, before generating a single new signal.**
   `recover_unresolved()` reconciles all `PENDING`/`UNKNOWN` rows. A bot that resumes signal
   generation with an in-flight order from before the crash doubles the position, and the
   ledger entry that would have caught it is sitting one query away.
   - **Decision point — an intent that stays unresolved blocks the strategy.** It is not a
     warning to log and move past. Either a human settles it against the broker's order book,
     or the strategy stays down.

> Full step-by-step procedure with broker-specific detail: see `references/workflows.md`.
> Broker/framework coverage table and regulatory citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Retrying on any exception without distinguishing "confirmed rejected" from "network
  timeout, unknown outcome".** The core bug this skill prevents. A timeout is not a failure;
  it is an absence of information.
- **Re-sending an order whose earlier intent is still `PENDING` or `UNKNOWN`.** The subtler
  form of the same bug: the first call handles the timeout correctly, and then the caller's
  outer retry loop calls `place_order` again with identical arguments and the guard only
  checks for `PLACED`. An unresolved intent must block the send, not fall through it.
- **Treating any response that is not the exact success shape as a rejection.** Kite's
  success body is `{"status": "success", "data": {"order_id": …}}`; a strict equality check
  against `"SUCCESS"` records a live order as rejected.
- **Deriving a key longer than the broker's tag field.** Kite Connect documents `tag` as
  "alphanumeric, max 20 chars". A 24-character key is either rejected or silently truncated,
  and a truncated key is a key you cannot match on.
- **Assuming the broker echoes the tag back.** ICICI Breeze accepts `user_remark`, but the
  official SDK repository carries an open, unanswered issue reporting that the field is not
  preserved in responses. A reconciliation strategy built on reading it back never fires.
- **Reconciling on `(symbol, side, quantity)` alone.** For any strategy that scales into a
  position, yesterday's identical order matches today's intent, the ledger records a phantom
  fill, and the real order is never sent.
- **Type-unstable key derivation.** `qty=50` on the hot path and `qty=50.0` after a restart
  hash to different keys, so the restart re-sends the order the ledger was holding.
- **Racing on the check-then-act.** Two threads (or two replicas) both read "no existing
  intent", both insert, both send. The uniqueness constraint has to be the claim, not a prior
  `SELECT`.
- **One placement request, several broker orders.** Kite's auto-slicing returns `data` as an
  *array* of order ids for a single request. A ledger row that stores only the first id
  leaves the rest untracked.
- **Assuming idempotency keys are unique forever.** Alpaca's `client_order_id` is documented
  at 128 characters, and its uniqueness is reported to be enforced against *open* orders
  rather than for all time; IBKR's `orderId` is explicitly reusable across days. Scope key
  reuse to what the broker actually guarantees.
- **Keeping the ledger in memory.** A dict does not survive the crash the ledger exists to
  survive. So does not an unflushed file.

## Verification

- Run `python -m unittest discover -s skills/order-placement-idempotency/scripts`. The suite
  asserts the invariants directly: at most one broker call per `place_order`, no re-send
  while an intent is unresolved, indeterminate responses classified `UNKNOWN`, absence
  concluded only when the key is echoed, and eight concurrent calls on one key producing one
  send.
- Inject an artificial timeout (mock the HTTP client to drop the response *after* the broker
  has processed the request, using a broker sandbox/paper environment) and confirm the bot
  reconciles, finds the order, and does not place a duplicate.
- Kill the process between the intent write and the confirmed response, restart, and confirm
  `recover_unresolved()` links the existing broker order and that exactly one order exists on
  the broker side.
- Point the reconciler at an order book containing an *older, identical* order and confirm it
  is not adopted — this is the failure mode that produces a phantom position rather than a
  duplicate one.
- Audit the ledger after a multi-day live run: every `PLACED` row has a broker order id, no
  broker order lacks a matching ledger row, and no row sat in `UNKNOWN` past a session
  boundary without an operator record of how it was settled.

## Related Skills

- `broker-api-idempotent-cancel-requests`
- `kill-switch-and-drawdown-circuit-breakers`
- `webhook-based-order-fill-notifications`
- `india-sebi-algo-trading-tagging-requirements`
- `sec-rule-15c3-5-risk-controls-us`
- `multi-broker-rate-limit-handling`
- `paper-to-live-promotion-checklist`
