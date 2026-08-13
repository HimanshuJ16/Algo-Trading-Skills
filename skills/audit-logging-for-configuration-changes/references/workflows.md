# Workflows for Configuration Audit Logging

1. **Intercept**: Any UI dashboard, CLI tool, or API endpoint that mutates a
   configuration parameter must route the request through
   `ConfigurationAuditLogger.process_change_request`. No backdoor paths
   (direct DB writes, flat-file edits) are permitted for audited parameters.
2. **Capture principal and reasoning**: The frontend must capture the user's
   ID (via SSO/JWT) and explicitly prompt for a text `justification`. Requests
   with an empty/whitespace `user_id` or a justification shorter than
   `MIN_JUSTIFICATION_LENGTH` are rejected and logged at WARNING level.
3. **Record and chain**: `process_change_request` assigns a monotonic
   `sequence_number`, a high-precision UTC `timestamp_utc`, the originating
   `environment`, and a SHA-256 `record_hash` chained to the previous record's
   hash via `prev_hash`. Both approved and rejected attempts are recorded.
4. **Emit**: Approved records are emitted at INFO as `AUDIT_LOG_ENTRY`;
   rejected attempts are emitted at WARNING as `AUDIT_LOG_REJECTED`. Both use
   canonical (sorted-key) JSON for deterministic SIEM ingestion.
5. **Forward**: The emitted log lines are scraped by a daemon (Fluentd,
   Filebeat) and forwarded to a Write-Once-Read-Many (WORM) compliant
   centralized logging server (e.g., S3 Object Lock, Splunk with data
   integrity controls).
6. **Commit**: If `is_approved` is `True`, the calling system commits the
   change to the database. If `False`, the change must NOT be applied.
7. **Examination / verification**: To verify integrity, walk records in
   sequence order, recompute each `record_hash` from its serialized fields,
   and confirm each `prev_hash` equals the prior `record_hash`. The first
   mismatch marks the boundary of tampering or a gap.
