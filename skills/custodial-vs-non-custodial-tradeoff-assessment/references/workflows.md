# Workflows for Custodial vs Non-Custodial Trade-Off Assessment

1. **Requirements Audit**:
   - Collect latency SLA, volume, gas sensitivity, and counterparty tolerance.
2. **Multi-Factor Scoring**:
   - Evaluate `CUSTODIAL_CEX`, `HYBRID_OFF_EXCHANGE`, and `NON_CUSTODIAL_DEX`.
3. **Composite Ranking**:
   - Score $= 0.40 \cdot \text{LatencyScore} + 0.35 \cdot \text{SecurityScore} + 0.25 \cdot \text{CostScore}$.
4. **Architecture Selection**:
   - Output primary recommendation and risk mitigations.