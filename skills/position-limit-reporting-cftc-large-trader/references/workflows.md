# Workflows for Position Limit Reporting CFTC Large Trader

1. **Multi-Account Aggregation**:
   - Aggregate positions across all sub-accounts per entity and commodity code.
2. **Form 102A LTR Threshold Audit**:
   - Check if aggregated position meets or exceeds CFTC Form 102A reporting levels.
3. **Speculative Limit Audit**:
   - Audit aggregated position against CFTC Part 150 Federal speculative limits.
4. **Audit Report Generation**:
   - Output structured CFTC Large Trader report.
