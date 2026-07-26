# Workflows for Configuration Drift Detection

1. **Config Loading**:
   - Ingest `golden_baseline` dict and `target_config` dict.
2. **Override Whitelist Definition**:
   - Set `allowed_overrides = ['env_name', 'api_url', 'log_level', 'port']`.
3. **Recursive Comparison**:
   - For key $K$ in `golden_baseline`:
     - If $K \notin \text{target}$: Record `CRITICAL` (Missing Key).
     - Else if $V_{gold} \ne V_{target}$:
       - If $K \in \text{allowed\_overrides}$: Record `ALLOWED`.
       - Else: Record `CRITICAL` (Value Mismatch).
4. **Audit Decision**:
   - If `critical_count > 0`: Return `is_compliant = False` and block startup.
   - Else: Return `is_compliant = True`.