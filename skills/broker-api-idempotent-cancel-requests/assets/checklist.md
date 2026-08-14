# Pre-Flight / Sign-off Checklist — broker-api-idempotent-cancel-requests

Use this before considering the skill's implementation complete.

## Acknowledgement vs. completion

- [ ] **2xx is not "cancelled".** Confirm HTTP `200` / `202` / `204` yield `PENDING_CANCEL`,
      and that the caller does **not** free capital, reduce tracked exposure, or place a
      replacement order on that result.
- [ ] **Synchronous-cancel opt-in is deliberate.** If `treat_ack_as_cancelled=True` is set,
      confirm against the broker's own documentation that its cancel endpoint returns the
      order already in a terminal cancelled state.
- [ ] **Terminal confirmation comes from the order-state stream.** Confirm the
      `ExecutionReport` / postback / order-status consumer exists and is what actually
      transitions local order state.

## Indeterminate outcomes

- [ ] **Nothing indeterminate is cached.** Confirm `UNKNOWN` and `ORDER_UNKNOWN` are absent
      from the cache after the call, and that a retry under the same `client_cancel_id`
      genuinely reaches the broker again.
- [ ] **Timeouts are not failures.** Confirm a dropped response yields `UNKNOWN` with
      `requires_reconciliation` true, never a terminal status.
- [ ] **Exhausted 5xx is `UNKNOWN`.** Confirm the classification after the retry budget is
      spent, and that it triggers reconciliation rather than a silent give-up.
- [ ] **Nothing raises into the trading loop.** Confirm transport exceptions and malformed
      transport return values are contained.

## Classification safety

- [ ] **"Not found" is not "cancelled".** Confirm HTTP `404` and "unknown order" text map to
      `ORDER_UNKNOWN`, not `ALREADY_CANCELLED`.
- [ ] **Negations do not match.** Confirm `"order was not filled, cannot cancel"` is not
      classified `FILLED_BEFORE_CANCEL`.
- [ ] **Partial fills are not full fills.** Confirm `"order partially filled"` does not
      produce `FILLED_BEFORE_CANCEL`.
- [ ] **Unrecognised rejections are non-terminal.** Confirm a bare `422` / `403` yields
      `REJECTED` with `is_terminal` false.
- [ ] **Broker patterns reviewed.** Confirm the default text patterns were checked against
      this broker's actual error strings, and overridden where they do not fit.

## Concurrency and dedup

- [ ] **One dispatch per cancel id.** Fire N concurrent cancels of one `client_cancel_id`
      against a slow transport; confirm exactly one broker call.
- [ ] **Check-and-claim is atomic.** Confirm the cache lookup and the in-flight claim happen
      under a single lock, not as two steps.
- [ ] **In-flight slots are released on every path.** Confirm a failed dispatch does not
      leave the cancel id permanently blocked.
- [ ] **Cache is bounded.** Confirm the eviction policy and that an evicted id is safely
      re-dispatched rather than silently dropped.

## Retry and throttling

- [ ] **Backoff is exponential, capped, and jittered.** Confirm the delay sequence and that
      jitter is enabled outside tests.
- [ ] **Only retryable statuses are retried.** Confirm 5xx / `408` / `429` / `418` and
      transport errors retry, and that other 4xx do not.
- [ ] **`Retry-After` is parsed in both RFC 9110 forms.** Confirm delay-seconds and HTTP-date
      inputs both work, and that an unparseable value falls back to the schedule.
- [ ] **Long `Retry-After` returns control.** Confirm a wait exceeding the retry budget
      surfaces `retry_after_s` to the caller instead of sleeping the cancel thread.

## Durability

- [ ] **Restart behaviour is understood.** Confirm the team knows the cache is in-memory and
      per-process, and that cancel ids are persisted with the order intent if cross-restart
      de-duplication is required.
- [ ] **Cancel ids are process-unique.** Confirm the id includes a component that survives a
      counter reset, so a restarted process cannot mint a colliding id.

## Testing

- [ ] Run `python -m unittest discover -s skills/broker-api-idempotent-cancel-requests/scripts`
      and achieve a 100% pass rate.
- [ ] Confirm at least one test would **fail** against the unsafe behaviour it guards
      (cached indeterminate outcome, 2xx-as-cancelled, 404-as-cancelled, concurrent
      duplicate dispatch).

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
