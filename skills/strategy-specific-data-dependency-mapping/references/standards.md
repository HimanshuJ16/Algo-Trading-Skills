# Standards for Strategy-Specific Data Dependency Mapping

| Criticality Tier | Max SLA Lag Limit | Action on Primary Vendor Failure |
|---|---|---|
| Critical | $\le 60.0\text{s}$ | Pivot to Secondary; Hard-block strategy if both fail. |
| High | $\le 300.0\text{s}$ | Pivot to Secondary; Degrade readiness score. |
| Medium | $\le 900.0\text{s}$ | Impute missing values from historical cache. |