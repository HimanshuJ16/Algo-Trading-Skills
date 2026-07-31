# Workflows for Universe Change Detection

1. **Snapshot Ingestion**:
   - Ingest universe snapshots $U_{t-1}$ and $U_t$ keyed by permanent FIGI/ISIN ID.
2. **Delta Cross-Matching Analysis**:
   - Classify additions, deletions, ticker renames, and status halts.
3. **Actionable Alert Dispatch**:
   - Emit downstream trading bot action alerts.
4. **Audit Reporting**:
   - Output structured universe change report.
