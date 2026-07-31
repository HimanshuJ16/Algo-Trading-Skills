# Workflows for Symbology Cross-Referencing

1. **Checksum Validation**:
   - Validate ISIN (12 chars), CUSIP (9 chars), and SEDOL (7 chars) Modulo 10 checksums.
2. **Security Master Cross-Reference Mapping**:
   - Match input identifier against canonical Security Master database.
3. **Unified Record Resolution**:
   - Return full cross-referenced mapping (ISIN, CUSIP, SEDOL, FIGI, Ticker).
4. **Audit Reporting**:
   - Output structured identifier report.
