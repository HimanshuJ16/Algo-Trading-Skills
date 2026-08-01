# Workflows for Reference Data Golden Source Designation

1. **Multi-Vendor Data Ingestion**:
   - Collect field values from all vendors for each instrument.
2. **Golden Source Priority Resolution**:
   - Select value from highest-priority vendor with non-null data per field.
3. **Conflict Detection & Logging**:
   - Flag fields with vendor disagreements; log which vendor was selected.
4. **Golden Record Output**:
   - Output structured reconciled golden record report.
