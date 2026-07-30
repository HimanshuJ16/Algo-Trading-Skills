# Workflows for Crypto Transaction Tax Lot Tracking

1. **Lot Acquisition Ingestion**:
   - Record asset, quantity, timestamp, and USD cost basis.
2. **Swap / Disposal Processing**:
   - Determine USD Fair Market Value (FMV) of received asset or cash proceeds.
   - Net out gas fees ($\text{Proceeds}_{\text{net}} = \text{Proceeds}_{\text{gross}} - \text{Gas Fee}_{\text{usd}}$).
3. **Tax Lot Matching (HIFO/FIFO)**:
   - Match disposed quantity against ranked open tax lots.
   - Realized PnL = $\text{Proceeds}_{\text{net}} - \text{Cost Basis}$.
4. **Form 8949 Reporting**:
   - Categorize short-term ($\le 365$ days) vs long-term ($> 365$ days) realized gains/losses.