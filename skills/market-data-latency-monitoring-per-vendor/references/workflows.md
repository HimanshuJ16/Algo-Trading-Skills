# Workflows for Vendor Latency Monitoring

1. **Microsecond Latency Sample Ingestion**:
   - Record timestamps at exchange, vendor, NIC, and application layers.
2. **Percentile & Jitter Calculation**:
   - Compute P50, P90, P95, P99, and P99.9 percentiles per vendor.
3. **Vendor SLA Threshold Audit**:
   - Compare P99 latency against vendor SLA limit.
4. **Audit Report Generation**:
   - Output structured vendor latency report.
