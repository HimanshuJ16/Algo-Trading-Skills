# Broker & Framework Coverage — broker-agnostic-adapter-interface

| Broker SDK / API | Original Status Code | Standard Normalized Status |
|---|---|---|
| Zerodha Kite Connect | `"COMPLETE"`, `"REJECTED"`, `"OPEN"`, `"CANCELLED"` | `OrderStatus.FILLED`, `REJECTED`, `PENDING`, `CANCELLED` |
| Alpaca Trading API | `"filled"`, `"rejected"`, `"new"`, `"canceled"`, `"expired"` | `OrderStatus.FILLED`, `REJECTED`, `PENDING`, `CANCELLED`, `EXPIRED` |
| IBKR TWS API | `"Submitted"`, `"Filled"`, `"Cancelled"`, `"Inactive"` | `OrderStatus.PENDING`, `FILLED`, `CANCELLED`, `REJECTED` |
| Upstox API | `"complete"`, `"rejected"`, `"open"`, `"cancelled"` | `OrderStatus.FILLED`, `REJECTED`, `PENDING`, `CANCELLED` |

## Precision Standard
All quantitative financial logic *must* use `decimal.Decimal` rather than standard floating-point numbers. Standard float precision loss is unacceptable in production environments when rounding to tick sizes or summing large volume orders.

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up across the full skill library.

## Regulatory & Operational Notes

Intersects with software architecture decoupling principles, multi-venue execution routing, latency minimization, and financial software design standards. Use unified logging and robust exception typing (e.g. `BrokerAdapterError`) to guarantee operational resilience.
