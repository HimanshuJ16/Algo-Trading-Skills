# Pre-Flight Checklist — multi-strategy-capital-allocation-limits

## Configuration

- [ ] All strategies registered with allocation percentages.
- [ ] Total allocations + cash reserve ≤ 100% (enforced at registration; re-verified by
      `validate_allocations()` after any config load).
- [ ] Cap changes go through `update_allocation()`; no code path re-registers a live strategy.
- [ ] Strategy code cannot widen its own cap; limit changes are authorised and logged.

## Exposure inputs

- [ ] Exposure is reported as **gross** notional (longs and shorts summed in absolute value).
- [ ] Exposure is marked to market, not cost basis.
- [ ] Fills are attributed to exactly one strategy.
- [ ] NAV is timestamped at the source, and trading halts on a stale NAV.
- [ ] All amounts are in a single base currency.

## Pre-trade path

- [ ] The live order path calls `reserve()`, not `check_order()`.
- [ ] `order_id` is the broker client order id, so retries are idempotent.
- [ ] Pre-trade check blocks orders exceeding the strategy cap (`STRATEGY_CAP`).
- [ ] Pre-trade check blocks orders exceeding the account investable ceiling (`PORTFOLIO_CAP`).
- [ ] Non-finite / non-positive NAV and order values are rejected (`INVALID_INPUT`), and the
      strategy halts rather than retrying.
- [ ] Exposure-reducing orders are still approved when a strategy is over cap.
- [ ] Blocked orders are classified by `rejection_code` before any retry; no unchanged retry loop.

## Reservation lifecycle

- [ ] Every reservation is settled or released on the order-update stream.
- [ ] Partial fills use `close=False` so the working remainder stays reserved.
- [ ] Open reservations are reconciled against the broker's open-order book on a heartbeat.
- [ ] On restart, state is rebuilt from the broker, not from local memory.
- [ ] Alarm fires on a live broker order with no matching reservation.

## Verification

- [ ] Utilization report reflects current mark-to-market exposures, in-flight capital, and
      `is_over_cap`.
- [ ] Breach fire-drill executed: cap breach, portfolio breach, and NaN NAV all block.
- [ ] Run `python scripts/test_capital_allocator.py` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
