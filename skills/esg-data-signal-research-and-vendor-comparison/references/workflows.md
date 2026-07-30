# Workflows for ESG Data Signal Research and Vendor Comparison

1. **Vendor Rating Ingestion & Normalization**:
   - Convert MSCI, Sustainalytics, and Refinitiv ratings to $[0.0, 1.0]$ scales.
2. **Consensus & Dispersion Calculation**:
   - Compute mean consensus ESG score and standard deviation dispersion.
3. **Disagreement & Exclusion Audit**:
   - Flag high vendor disagreement ($\sigma_{\text{esg}} > 0.25$) and sector exclusions.
4. **Signal Generation**:
   - Emit `BULLISH_ESG_LEADER` or `BEARISH_ESG_LAGGARD`.