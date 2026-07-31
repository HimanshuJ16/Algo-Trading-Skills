# Workflows for Hot/Cold Wallet Allocation

1. **Balance & Key Audit**:
   - Audit Hot, Cold, and Warm balances and verify API key withdrawal restriction.
2. **Ratio Evaluation**:
   - Compute current Hot capital ratio relative to target (15%).
3. **Rebalance Action Proposal**:
   - Trigger sweep to cold or refill request based on ratio thresholds.
4. **Audit Trail Logging**:
   - Output structured treasury report.