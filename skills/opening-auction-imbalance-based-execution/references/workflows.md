# Workflows for Opening Auction Imbalance-Based Execution

1. **Imbalance Ingestion & Ratio Calculation**:
   - Compute Imbalance Ratio = ImbalanceQty / (PairedQty + ImbalanceQty).
2. **Cutoff Check**:
   - Verify seconds_to_open >= cutoff_seconds_to_open (e.g. 120s for 09:28 AM EST).
3. **Contra-Side Order Generation**:
   - Generate BUY or SELL MOO/LOO orders providing contra-side liquidity against the imbalance.
4. **Audit Report Generation**:
   - Output structured auction execution report.