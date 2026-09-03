# Broker & Framework Coverage — broker-agnostic-adapter-interface

## How to read the status tables

These are each broker's **documented** status enumerations, not the subset a given
implementation happens to map. That distinction is the point: a map covering only the
four obvious statuses will meet the others in production, and what it does then decides
whether a dead order looks alive.

Broker status sets change. Re-check against the linked documentation before trusting a
mapping, and let `OrderStatus.UNKNOWN` tell you when a new one appears.

## 1. Zerodha Kite Connect

Documented lifecycle statuses. An order passes through several interim states — often too
fast to observe — before reaching a final one.

| Kite status | Normalized | Terminal? |
|---|---|---|
| `COMPLETE` | `FILLED` | yes |
| `REJECTED` | `REJECTED` | yes |
| `CANCELLED` | `CANCELLED` | yes |
| `LAPSED` | `EXPIRED` | yes — order expired without executing |
| `OPEN` | `PENDING` | no |
| `OPEN PENDING`, `VALIDATION PENDING`, `PUT ORDER REQ RECEIVED` | `PENDING` | no |
| `MODIFIED`, `MODIFY PENDING`, `MODIFY VALIDATION PENDING` | `PENDING` | no |
| `CANCEL PENDING` | `PENDING` | no |
| `TRIGGER PENDING` | `PENDING` | no — stop-loss order awaiting its trigger |
| `AMO REQ RECEIVED` | `PENDING` | no — after-market order accepted |

Sources: [Kite Connect v3 — Orders](https://kite.trade/docs/connect/v3/orders/),
[Kite Connect forum — list of order status](https://kite.trade/forum/discussion/516/list-of-order-status).

> `LAPSED` is the trap: it is terminal, it is not obvious, and a map missing it will
> report an expired order as working.

## 2. Alpaca Trading API

Alpaca's statuses mirror FIX order states.

| Alpaca status | Normalized | Notes |
|---|---|---|
| `filled` | `FILLED` | terminal, no further updates |
| `partially_filled` | `PARTIALLY_FILLED` | |
| `canceled` | `CANCELLED` | user request or time-in-force expiry |
| `expired` | `EXPIRED` | terminal |
| `rejected` | `REJECTED` | terminal |
| `done_for_day` | `EXPIRED` | finished executing for the session; no further updates today |
| `replaced` | `CANCELLED` | superseded by another order or a corporate action |
| `new` | `PENDING` | usual initial state — received and routed |
| `pending_new`, `accepted` | `PENDING` | rare interim states |
| `pending_cancel`, `pending_replace` | `PENDING` | awaiting cancel/replace |
| `suspended` | `PENDING` | not currently eligible for trading |
| `stopped`, `calculated`, `accepted_for_bidding` | `PENDING` | rare |

Source: [Alpaca — Placing Orders / order lifecycle](https://docs.alpaca.markets/us/docs/orders-at-alpaca).

> `done_for_day` and `replaced` both mean "stop waiting for this order", and both are
> easy to omit.

## 3. Interactive Brokers TWS API

`orderStatus` returns mixed-case strings — normalize case before lookup.

| IBKR status | Normalized | Meaning |
|---|---|---|
| `Filled` | `FILLED` | |
| `Cancelled` | `CANCELLED` | |
| `ApiCancelled` | `CANCELLED` | cancelled via an API client before acknowledgement |
| `Inactive` | `REJECTED` | order is not working; commonly rejected or invalid |
| `Submitted` | `PENDING` | accepted at the destination and working |
| `PreSubmitted` | `PENDING` | held by IB until a trigger condition; uncommonly seen |
| `PendingSubmit` | `PENDING` | sent from TWS, destination has not confirmed receipt |
| `PendingCancel` | `PENDING` | cancel request sent, not yet confirmed |

Sources: [TWS API — Placing Orders](https://interactivebrokers.github.io/tws-api/order_submission.html),
[TWS API — Order status](https://interactivebrokers.github.io/tws-api/orders.html).

> `Inactive` is genuinely ambiguous at IBKR — it covers rejection *and* orders halted by a
> system condition. Mapping it to `REJECTED` is the conservative reading (treat the order
> as not working); confirm against the accompanying error text before acting.

## 4. Upstox

Upstox's order statuses (`complete`, `rejected`, `open`, `cancelled`, and interim states)
follow a similar pattern to Kite's. **The full enumeration has not been verified against
Upstox's current API documentation for this skill** — no adapter is shipped for it, and
the mapping should be built from their live docs rather than assumed from the Zerodha
table.

## Precision Standard

All monetary and quantity values use `decimal.Decimal`. `float` is **rejected** at the
adapter boundary rather than coerced: binary floating point cannot represent ordinary
decimal prices and tick sizes exactly, and a float that passes validation propagates into
`OrderResult` and surfaces only later as a `TypeError` against a `Decimal`. `int` is
accepted and widened, since it is exact.

`Decimal("NaN")` and `Decimal("Infinity")` are rejected explicitly — comparing them raises
`decimal.InvalidOperation`, which would otherwise escape the adapter boundary as a raw
stdlib exception.

## Operational Notes

- Every adapter must wrap SDK and transport exceptions into `BrokerAdapterError`
  subclasses (`NetworkError`, `AuthenticationError`, `OrderExecutionError`) so no
  broker-specific type escapes the boundary.
- `cancel_order` returning True means the *request* was accepted, not that the order is
  cancelled. Confirm via `get_order_status`.
- The factory registry is process-wide class state and starts empty by design; simulated
  adapters must be opted into explicitly.
