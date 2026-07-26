# Standards for Configuration Drift Detection

| Metric | Engineering Standard |
|---|---|
| Pre-Trade Pre-requisite | Configuration drift audit MUST execute prior to opening trading socket connections. |
| Zero Tolerance on Risk Parameters | Key parameters (`max_position_size`, `stop_loss_pct`, `kill_switch_enabled`) MUST NEVER be whitelisted in `allowed_overrides`. |
| Strict Type Equivalence | Values with mismatched data types (`"10"` vs `10`) MUST be flagged as drift errors. |