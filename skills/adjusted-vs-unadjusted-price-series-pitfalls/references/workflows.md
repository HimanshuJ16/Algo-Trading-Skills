# Workflows for adjusted-vs-unadjusted-price-series-pitfalls

## Institutional Data Ingestion Pipeline

1. **Load Raw Unadjusted Series**: Fetch exact historical tape data (OHLCV) from the data vendor.
2. **Discontinuity Scan**: Pass data through `PriceAdjustmentAuditor` to find $\ge 30\%$ overnight jumps.
3. **Volume Consistency Audit**: For any detected split, verify that trading volume scaled inversely to the price drop (maintaining historical liquidity depth/notional value).
4. **Apply Adjustments**:
   - For **Splits**: Apply the backward adjustment to both price and volume across the entire historical series prior to the split date.
   - For **Dividends**: Do *not* alter historical prices. Record the dividend as a discrete cash-inflow event to the portfolio to prevent Look-Ahead Bias.
5. **Universe Validation**: Run `validate_universe_consistency` to ensure no mixed data formats exist in the final backtest universe.
