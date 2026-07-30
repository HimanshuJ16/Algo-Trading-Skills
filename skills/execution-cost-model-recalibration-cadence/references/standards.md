# Standards for Execution Cost Model Recalibration Cadence

| Metric | Engineering Standard |
|---|---|
| Max Tracking Error (RMSE) | Cost model RMSE MUST NOT exceed $3.5\text{ bps}$. |
| Max Systematic Bias | Mean prediction bias $|\bar{\epsilon}|$ MUST NOT exceed $1.5\text{ bps}$. |
| Minimum Training Sample Size | Parameter refitting MUST use at least $N \ge 50$ trade executions. |
