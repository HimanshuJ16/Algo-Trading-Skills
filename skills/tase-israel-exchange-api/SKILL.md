---
name: tase-israel-exchange-api
description: >-
  Use when routing orders to or scheduling activity against the Tel Aviv Stock Exchange,
  which moved to a Monday-Friday trading week on 5 January 2026, with Asia/Jerusalem
  session resolution across IST and IDT.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: global-market-integration
  tags: tase, israel, venue-integration, session-calendar, agorot-conversion, price-denomination, pre-trade-risk
  brokers_frameworks: "TASE (Nasdaq Genium INET platform); QuickFIX / FIX 4.4; Python standard library (zoneinfo)"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system routes orders to, or schedules activity against,
the **Tel Aviv Stock Exchange (TASE)**. It covers the three things a TASE integration
gets wrong, all of which fail silently:

1. **The trading week.** Effective **5 January 2026**, TASE moved from Sunday-Thursday
   to **Monday-Friday**, with a short Friday session closing before Shabbat. Any system
   still carrying the old calendar breaks in both directions — it sits out Friday
   sessions and routes into a closed market on Sunday.
2. **The timezone.** Israel alternates between IST (UTC+2) and IDT (UTC+3) for roughly
   seven months a year. A fixed offset misreads the session phase by a full hour.
3. **The price denomination.** Equities quote in Agorot, bonds and Makam quote as a
   percentage of par, index derivatives quote in ILS. Confusing them is a 100x notional
   error that passes every other pre-trade check.

## When NOT to Use

- **As a FIX engine.** This module has no socket, no message serialisation and no
  sequence-number persistence. Use QuickFIX or the venue-supplied gateway library for
  the session layer, and see `fix-protocol-session-management-across-venues`.
- **As a source of authoritative session times.** The phase boundaries shipped here are
  defaults with mixed corroboration (see Prerequisites). The venue's published schedule
  and session-definition feed are authoritative; this module is the enforcement point,
  not the reference.
- **As a holiday calendar.** TASE holidays follow the Hebrew calendar and cannot be
  derived from a weekday rule. Supply them via `TASESessionSchedule(holidays=...)` from
  TASE's published schedule. See `global-exchange-holiday-calendar-handling`.
- **For pre-2026 backtests, without changing the schedule.** Use
  `TASESessionSchedule.for_date(d)` or `.legacy_sunday_thursday()`. Replaying 2025 data
  under the current Monday-Friday calendar reintroduces the same class of error in
  reverse.
- **As your only pre-trade risk layer.** These are per-order parameter checks. Aggregate
  exposure, drawdown and kill-switch controls belong out of band — see
  `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- Python 3.10+ for `zoneinfo`. On Windows and slim containers the `tzdata` package is
  also required; the engine raises `TASEConfigurationError` at construction rather than
  falling back to a fixed UTC offset.
- TASE membership or sponsored access, with `SenderCompID`, `TargetCompID` and
  `TraderID` issued by TASE Member Services, and network reachability to the gateway.
- A security master carrying, per instrument: TASE 6/7-digit security number, ISIN,
  price denomination, tick size, reference price, and **par value for every
  percentage-quoted instrument** (bonds, Makam) — a percentage price has no cash value
  without it.
- **Confirm the session boundaries before production.** Session open (09:59) and close
  (17:25 Mon-Thu, 13:50 Fri) are corroborated by MSCI's announcement of the 2026 change;
  pre-open (09:25) by market-data vendor session tables. The **closing-auction start
  times are not independently corroborated** and ship as placeholders. Replace them with
  TASE's published values.

## Workflow

1. **Pick the schedule for the period you are trading.** `TASESessionSchedule.current()`
   for live trading; `TASESessionSchedule.for_date(d)` when replaying history, which
   selects the regime actually in force on `d`. Pass TASE's published holiday dates in
   `holidays` — an empty holiday set means the engine will call a holiday a trading day.
2. **Configure `TASEConfig`.** Set the session IDs, host and port, and the risk
   thresholds (`max_order_value_ils`, `max_order_qty`, `max_price_collar_pct`). Leave
   `enforce_session_calendar` and `require_registered_security` enabled unless you have
   a specific reason: both fail closed, and disabling them removes a control rather than
   relaxing one.
3. **Register the security master** with `register_security()`. Registration rejects a
   percentage-quoted instrument with no `par_value_ils` and a non-positive tick size, so
   a malformed master fails at load time rather than at order time.
4. **Connect**, then **check the phase** with `get_market_phase()` or
   `accepts_order_entry()`. Pass a timezone-aware datetime; naive values are rejected
   because they cannot be mapped to Israel local time unambiguously.
5. **Submit orders.** `submit_order()` gates on the session phase, rejects a reused
   `client_order_id`, then runs pre-trade validation. Handle the three failure classes
   distinctly — `TASEMarketClosedError` means retry when the session opens,
   `TASEValidationError` means the order is malformed and retrying it unchanged will
   fail identically, and `TASERiskLimitError` means a control fired and the order needs
   a risk decision, not a retry.
6. **Apply execution reports** with `simulate_execution_report()`, which maintains
   cumulative fills, VWAP and status. `average_price` stays in the order's own
   denomination — it is not converted to ILS.
7. **Cancel and disconnect.** `cancel_order()` returns `False` for an order already in a
   terminal state rather than raising, so a cancel-all sweep does not abort partway
   through on the first already-filled order.

## Common Pitfalls

- **Carrying the pre-2026 Sunday-Thursday calendar.** This is the failure this skill
  exists to prevent, and it is asymmetric: skipping Friday costs you a session quietly,
  while treating Sunday as open sends orders into a closed market and produces
  rejections that look like connectivity faults. Assert both directions in a test.
- **Deriving Israel local time from a fixed UTC offset.** UTC+2 is correct only in
  winter. From the Friday before the last Sunday in March to the last Sunday in October,
  Israel is UTC+3, and a fixed offset reads the closing auction as continuous trading.
  Resolve through `Asia/Jerusalem`, and treat a missing tz database as a start-up
  failure — not a reason to fall back to a constant.
- **Valuing a market order at zero.** A market order carries no price. Substituting
  `0.0` for the missing price makes its notional zero, so the max-order-value cap never
  fires and an unbounded order passes the control unchallenged. Estimate from the
  reference price, and refuse the order when no reference price exists.
- **Reading a bond's percentage quote as shekels.** A bond at 102.5 is 102.5% of par,
  not 102.5 ILS. With 1 ILS par that is a 100x overstatement of notional — enough to
  falsely trip the value cap on a legitimate order, and to mis-scale the collar check in
  the same breath.
- **Trusting the order's own denomination field.** The order says Agorot because the
  caller set it, not because it is true. Compare against the security master: an equity
  priced 35 (meaning ILS) against a master that says Agorot is a 100x error that no
  quantity, value or collar check will catch, because all three are computed from the
  same wrong number.
- **Skipping the collar for unregistered symbols.** An unknown symbol is the case where
  a price collar matters most, not least. Silently passing an order whose reference
  price you cannot look up inverts the control.
- **Encoding iceberg as a FIX `OrdType`.** Iceberg is not a tag 40 value. It is a limit
  order (tag 40 = `2`) with the visible size in `DisplayQty` (tag 1138; `MaxFloor`, tag
  111, in FIX 4.x). Likewise tag 40 = `3` is Stop/Stop Loss — stop-limit is `4`. Read
  `OrderType.fix_ord_type` rather than `OrderType.value`.
- **Retrying an order submission after a timeout.** A lost response is not a rejection;
  the venue may already hold the order. Reuse of a `client_order_id` is rejected here
  precisely so a blind retry cannot overwrite the original's fill state. Reconcile
  against the venue before resubmitting — see `order-placement-idempotency`.

## Verification

```bash
python -m unittest discover -s skills/tase-israel-exchange-api/scripts
```

The suite asserts the calendar in both directions (Friday open, Sunday closed), walks
every phase boundary of a session, pins the DST behaviour to a boundary instant where a
fixed UTC+2 offset gives a different answer, derives VWAP and percentage-of-par notionals
independently of the implementation, and covers each fail-closed control: unpriced market
orders, unregistered symbols, denomination mismatch, tick misalignment and duplicate
client order ids.

## Related Skills

- `fix-protocol-session-management-across-venues`
- `global-exchange-holiday-calendar-handling`
- `order-placement-idempotency`
- `kill-switch-and-drawdown-circuit-breakers`
- `exchange-tick-size-regime-tracking`
- `multi-timezone-session-scheduling`
