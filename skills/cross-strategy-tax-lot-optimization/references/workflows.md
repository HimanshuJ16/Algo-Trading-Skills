# Workflows for Cross-Strategy Tax Lot Optimization

1. **Tax Lot Inventory Ingestion**:
   - Collect open lots with cost basis, acquisition date, and holding period.
2. **HIFO / Specific ID Matching**:
   - Sort lots by highest cost basis first ($\max P_{\text{cost}}$).
3. **Cross-Strategy Netting**:
   - Compute $\text{Net Quantity} = \sum Q_{\text{buy}} - \sum Q_{\text{sell}}$.
4. **Wash Sale Verification**:
   - Confirm zero purchases within 30 days of loss realization.