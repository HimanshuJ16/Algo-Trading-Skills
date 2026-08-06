# Workflows for Strategy Latency Budget Decomposition

1. **Timestamp Capture**:
   - Capture microsecond hardware/software timestamps at hot-path stage boundaries.
2. **Decomposition Analysis**:
   - Segment pipeline into Ingress, Decode, Signal, Risk, and Egress.
3. **SLA Comparison**:
   - Compare measured stage latencies against microsecond SLA budgets.
4. **Bottleneck Reporting**:
   - Isolate bottleneck stage and compute P99 jitter statistics.
