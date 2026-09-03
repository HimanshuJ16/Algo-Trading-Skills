---
name: smart-order-router-failover-on-venue-outage
description: >-
  Use when a router sends live orders to several venues and must keep working when one
  stops: per-venue circuit breakers with cooldown and single-probe recovery, stale-quote
  exclusion, and demotion of recovering venues.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: execution-algorithms
  tags: smart-order-router, sor-failover, venue-outage, circuit-breaker, best-execution, order-routing, self-help-exception
  brokers_frameworks: "Reg NMS Rule 611(b)(1) Self-Help; FINRA Rule 5310; SEC Rule 15c3-5; MiFID II RTS 6 Article 14; FIX Transport Circuit Breaker; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a router or execution algorithm sends live orders to more
than one venue (NASDAQ, NYSE, Cboe BATS/EDGX, IEX, or crypto equivalents) and
must keep working when one of them stops working. Exchange outages, FIX gateway
drops, and matching-engine stalls happen without warning and without the venue
withdrawing its quote first.

The failure this prevents is subtler than "the venue is down." A dead venue
usually keeps *displaying its last quote*, and that quote is often the best one
on the book — because it is stale. A naive best-price router therefore routes
**preferentially into the outage**, and keeps doing so until enough orders have
been lost to notice. This engine excludes a venue on evidence (open breaker,
stale quote, invalid quote, no liquidity), ranks what is left, and records
everything it bypassed.

`SmartOrderRouterFailoverEngine` in
`scripts/smart_order_router_failover_on_venue_outage.py` is the reference
implementation: thread-safe, monotonic-clocked, with `HEALTHY` / `DEGRADED` /
`CIRCUIT_BROKEN_OUTAGE` / `RECOVERY_PROBE` per venue.

## When NOT to Use

- **As an in-flight order recovery mechanism.** This engine does not know what
  you already sent to the failed venue and never re-routes residual quantity on
  its own. It reports `unrouted_quantity`; you must reconcile fills against the
  venue's drop copy before re-routing anything. See
  `order-placement-idempotency` and `websocket-reconnection-with-state-recovery`.
- **As your market data plane.** It stores whatever quote you last handed it. It
  cannot tell a stale quote from a fresh one unless you stamp
  `quote_monotonic_ts` — use `update_quote()`, which stamps it for you.
- **For sizing or sweeping a parent order across venues.** One call selects one
  venue. Splitting a parent across several venues at the NBBO, with maker-taker
  fee arithmetic, is `smart-order-routing-across-venues`; scheduling the parent
  over time is `execution-algo-twap-vwap-slicing`.
- **For a halted instrument.** A trading halt is not a venue outage; the venue is
  healthy and deliberately not trading. Routing around it to a venue that has not
  yet processed the halt is a different and worse error — see
  `execution-algo-behavior-under-halted-instrument`.
- **For failover between gateways or regions of the *same* venue.** Two sessions
  into one exchange is `exchange-gateway-redundancy-and-failover-testing`;
  region-level connectivity failover is
  `multi-region-failover-for-broker-connectivity`.
- **As a kill switch.** This keeps trading by moving it elsewhere. Deciding to
  *stop* trading is a separate control that must not share this code path —
  `kill-switch-and-drawdown-circuit-breakers`.
- **On a sub-millisecond hot path in CPython.** The per-route lock and linear
  scan are fine for tens of venues at human or algo-order rates, not for a
  latency-arbitrage stack.

## Prerequisites

- **A live quote feed you push into the engine**, via
  `update_quote(venue_id, bid, ask, qty)`. Quotes carry a `time.monotonic()`
  stamp; the engine's staleness check is inert without one.
- **A venue health signal**: FIX session-level rejects, gateway timeouts,
  transport disconnects, HTTP 5xx → `report_venue_error()`; fills, acks,
  heartbeats → `report_venue_success()`. Both raise `KeyError` on an unknown
  venue id rather than silently doing nothing.
- **Error classification.** Only transport/gateway faults may reach the breaker.
  An order rejected for buying power, a bad symbol, or a failed pre-trade risk
  check says nothing about venue health and must not trip it.
- **Calibrated thresholds.** The defaults (3 errors, 1 s quote age, 60 s
  cooldown) are engineering starting points, not regulatory figures — see
  `references/standards.md`.
- Python 3.9+. Standard library only.

## Workflow

1. **Register venues and keep both inputs current.** `add_venue()` rejects a
   duplicate id rather than replacing it — a silent replace would discard the
   existing venue's breaker state and re-enable a venue that is currently
   tripped. Then push quotes and health continuously.

2. **Classify the error before reporting it.**
   - **Decision point — a rejected order is not a broken venue.** Feeding
     business rejects into the breaker trips healthy venues and routes you away
     from your best liquidity because of a bug in your own order construction.

3. **Self-diagnose before declaring venues dead.**
   - **Decision point — if half your venues fail at once, suspect yourself.**
     `diagnose_suspected_local_fault()` implements the check the Reg NMS adopting
     release attaches to the Rule 611(b)(1) self-help exception: an electing
     trading center "must also assess ... whether the cause of a problem lies
     with its own systems." Simultaneous multi-venue outages are rare; a dead
     NIC, an expired credential, or clock skew breaking FIX sequencing is not.
     The flag is surfaced on every result and never stops routing by itself.

4. **Filter on evidence, and record every exclusion.** A venue is dropped only
   as `CIRCUIT_BROKEN_OUTAGE`, `INVALID_QUOTE`, `NO_LIQUIDITY`, `STALE_QUOTE`,
   `QUOTE_TIMESTAMP_MISSING`, or `QUOTE_TIMESTAMP_IN_FUTURE`, each recorded in
   `excluded_venues`.
   - **Decision point — a price of `0.0` is not a cheap venue.** It is the
     dataclass default, i.e. a venue that has never quoted or whose book was
     wiped on reconnect. Unfiltered, it wins `min(ask)` on every buy and the
     order routes at $0.00.
   - **Decision point — exclusion must never be silent.** An exclusion you
     cannot see is indistinguishable from a routing bug.

5. **Rank: probe-last, then price, then health, then latency.**
   - **Decision point — a recovering venue never wins on price.** A venue that
     just failed is the one most likely to be quoting a price it can no longer
     honour, so `RECOVERY_PROBE` venues rank below everything regardless of how
     good their quote looks.
   - **Decision point — but a `DEGRADED` venue at a better price still wins.**
     Health only breaks ties. Skipping a better price to avoid a venue with a
     single timeout is a trade-through taken for noise.
   - `preferred_venue_id` overrides ranking when eligible; the price given up is
     returned in `price_improvement_forgone` and logged at WARNING as a
     best-execution exception.

6. **Recover through a cooldown and a single probe.**
   - **Decision point — a success on an open breaker must not close it.** The ack
     for an order sent *before* the outage arrives *after* the trip; honouring it
     resurrects a dead venue on evidence that proves nothing about its current
     state. Only a success while in `RECOVERY_PROBE` closes the circuit; each
     re-trip multiplies the cooldown up to `max_cooldown_seconds`.
   - **Decision point — the probe is a real order.** Prefer a session heartbeat
     or test-request round trip where the venue offers one.

7. **Reconcile in-flight orders yourself, then handle the residual.**
   `unrouted_quantity` is reported, never auto-routed. Confirm the state of
   everything already sent to the failed venue before placing more.

8. **Persist the whole result.** `fallback_venues_used` is the set of venues you
   actually traded through; with `audit_notes` it is the record that supports a
   self-help election and the FINRA Rule 5310 .09 regular and rigorous review.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Routing preferentially into the outage.** The core trap. A dead venue keeps
  showing its last quote, staleness makes that quote look best, and a pure
  best-price router therefore *increases* the share of flow it sends to the
  broken venue. Price alone cannot detect this; only quote age and health can.
- **Treating an unquoted venue as free.** `ask_price` defaults to `0.0`. Without
  a positivity check that venue wins every buy and the router reports a fill
  price of $0.00.
- **Letting a stray success clear the breaker.** Resetting to `HEALTHY` on any
  success means one late acknowledgement from before the outage re-enables the
  dead venue, and the next order goes straight back into it.
- **Tripping every venue on a local fault.** Three timeouts against every venue
  at once is your NIC, not three simultaneous exchange outages. A router that
  concludes "all venues are down" and raises, with a live parent order
  outstanding, has converted a local cable fault into an unmanaged position.
- **Silently dropping residual quantity.** `min(quantity, available_qty)` looks
  like prudent sizing and is actually a silent partial route: the caller believes
  the whole order went out. Report the residual explicitly.
- **Re-routing residual before reconciling.** Orders in flight to a venue that
  dropped are in an unknown state. Re-sending on the assumption they died is how
  one parent order becomes two positions.
- **Defaulting an unrecognised side to SELL.** `if side == "BUY": ... else: ...`
  turns a typo'd `"SEL"` into a live short at the bid. Validate and raise.
- **Feeding business rejects into the breaker.** Insufficient buying power, a bad
  symbol, or a locked/crossed limit are your errors, not the venue's. They trip
  healthy venues and push flow to worse prices.
- **Wall-clock arithmetic for cooldowns.** An NTP step backwards during a
  recovery window either extends the cooldown indefinitely or expires it
  instantly. Use `time.monotonic()` for every elapsed-time decision.
- **Assuming Rule 611 applies to you.** The Order Protection Rule binds *trading
  centers* (17 CFR 242.600(b)(106)). A broker that only routes orders away is
  generally not one; its obligation is best execution under FINRA Rule 5310. And
  note Rule 611 is under an active SEC rescission proposal (Rel. 34-105680, June
  2026) that has **not** been adopted — see `references/standards.md`.
- **Citing a failover latency SLA.** No regulator publishes one. The one-second
  figure in the Reg NMS adopting release is a standard for judging *the away
  venue's* response time, not a deadline for your own failover code.

## Verification

- **Baseline routing**: asks NASDAQ 100.05 / BATS 100.08 / NYSE 100.10 ⟹ a BUY
  routes to NASDAQ at 100.05, `is_failover_triggered` False, `excluded_venues`
  empty. Bids BATS 100.02 / NASDAQ 100.00 / NYSE 99.95 ⟹ a SELL routes to BATS at
  100.02, so a side bug cannot pass both directional tests.
- **Invalid quotes**: a venue with the default `ask_price=0.0` and real
  `available_qty` must be excluded as `INVALID_QUOTE` and must not win the buy; a
  `NaN` price and a zero `available_qty` must likewise be excluded.
- **Staleness**: with `max_quote_age_seconds=0.5`, a quote stamped 5 s ago is
  excluded as `STALE_QUOTE` and appears in `fallback_venues_used` if it was
  quoting better. A venue with no timestamp appears in
  `stale_quote_check_skipped`, and is excluded outright under
  `require_quote_timestamp=True`. A quote stamped from the wrong clock (a
  `time.time()` value, giving a negative age) is excluded as
  `QUOTE_TIMESTAMP_IN_FUTURE` rather than read as permanently fresh.
- **Breaker**: 3 errors ⟹ `CIRCUIT_BROKEN_OUTAGE`. `report_venue_success()` while
  open must leave it open. After the cooldown, `refresh_venue_states()` ⟹
  `RECOVERY_PROBE`; a success then ⟹ `HEALTHY` with `consecutive_trips` reset; a
  single error instead ⟹ re-trip with the cooldown doubled, capped at
  `max_cooldown_seconds`.
- **Probe demotion**: a `RECOVERY_PROBE` venue showing the best ask must not be
  selected.
- **Failover audit**: tripping NASDAQ and routing with **no** `preferred_venue_id`
  must still report `is_failover_triggered` True and
  `fallback_venues_used == ["NASDAQ"]`. A dead venue that was already worse than
  the fill must appear in `excluded_venues` but **not** in
  `fallback_venues_used` — unless it was the venue the caller explicitly asked
  for, which is always recorded as a bypass.
- **Residual**: BUY 2,500 against 1,000 displayed ⟹ `routed_quantity` 1,000 and
  `unrouted_quantity` 1,500.
- **Local fault**: 2 of 3 venues tripped ⟹ `diagnose_suspected_local_fault()`
  True and `suspected_local_fault` set on the result; a single-venue engine ⟹
  always False.
- **Validation**: `side` of `"SEL"`, `""` or `"SHORT"` ⟹ `ValueError`;
  `quantity` of 0, negative, `NaN`, `inf` or `"500"` ⟹ `ValueError`; an unknown
  venue id on `report_venue_error`, `report_venue_success` or
  `preferred_venue_id` ⟹ `KeyError`; a duplicate `add_venue` ⟹ `ValueError`.
- **Exhaustion**: all venues tripped ⟹ `NoEligibleVenueError` (a `RuntimeError`
  subclass) carrying the per-venue reason map and the local-fault flag.
- Run `python -m unittest discover -s skills/smart-order-router-failover-on-venue-outage/scripts`
  and confirm 43/43 pass.

## Related Skills

- `smart-order-routing-across-venues`
- `execution-algorithm-kill-switch-integration`
- `exchange-gateway-redundancy-and-failover-testing`
- `multi-region-failover-for-broker-connectivity`
- `circuit-breaker-for-downstream-service-calls`
- `order-placement-idempotency`
- `execution-algo-behavior-under-halted-instrument`
- `fix-protocol-session-management-across-venues`
