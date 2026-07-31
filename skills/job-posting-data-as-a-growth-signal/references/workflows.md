# Workflows for Job Posting Growth Signal Analysis

1. **Job Posting Data Ingestion**:
   - Ingest current vs previous active job postings, role mix percentages, and average duration.
2. **Stale Ghost Listing Haircut**:
   - Apply a 50% haircut penalty if average posting duration $> 120$ days.
3. **Role-Weighted Growth Score Calculation**:
   - Weight engineering and sales openings, computing normalized score $S_{\text{growth}} \in [-1.0, +1.0]$.
4. **Signal Classification & Reporting**:
   - Classify expansion/contraction status and output report.