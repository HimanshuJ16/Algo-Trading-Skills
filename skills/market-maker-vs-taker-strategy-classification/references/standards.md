# Standards for Maker vs Taker Classification

| Metric | Engineering Standard |
|---|---|
| Pure Maker Threshold | $R_{\text{maker}} \ge 0.80$ MUST be classified as PURE_MAKER_STRATEGY. |
| Pure Taker Threshold | $R_{\text{maker}} \le 0.20$ MUST be classified as PURE_TAKER_STRATEGY. |
| Fee Metric Standard | Effective fee rate MUST be calculated in basis points (bps) against gross notional. |
