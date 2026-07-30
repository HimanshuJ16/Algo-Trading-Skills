# Standards for ESG Data Signal Research and Vendor Comparison

| Metric | Engineering Standard |
|---|---|
| Scale Normalization | ALL vendor ESG ratings MUST be normalized to a standard $[0.0, 1.0]$ range. |
| Vendor Disagreement Threshold | Disagreement dispersion $\sigma_{\text{esg}} > 0.25$ MUST trigger `HIGH_VENDOR_DISAGREEMENT`. |
| Sector Exclusion Guard | Controversial sectors MUST override positive ESG consensus ratings. |