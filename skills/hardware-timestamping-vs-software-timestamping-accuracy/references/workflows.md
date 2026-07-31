# Workflows for Hardware vs Software Timestamping Analysis

1. **Packet Timestamp Ingestion**:
   - Ingest hardware MAC timestamp, OS kernel timestamp, and user-space app timestamp.
2. **Latency Decomposition**:
   - Calculate kernel protocol stack delay and application context-switching jitter.
3. **MiFID II RTS 25 Audit**:
   - Verify hardware and software timestamps against $100\mu\text{s}$ UTC drift threshold.
4. **Audit Report Generation**:
   - Output structured timestamp compliance report.
