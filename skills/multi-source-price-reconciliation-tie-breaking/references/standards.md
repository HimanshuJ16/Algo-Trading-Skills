# Standards for Multi-Source Price Reconciliation Tie Breaking

| Metric | Engineering Standard |
|---|---|
| Outlier Deviation Threshold | Quote deviation $|P_i - M| / M > 1.0\%$ MUST trigger outlier rejection. |
| Agreement Tolerance | Vendor quote spread $\le 0.05\%$ triggers weighted average composite price. |
| Tie-Breaker Precedence | Priority rank ($\min r_i$) overrides secondary tie-breakers. |
