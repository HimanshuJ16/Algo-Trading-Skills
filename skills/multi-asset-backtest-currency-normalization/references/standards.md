# Broker & Multi-Currency Standards — multi-asset-backtest-currency-normalization

| Broker / Accounting Engine | Multi-Currency Cash Representation | FX Conversion Handling |
|---|---|---|
| Interactive Brokers (Multi-Currency) | Multi-Currency Cash Balances (`USD`, `EUR`, `HKD`) | Real-time FX rates / Automatic Auto-FX conversion |
| Backtrader Multi-Asset | Custom Data Feeds per FX Pair | Manual FX Rate multiplier on line |
| VectorBT Portfolio | Base currency reporting | FX matrix mapping |

## Category

`backtesting-methodology` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with IAS 21 (The Effects of Changes in Foreign Exchange Rates), multi-currency portfolio risk management, and global macro asset allocation standards.
