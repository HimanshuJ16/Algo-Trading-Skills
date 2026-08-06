# Workflows for Swiss FINMA Algorithmic Trading Expectations

1. **System Metadata Ingestion**:
   - Collect algorithm ID, version, owner, kill switch status, and rate limits.
2. **FinfraG 5-Control Audit**:
   - Evaluate against mandatory baseline controls 1 through 5.
3. **Compliance Decision & Scoring**:
   - Compute compliance score percentage; output blocked status if any control fails.
4. **Regulatory Reporting**:
   - Log compliance records in immutable audit repository for FINMA inspection.
