# Standards for Secrets Rotation Without Bot Downtime

| Rotation Stage | Standard Requirement |
|---|---|
| Zero Downtime | Bot process MUST NOT be restarted during secret rotation. |
| Dual-Token Overlap | Previous key MUST remain valid as fallback during 5-minute validation window. |
| Automatic Rollback | Bot MUST automatically revert to previous key on HTTP 401/403 response. |
