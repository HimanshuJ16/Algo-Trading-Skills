# Deep Workflow Reference — multi-strategy-capital-allocation-limits

## Full Procedure

1. **Register strategies** with maximum allocation percentages. The budget
   (`sum(alloc) <= 1 - cash_reserve_pct`) is enforced on every registration, so an
   over-allocating call raises and leaves the roster untouched. Use `update_allocation()` — not
   a second `register_strategy()` — to change a live cap, so tracked exposure survives.
2. **Update exposures** on every fill and mark-to-market cycle, in **gross** notional
   (sum of absolute position values, marked to market). Negative or non-finite values are
   rejected rather than absorbed.
3. **Reserve capital before submitting**: `reserve(strategy, order_value_usd, nav, order_id)`
   evaluates the caps and claims the capacity atomically. `order_value_usd` is the change in
   gross notional — positive to increase exposure, negative to reduce it.
4. **Settle or release** each reservation from the order-update stream:
   - full fill → `settle_reservation(order_id)`
   - partial fill, remainder working → `settle_reservation(order_id, filled_usd, close=False)`
   - reject / cancel / expiry → `release_reservation(order_id)`
5. **Report**: `get_utilization_report(nav)` per strategy (including `is_over_cap`), and
   `get_portfolio_summary(nav)` for account-level committed capital versus the investable ceiling.

## Order lifecycle and the ambiguous-submission case

`order_id` should be the client order id sent to the broker. Re-reserving the same id returns
the existing claim instead of booking the capital twice, which is what makes the retry path
after a timed-out submission safe: the broker may already have the order, so the retry must not
consume a second slice of the cap. Reserving the same id for a *different* strategy or a
different amount raises — that is a caller bug, and guessing which value is authoritative would
be worse than failing closed. See `order-placement-idempotency`.

If a submission's outcome is unknown and cannot be resolved, leave the reservation in place and
reconcile against the broker's open-order book rather than releasing optimistically. Releasing a
reservation for an order that is in fact live is exactly the double-spend the reservation exists
to prevent.

## Recovery and reconciliation

Reservations live in process memory. On restart, rebuild state from the broker's open orders and
positions, never from a local snapshot: an order that filled during the outage must appear as
settled exposure, and one still working must appear as an in-flight reservation. Run a periodic
reconciliation that (a) releases reservations with no corresponding live order and (b) alarms on
live orders with no reservation — the latter means something bypassed the control.

## Concurrency

All public methods serialise on an internal re-entrant lock, so the object is safe to share
across strategy threads. That is not the same as making the *caller* safe:
`check_order()` decides without claiming anything, so between its approval and the actual
submission another thread can take the headroom. Only `reserve()` closes that window.

## Handling a blocked order

Classify before reacting:

| `rejection_code` | Meaning | Correct response |
|---|---|---|
| `STRATEGY_CAP` | This strategy is at its allocation | Downsize to `remaining_capacity_usd`, or skip. Do not retry unchanged. |
| `PORTFOLIO_CAP` | Account committed capital is at the investable ceiling | Stop adding exposure account-wide; this is a portfolio-level condition, not a strategy one. |
| `INVALID_INPUT` | Non-finite or non-positive NAV / order value | Data fault upstream. Halt the strategy and fix the feed — do not retry, and never fall back to a default NAV. |
| `UNKNOWN_STRATEGY` | Strategy not registered | Configuration fault. Never auto-register from the order path; that would create a cap out of thin air. |

Retrying a `STRATEGY_CAP` rejection unchanged in a tight loop is a message-rate problem as well
as a pointless one — see `order-to-trade-ratio-fee-penalty-avoidance`.

## Limitations

- **No NAV staleness detection.** NAV is passed per call, so the module cannot know how old it
  is. Timestamp NAV at the source and refuse to trade on a stale value.
- **No margin modelling.** Gross notional ignores broker offsets (portfolio margin, SPAN,
  cross-margin). These figures will not equal broker buying power.
- **No currency conversion.** All amounts must already be in one base currency; see
  `multi-currency-pnl-and-fx-conversion`.
- **Notional, not risk.** Equal capital is not equal risk; see
  `risk-parity-allocation-across-strategies`.

## Production Implementation Reference

- Code: `scripts/capital_allocator.py` (`MultiStrategyCapitalAllocator`).
- Tests: `scripts/test_capital_allocator.py`.
