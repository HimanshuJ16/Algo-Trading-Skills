# Workflows for Sequence Number Gap Detection for Feeds

1. **Sequence Ingestion**:
   - Track expected sequence ID per ticker / channel.
2. **Gap Identification & Buffering**:
   - Buffer future out-of-order frames when a sequence gap occurs ($S_{\text{incoming}} > S_{\text{expected}}$).
3. **Retransmission Recovery**:
   - Request missing range from TCP retransmission server (e.g. SoupBinTCP).
4. **State Reconciliation**:
   - Drain buffered frames once gap is filled; return to SYNCED state.
