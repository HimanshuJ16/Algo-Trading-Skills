# Workflows for Earnings Call Transcript NLP Signal Research

1. **Transcript Section Segmentation**:
   - Split transcript text into Prepared Remarks and Q&A Session.
2. **Loughran-McDonald Tokenization**:
   - Count positive, negative, and uncertainty terms.
3. **Divergence & Signal Computation**:
   - Compute section sentiment scores and Q&A tone divergence ($\Delta_{\text{tone}}$).
4. **Trading Signal Emission**:
   - Emit `BULLISH_EARNINGS_TONE` or `BEARISH_QA_DIVERGENCE`.