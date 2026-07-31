# Workflows for Centralized Log Aggregation

1. **Log Record Ingestion & Redaction**:
   - Ingest raw log records and sanitize sensitive dictionary keys.
2. **Structured JSON Formatting**:
   - Format OpenTelemetry-compliant JSON payloads with correlation IDs.
3. **Error Velocity & Rate Limit Audit**:
   - Sample DEBUG logs and monitor error log velocity spikes.
4. **Audit Report Generation**:
   - Output structured observability report.
