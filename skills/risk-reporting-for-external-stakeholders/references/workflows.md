# Workflows for Risk Reporting for External Stakeholders

1. **Portfolio State Snapshot**:
   - Ingest NAV, gross/net exposures, 99% VaR, Sharpe, drawdown, and sector concentrations.
2. **Information Barrier & Redaction**:
   - Redact raw proprietary constituent positions from the export payload.
3. **Stakeholder Customization**:
   - Format metrics according to regulatory (Form PF/Annex IV) or LP reporting schemas.
4. **Cryptographic Signing & Delivery**:
   - Generate SHA-256 report signature and log report dispatch event.