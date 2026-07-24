# Broker & Framework Coverage — order-book-depth-processing-l2-l3

| Feed Level | Protocol / Exchange | Primary Feature Outputs |
|---|---|---|
| Level 2 (Price Aggregated) | Binance Depth, Zerodha Depth, CME MDP 3.0 | Top-of-book, Weighted Midprice, Order Book Imbalance |
| Level 3 (Order-by-Order) | Nasdaq TotalView (ITCH), Coinbase L3 | Individual Order Tracking, Queue Position, Order Lifetime |

## Category

`real-time-architecture` — see the top-level `mappings/` directory for how this category rolls up
across the full skill library.

## Regulatory & Operational Notes

Intersects with high-frequency order book dynamics, market microstructure feature engineering, and thread-safe streaming algorithms.
