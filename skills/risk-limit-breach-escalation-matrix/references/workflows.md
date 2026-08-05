# Workflows for Risk Limit Breach Escalation Matrix

1. **Breach Event Ingestion**:
   - Ingest metric breach payload and calculate ratio to limit.
2. **Policy Matching & Duration Escalation**:
   - Match ratio to severity tier; escalate action if breach duration > 300s.
3. **Automated Action Execution**:
   - Trigger WARN, REDUCE, HALT, or FLATTEN action.
4. **Notification Routing & Audit Trail**:
   - Dispatch alerts via PagerDuty/Slack and log decision in immutable audit trail.
