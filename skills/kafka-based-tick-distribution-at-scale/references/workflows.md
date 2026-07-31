# Workflows for Kafka Tick Distribution

1. **Symbol Key Partition Assignment**:
   - Assign partition index via deterministic symbol key hashing.
2. **Producer Batching & Compression**:
   - Buffer ticks into 128KB batch buffers with 5ms linger.
3. **Consumer Lag Monitoring**:
   - Monitor offset delta and flag lag threshold breaches.
4. **Audit Report Generation**:
   - Output structured Kafka tick distribution report.
