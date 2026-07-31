# Standards for Model A/B Testing

| Metric | Engineering Standard |
|---|---|
| Statistical Test | Welch's Two-Sample t-test MUST be used (does NOT assume equal variance). |
| Significance Level | Challenger promotion MUST require $p < 0.05$ and positive mean return delta. |
| Minimum Sample Size | A minimum of $N \ge 30$ trade samples per model MUST be collected before decisioning. |