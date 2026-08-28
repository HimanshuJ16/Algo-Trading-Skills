# Workflows for Smart Order Router Failover on Venue Outage

The engine in `scripts/smart_order_router_failover_on_venue_outage.py` implements
steps 2-5. Steps 1, 6 and 7 are yours: the engine has no market data feed, no
order gateway, and no view of your positions.

## 1. Feed the engine before you trust it

`route_order()` decides using only what is stored on each `TradingVenue`. Two
independent inputs must be kept current or the routing decision is fiction:

- **Quotes.** Call `update_quote(venue_id, bid, ask, qty)` on every top-of-book
  change. It stamps `quote_monotonic_ts` from `time.monotonic()` for you. If you
  mutate `TradingVenue` fields directly you must stamp the timestamp yourself,
  from the same clock.
- **Health.** Call `report_venue_error(venue_id, msg)` on every FIX session-level
  reject, gateway timeout, transport disconnect, or HTTP 5xx; call
  `report_venue_success(venue_id)` on every fill, ack, or heartbeat.

Both raise `KeyError` for an unregistered venue id. That is deliberate: a typo'd
venue id that silently did nothing would disable outage detection for that venue
for the life of the process.

## 2. Health accounting and the breaker trip

`report_venue_error` increments `consecutive_error_count`:

- Below `max_error_threshold` → `DEGRADED`. Still routable. A single timeout is
  not evidence of an outage, and excluding on it would forfeit price improvement
  for noise.
- At or above the threshold → `CIRCUIT_BROKEN_OUTAGE`, `circuit_opened_monotonic`
  stamped, `consecutive_trips` incremented.

**Classify the error before reporting it.** A venue-side outage and a rejected
order are not the same event. An order rejected for insufficient buying power, a
bad symbol, a locked/crossed limit, or a failed risk check says nothing about
venue health — feeding those into the breaker trips healthy venues and routes
you away from your best liquidity for a bug in your own order construction.
Report only transport and gateway faults.

## 3. Self-diagnosis before declaring a venue dead

`diagnose_suspected_local_fault()` returns True once at least half the registered
venues (and at least two) are simultaneously unavailable. This is the check the
Regulation NMS adopting release attaches to the Rule 611(b)(1) self-help
exception: "An electing trading center must also assess ... whether the cause of
a problem lies with its own systems."

The flag is surfaced on every `SORRoutingResult` and on `NoEligibleVenueError`.
It is a signal for a human, not an automatic action — the engine does not stop
routing on it, because in a genuine market-wide event routing must continue.

**When it fires, check the local causes first**, in this order, because they are
far more probable than a simultaneous multi-venue outage: local NIC/link state,
DNS, expired or revoked session credentials, clock skew breaking FIX sequence
validation, a full disk stalling the logger, and an egress firewall change.

## 4. Eligibility filtering

Each venue is excluded, with a recorded reason, if any of these holds. Every
reason lands in `SORRoutingResult.excluded_venues`:

| Reason | Trigger |
|---|---|
| `CIRCUIT_BROKEN_OUTAGE` | Breaker open. |
| `INVALID_QUOTE` | Side-relevant price is non-finite or ≤ 0. Catches the never-quoted venue, whose price fields default to `0.0`, and a book wiped on reconnect. |
| `NO_LIQUIDITY` | `available_qty` non-finite or ≤ 0. |
| `STALE_QUOTE` | Quote older than `max_quote_age_seconds`. |
| `QUOTE_TIMESTAMP_MISSING` | Only when `require_quote_timestamp=True`. |
| `QUOTE_TIMESTAMP_IN_FUTURE` | Negative quote age. `time.monotonic()` never runs backwards, so this means the stamp came from another clock — nearly always `time.time()`. Unflagged it would read as permanently fresh and silently disable staleness checking for the life of the process. |

If no venue survives, `NoEligibleVenueError` (a `RuntimeError` subclass) is
raised carrying the full reason map. It never returns a degraded "best guess."

## 5. Ranking among eligible venues

Sort key, in order: **(probe-last, price, health, latency)**.

- **`RECOVERY_PROBE` venues rank last regardless of price.** A venue that just
  failed is the one most likely to be quoting a price it can no longer honour.
  Letting a stale-but-attractive quote from a recovering venue win reintroduces
  exactly the failure this skill exists to prevent. Ranked last still means
  *selectable*: if every other venue is excluded, the order goes to the
  recovering venue rather than being rejected. That is deliberate — attempting
  the one venue that might work beats refusing a live parent order — but it does
  mean a probe can carry real size. Cap the order size, or drive recovery from a
  heartbeat, if that is not acceptable.
- **Among everything else, price leads and health only breaks ties.** A
  `DEGRADED` venue quoting better is the better execution; skipping it for a
  `HEALTHY` venue at a worse price is a trade-through taken to avoid a venue with
  one timeout. At equal price, prefer `HEALTHY`, then lower latency.

`preferred_venue_id` overrides the ranking when that venue is eligible. Any price
given up against the best eligible venue is computed into
`price_improvement_forgone` and logged at WARNING. Treat a non-zero value as a
best-execution exception that has to be justifiable under FINRA Rule 5310(a)(1).

## 6. Recovery: cooldown, single probe, escalating backoff

```
HEALTHY ──errors≥threshold──▶ CIRCUIT_BROKEN_OUTAGE
                                      │ cooldown elapsed (refresh_venue_states)
                                      ▼
                               RECOVERY_PROBE ──success──▶ HEALTHY
                                      │ any error
                                      └──────────▶ CIRCUIT_BROKEN_OUTAGE (cooldown × multiplier)
```

Two properties matter more than the diagram:

- **A success on an open breaker does not close it.** An acknowledgement for an
  order sent *before* the outage arrives *after* the trip. Honouring it would
  resurrect a dead venue on the strength of a message that proves nothing about
  its current state. Only a success while in `RECOVERY_PROBE` closes the circuit.
- **The probe is a live order.** `refresh_venue_states()` promotes the venue and
  the next `route_order()` may select it — ranked last, but selectable. That
  order carries real outage risk. Where the venue offers a session-level
  heartbeat or a test-request round trip, drive recovery from that instead and
  call `report_venue_success()` only once it answers.

`refresh_venue_states()` is called at the top of every `route_order()`. Call it
yourself from a timer if you want state to advance in a quiet market.

## 7. Reconcile in-flight orders — the engine does not

**This is the step most likely to lose money and the engine cannot do it for
you.** When a venue drops, orders already sent to it are in an unknown state:
possibly filled, possibly resting, possibly never received. `route_order()`
returns `unrouted_quantity` for the size it could not place against the selected
venue's displayed depth, and never re-routes it automatically.

Before re-routing any residual:

1. Query the venue's drop copy, order-status, or post-outage recovery channel.
2. Reconcile fills against your own position and order records.
3. Cancel or confirm the cancellation of anything still resting.
4. Only then size the residual and route it.

Re-routing on the assumption that in-flight orders died is how one parent order
becomes two positions. See `order-placement-idempotency` and
`websocket-reconnection-with-state-recovery`.

## 8. Notify and record

The self-help construct assumes the bypass is documented and, for a trading
center, that the bypassed venue is notified "immediately after (or at the same
time as) electing self-help." `fallback_venues_used` lists every excluded venue
whose last known quote was at least as good as the fill — that is the set you
actually traded through — and `audit_notes` carries the full decision. Persist
the whole `SORRoutingResult`, not just the target venue, and feed venue outage
and rejection statistics into the FINRA Rule 5310 Supplementary Material .09
regular and rigorous review.
