# Workflows for Satellite Imagery Based Signal Research

1. **Satellite Metric Ingestion**:
   - Ingest computer vision metric (parking lot vehicle count, oil tank roof shadow fill %, NDVI).
2. **Z-Score Normalization**:
   - Calculate Z-score against 52-week rolling historical mean and std dev.
3. **Directional & Confidence Scoring**:
   - Map Z-score to directional bias (-1.0 to +1.0) and calculate confidence %.
4. **Availability Lag Enforcement**:
   - Apply 2-day satellite capture & pipeline processing lag before signal execution.