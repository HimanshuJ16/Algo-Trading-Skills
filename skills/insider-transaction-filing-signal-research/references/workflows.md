# Workflows for Insider Filing Signal Research

1. **Form 4 Ingestion & Filtering**:
   - Ingest SEC Form 4 filings and filter out 10b5-1 pre-arranged trades.
2. **Executive Role Weighting**:
   - Apply role weights ($w_{\text{CEO}}=1.0$, $w_{\text{Director}}=0.6$).
3. **Net Sentiment Calculation**:
   - Compute normalized net insider sentiment score $S_{\text{insider}} \in [-1.0, +1.0]$.
4. **Signal Classification**:
   - Classify bullish, bearish, or routine sentiment and output report.