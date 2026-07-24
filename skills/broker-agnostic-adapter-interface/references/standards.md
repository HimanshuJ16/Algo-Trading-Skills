# Broker & Framework Coverage — broker-agnostic-adapter-interface

| Broker SDK | Original Status Code | Standard Normalized Status |
|---|---|---|
| Zerodha Kite | `"COMPLETE"`, `"REJECTED"`, `"OPEN"` | `OrderStatus.FILLED`, `REJECTED`, `PENDING` |
| Alpaca | `"filled"`, `"rejected"`, `"new"` | `OrderStatus.FILLED`, `REJECTED`, `PENDING` |
| IBKR TWS API | `"Submitted"`, `"Filled"`, `"Cancelled"` | `OrderStatus.PENDING`, `FILLED`, `CANCELLED` |

## Category

`broker-integration` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with software architecture decoupling principles, multi-venue execution routing, and financial software design standards.
