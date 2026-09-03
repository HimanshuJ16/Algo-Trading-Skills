# Pre-Flight / Sign-off Checklist — order-placement-idempotency

Use this before considering the skill's implementation complete. Every box is a live-capital
control; "probably fine" is a failed check.

## Broker facts (verify per broker, per API version — do not assume)

- [ ] **Client-identifier field named.** The exact field this broker accepts is recorded
      (`tag`, `client_order_id`, `orderTag`, `user_remark`, integer `orderId`), not guessed.
- [ ] **Length and character limits verified.** The derived key fits. Kite Connect's `tag` is
      capped at 20 alphanumeric characters — the 24-character default does **not** fit it.
- [ ] **Echo-back confirmed by observation.** You have placed a test order and seen the key
      come back in the order book. If it does not, `broker_echoes_key=False` is set and the
      reconciler is configured for attribute matching.
- [ ] **Success and rejection response shapes captured from the live/sandbox API**, including
      any interim state (`PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`) and any shape where
      one placement returns several order ids (Kite auto-slicing).

## Ledger

- [ ] **Write-ahead ordering.** `record_intent()` commits the `PENDING` row *before* the
      network call. Verified by asserting the ledger status from inside the broker stub.
- [ ] **Durable storage.** The ledger is a real file with WAL + `synchronous=FULL`, on the
      same host as the bot. Not a dict, not `:memory:`, not an unflushed handle.
- [ ] **Atomic claim.** Duplicate suppression rests on the primary-key insert, not on a
      `SELECT`-then-`INSERT`. Verified with a concurrent test.
- [ ] **State machine enforced.** `PLACED` and `REJECTED` are terminal and raise on any
      further transition; a status write that omits `broker_order_id` preserves it.

## Send path

- [ ] **One broker call per `place_order` invocation**, verified by call-count assertion.
- [ ] **No send while an intent is `PENDING` or `UNKNOWN`** unless reconciliation returned
      `ABSENT`. Verified by the timeout-then-retry regression test.
- [ ] **Conservative classification.** Ambiguous, unparseable, and success-without-order-id
      responses land in `UNKNOWN`, never `REJECTED` and never `PLACED` with a fabricated id.
- [ ] **Gateway faults are not refusals.** `{"status": "error", "error_type":
      "NetworkException"}`, gateway timeouts and every 5xx classify `UNKNOWN`; only an explicit
      rejection status or a documented refusal `error_type` reaches `REJECTED`.
- [ ] **An `ABSENT` verdict releases the ledger claim** (archived, not dropped) so the re-send
      it authorises actually happens. Verified by re-invoking `place_order` after an ABSENT
      reconciliation and asserting exactly one broker send and a settled row.
- [ ] **Rejection reasons classified** before any new order is issued — a retry after a
      rejection is a new order and needs a new key.
- [ ] **Key stability.** `qty=50` and `qty=50.0`, naive and aware datetimes, `"buy"` and
      `"BUY"` all derive the same key. `sequence` is used wherever repeat orders are intended.

## Reconciliation

- [ ] **Tri-state, not boolean.** `ABSENT` and `INCONCLUSIVE` are distinct and handled
      differently; nothing re-sends on `INCONCLUSIVE`.
- [ ] **Absence only counts when the key is echoed.** Confirmed against this broker.
- [ ] **A failed order-book query is `INCONCLUSIVE`**, never absence.
- [ ] **Attribute matching is strict** where used: symbol + side + quantity + price, inside a
      time window, excluding already-linked broker orders, escalating on ambiguity. The
      residual risk is documented for whoever operates this strategy.

## Recovery and operations

- [ ] **Startup sweep runs before the first signal.** `recover_unresolved()` is called on every
      restart and reconnect, and its result gates signal generation.
- [ ] **Unresolved intents block the strategy** and page a human; they are not logged and
      skipped.
- [ ] **`alert_fn` is wired to a real channel**, not left at the default warning log.

## Testing

- [ ] `python -m unittest discover -s skills/order-placement-idempotency/scripts` passes.
- [ ] **Timeout drill in sandbox/paper:** response dropped *after* the broker accepted the
      order; the bot reconciles and places no duplicate.
- [ ] **Crash drill:** process killed between intent write and response; after restart exactly
      one order exists at the broker.
- [ ] **Phantom-match drill:** the reconciler is shown an order book containing an older,
      identical order and does **not** adopt it.

## Sign-off

- Broker and API version tested: ___________________________
- Reviewed by: ___________________________
- Date: ___________________________
- Environment tested (paper/sandbox/live): ___________________________
