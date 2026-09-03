# TASE Integration Sign-Off Checklist

## Trading calendar — the 2026 regime change

- [ ] **Monday-Friday week confirmed.** The system trades **Friday** and does **not**
      trade **Sunday**. TASE changed regime on 2026-01-05; verify both directions, not
      just one — carrying the old calendar sits out Fridays *and* routes into a closed
      market on Sundays.
- [ ] **Friday early close.** Friday session boundaries differ from Mon-Thu (closes
      before Shabbat). Confirmed against TASE's published schedule, not assumed.
- [ ] **Closing-auction start times replaced.** The shipped values are placeholders and
      are **not** independently corroborated. Substitute venue-supplied values.
- [ ] **Holiday set supplied.** `TASESessionSchedule(holidays=...)` populated from TASE's
      published schedule. An empty set means every weekday is treated as a trading day.
- [ ] **Backtests use the regime in force.** Any replay crossing 2026-01-05 uses
      `TASESessionSchedule.for_date()`, not a single fixed schedule.

## Timezone

- [ ] **`Asia/Jerusalem` resolves.** `tzdata` installed on Windows / slim containers; the
      engine raises at construction rather than falling back to a fixed offset.
- [ ] **No fixed UTC offset anywhere** in scheduling, logging or bar timestamps. Israel is
      UTC+3 (IDT) from the Friday before the last Sunday in March to the last Sunday in
      October — roughly seven months a year.
- [ ] **DST transition tested.** Phase resolution asserted on both sides of a March and an
      October transition.
- [ ] **All datetimes timezone-aware.** Naive values are rejected by design; confirm no
      caller is stripping tzinfo upstream.
- [ ] **Host clock synchronised** (NTP/PTP).

## Price denomination

- [ ] **Denomination recorded per instrument** in the security master, and orders are
      validated **against the master** — never trusting the order's own field.
- [ ] **Par value populated for every percentage-quoted instrument** (bonds, Makam).
      Registration rejects those without it.
- [ ] **Equity pipeline converts ILS model prices to Agorot** before order construction.
- [ ] **Denomination-mismatch rejection tested** with a deliberately ILS-priced equity —
      the 100x error that passes every other check.
- [ ] **Tick sizes loaded and enforced**; `tick_size_agorot` positive for every instrument.

## Pre-trade risk controls

- [ ] **`max_order_qty`** configured from risk policy.
- [ ] **`max_order_value_ils`** configured, and **market-order notional tested** — verify a
      priceless market order is valued from the reference price and *rejected* when over
      the cap, not valued at zero.
- [ ] **Orders with no price and no reference price are refused**, not admitted at zero
      notional.
- [ ] **`max_price_collar_pct`** configured against a reference price that is actually
      refreshed each session.
- [ ] **`require_registered_security` left enabled**, or its removal explicitly
      risk-accepted in writing — disabling it removes the collar for unknown symbols.
- [ ] **`enforce_session_calendar` left enabled**, or explicitly risk-accepted.
- [ ] **Limits sourced from TASE/ISA documents and firm policy**, not from this skill's
      defaults, which are placeholders.
- [ ] **Aggregate controls exist out of band** — per-order caps are not exposure, drawdown
      or kill-switch controls.

## FIX encoding

- [ ] **Tag 40 read from `OrderType.fix_ord_type`**, never from `OrderType.value`.
- [ ] **Stop-limit encodes as `4`**, not `3` (which is Stop / Stop Loss).
- [ ] **Iceberg encodes as a limit order** (`2`) with `DisplayQty` (1138) / `MaxFloor`
      (111) — it is not an `OrdType` value.
- [ ] **`MarketPhase` is not serialised as `TradSesStatus`** (tag 340); the enumerations
      differ.
- [ ] **Connectivity spec obtained from TASE Member Services.** FIX version, gateway
      topology and market-data entitlements confirmed with the venue — this skill asserts
      none of them.

## Operational

- [ ] **Credentials verified**: `SenderCompID`, `TargetCompID`, `TraderID`, account.
- [ ] **Target environment confirmed** (production gateway vs UAT simulator).
- [ ] **FIX sequence numbers persist** across restarts (session layer, outside this module).
- [ ] **Client order id reuse rejected**; retry-after-timeout path reconciles with the
      venue before resubmitting rather than blind-retrying.
- [ ] **Failure classes handled distinctly** — `TASEMarketClosedError`,
      `TASEValidationError`, `TASERiskLimitError` and `TASEConnectionError` do not share
      one retry path.
- [ ] **Cancel-all sweep tested**; already-terminal orders return `False` rather than
      aborting the sweep.
- [ ] **Tests green**:
      `python -m unittest discover -s skills/tase-israel-exchange-api/scripts`
