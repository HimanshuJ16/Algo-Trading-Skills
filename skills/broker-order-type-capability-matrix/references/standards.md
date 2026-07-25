# Broker Integration Standards — broker-order-type-capability-matrix

| Broker | Bracket | OCO | Iceberg | TWAP / VWAP | Pegged | Fractional |
|---|---|---|---|---|---|---|
| IBKR | Native | Native | Native | Native | Native | Yes |
| Alpaca | Native | Native | Emulated | Emulated | Emulated | Yes |
| Zerodha | Emulated | Emulated | Native | Emulated | Emulated | No |
| Binance | Emulated | Native | Native | Native | Emulated | Yes |

## Standardizing Fallback Emulation

All software emulated orders MUST:
1. Provide a `primary_order_type` specifying the initial native API action (usually `LIMIT` or `MARKET`).
2. Provide a typed list of `EmulatedLeg` objects containing specific triggers, quantities, and side adjustments.
3. Be compatible with a localized Execution Management System (EMS) capable of tracking slice feeders (time-based) or trigger watchers (price-based).

## Category

`broker-integration` — see top-level `mappings/` directory.
