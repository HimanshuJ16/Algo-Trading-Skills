# Workflows for Tax Lot Accounting Methods

1. **Tax Lot Ingestion**:
   - Ingest open tax lot inventory per symbol with purchase prices and holding periods.
2. **Strategy Sorting**:
   - Sort lots based on matching algorithm (FIFO, LIFO, HIFO, SPECIFIC_LOT).
3. **Lot Liquidation & Realization**:
   - Deplete target sale quantity from ordered tax lots and compute realized PnL.
4. **Tax Class Accounting**:
   - Categorize gains and losses into short-term (STCG) vs long-term (LTCG).