# Standards for Earnings Call Transcript NLP Signal Research

| Metric | Engineering Standard |
|---|---|
| Financial Lexicon Rule | Financial transcript sentiment MUST use the Loughran-McDonald (LM) dictionary. |
| Tone Divergence Threshold | Q&A tone drop $\Delta_{\text{tone}} < -0.15$ MUST be flagged as a bearish divergence signal. |
| Section Segmentation | Transcripts MUST be split into Prepared Remarks vs Q&A prior to scoring. |