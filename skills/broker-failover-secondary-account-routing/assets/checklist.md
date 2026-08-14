# Go-Live Checklist: Broker Failover & Secondary Account Routing

One pass before enabling failover, and again whenever either broker relationship
changes. Bracketed values are calibrated defaults — replace with figures derived from
your own primary's observed behaviour.

## Secondary account readiness

- [ ] Secondary account funded, entitled, and **exercised with real orders** — fills,
      symbols, order types, and margin treatment all confirmed. A backup never traded
      through is an assumption, not a control.
- [ ] Instrument coverage compared against the primary. Every symbol the strategy can
      trade is tradable in both accounts.
- [ ] Rate limits, supported order types, fee schedule, margin treatment, and settlement
      conventions documented for the backup **separately** — it inherits none of the
      primary's.
- [ ] Per-account margin and day-trading accounting understood for both accounts. The
      backup is a different account at a different firm.
- [ ] Market-data entitlements confirmed for the backup if the strategy prices off it.

## Failure classification (the part that prevents duplicates)

- [ ] Adapters raise `BrokerError` with an explicit `FailureClass` rather than bare
      exceptions.
- [ ] Connect timeout mapped to `UNAVAILABLE`; **read timeout mapped to `AMBIGUOUS`**.
      Confirm your HTTP client distinguishes them.
- [ ] HTTP 5xx mapped to `AMBIGUOUS` — the order may have been accepted.
- [ ] HTTP 429/418 mapped to `RATE_LIMITED`, with `retry_after_s` populated from the
      `Retry-After` header (delay-seconds *or* HTTP-date).
- [ ] Business rejections (bad symbol, buying power, size limits) mapped to `REJECTED`.
- [ ] Verified that an unrecognised exception defaults to `AMBIGUOUS`, not to failover.
- [ ] **Tested**: primary accepts an order then the response is lost. Confirm
      `AmbiguousOrderStateError` and that the secondary received nothing.

## Reconciliation

- [ ] `client_order_id` is stable per order intent and reused across retries of that
      intent.
- [ ] Understood and documented that a client order id does **not** de-duplicate across
      brokers — the secondary has never seen it.
- [ ] `order_status_resolver` implemented and wired, or the operational cost of halting
      each ambiguous order explicitly accepted.
- [ ] **Tested**: a resolver that itself raises leaves the outcome ambiguous rather than
      being read as "order not found".
- [ ] Escalation path defined for `AmbiguousOrderStateError` — who reconciles, against
      which system, within what time.

## Position integrity

- [ ] Every closing or reducing order is submitted with `PositionEffect.REDUCE`.
- [ ] Position cache seeded from each broker's own position report at session start, and
      after any manual intervention.
- [ ] **Tested**: seed a long in the primary, take the primary down, submit a REDUCE
      sell. Confirm `PositionAffinityError` and that no short was opened at the
      secondary.
- [ ] Escalation path defined for `PositionAffinityError`.
- [ ] Post-failover procedure documented: reconcile both accounts before the next
      reducing order, and decide explicitly whether to flatten the secondary or manage
      the position where it sits.
- [ ] For US equities: confirmed that a sale routed to an account holding no position
      would be a short sale requiring a locate (Reg SHO 203(b)(1)) and correct marking
      (200(g)), and that no Rule 200(f) aggregation-unit plan is being assumed without a
      documented one in place.

## Circuit breaker

- [ ] `max_consecutive_failures` calibrated from the primary's observed error rate [3].
- [ ] `recovery_timeout_seconds` set from realistic recovery times [60 s REST].
- [ ] `half_open_max_probes` set deliberately [1] — each probe is a live order against a
      broker believed to be down.
- [ ] `half_open_successes_to_close` set deliberately [1]; raise it if the primary flaps.
- [ ] **Tested**: trip the circuit, wait past the recovery timeout, fire ten concurrent
      orders, confirm exactly one reached the primary.
- [ ] **Tested**: a slow call that started before the trip and returns after it does not
      close the circuit.
- [ ] **Tested**: recovery timing is unaffected by a wall-clock jump.
- [ ] `manual_open` / `manual_reset` exposed to the desk, and `manual_open` confirmed to
      survive the recovery timeout.
- [ ] Confirmed the circuit breaker is **not** being used as a kill switch — opening it
      stops new flow but does not withdraw working orders.

## Risk controls and compliance

- [ ] Pre-trade risk layer sits **above** the router; neither leg can be routed around.
- [ ] Limits enforced in aggregate across both accounts, not per-account.
- [ ] Verified a `REJECTED` order is never re-routed and never counted against broker
      health.
- [ ] For US broker-dealers with market access: 15c3-5(c)(1)(i) credit/capital and
      (c)(1)(ii) erroneous-order controls confirmed active on both paths.
- [ ] For EU-authorised investment firms: the secondary relationship documented as part
      of the business continuity arrangements required by MiFID II RTS 6 Article 14, in a
      durable medium.

## Configuration and operations

- [ ] Symbol mappings registered for every tradable instrument.
- [ ] `strict_symbol_mapping=True` in production. Confirmed an unmapped symbol raises
      `SymbolMappingError` and does **not** fail over.
- [ ] Primary and secondary have distinct broker names.
- [ ] `MockBrokerAdapter` replaced with live adapters implementing `BrokerAdapter`.
- [ ] Alerting wired on `ambiguous_outcomes`, `failovers`, `terminal_rejections`, and
      circuit state transitions.
- [ ] Understood that all router state is in-memory and per-process: it does not survive
      a restart and is not shared across replicas, which trip independently.
- [ ] Failover exercised on a schedule (game day), not only during real incidents.
- [ ] `python -m unittest discover -s skills/broker-failover-secondary-account-routing/scripts`
      passes.
