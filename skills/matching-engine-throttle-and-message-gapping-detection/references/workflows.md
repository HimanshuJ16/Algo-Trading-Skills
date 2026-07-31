# Workflows for Matching Engine Monitoring

1. **Outbound Rate Throttle Monitoring**:
   - Audit outbound message rate in 1.0s window against exchange limits.
2. **Inbound Sequence Gap Detection**:
   - Compare incoming sequence numbers against expected sequence counters.
3. **Retransmit Request Triggering**:
   - Record missing sequence ranges and request retransmits.
4. **Audit Report Generation**:
   - Output structured matching engine report.
