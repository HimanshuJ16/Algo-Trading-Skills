# Workflows for Regulatory Change Monitoring Service Integration

1. **Feed Ingestion & Regulator Filtering**:
   - Ingest regulatory update feeds; filter out unmonitored authorities.
2. **Deadline & Urgency Assessment**:
   - Calculate days until effective date; flag urgent items (CRITICAL/HIGH severity within 30 days).
3. **Action Routing**:
   - Classify update status as ACTION_REQUIRED or MONITORING.
4. **Compliance Reporting**:
   - Generate structured regulatory change report for compliance and engineering leads.