# Standards for Cold Start Handling

| Metric | Engineering Standard |
|---|---|
| Shrinkage Formula | Linear or Empirical Bayes shrinkage MUST be used to blend short-term sample metrics with sector priors. |
| Zero NaN Policy | Feature extraction pipelines MUST NEVER output `NaN` or `Inf` for probationary instruments. |
| Position Scaling | Maximum position size during probation MUST be monotonically non-decreasing as $N_{obs}$ increases. |
