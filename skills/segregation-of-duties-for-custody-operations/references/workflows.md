# Workflows for Segregation of Duties for Custody Operations

1. **Role Registration & Audit**:
   - Register identities and enforce strict role separation (Initiator vs Approver vs Admin).
2. **Proposal Ingestion (Maker)**:
   - Initiator proposes custody transfer; set required approval count.
3. **Approval Verification (Checker)**:
   - Verify approver is distinct from initiator and possesses `APPROVER` role.
4. **Audit Trail Recording**:
   - Append SHA-256 signed approval record; update status when threshold is satisfied.